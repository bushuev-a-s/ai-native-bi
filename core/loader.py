"""Загрузка сырых CSV: автоопределение кодировки, разделителя, дат. Всё внутри платформы."""
import codecs
import csv
import io
import re
import logging
import warnings

import pandas as pd

log = logging.getLogger(__name__)

DATE_RE = re.compile(
    r"^\s*\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?\s*$"
    r"|^\s*\d{1,2}[./]\d{1,2}[./]\d{2,4}(\s+\d{1,2}:\d{2}(:\d{2})?)?\s*$"
)


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _sniff_sep(text: str) -> str:
    sample = text[:20000]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        first = sample.split("\n", 1)[0]
        return max(",;\t|", key=first.count)


def clean_names(columns) -> list[str]:
    out, seen = [], {}
    for c in columns:
        n = re.sub(r"[^\w]+", "_", str(c).strip().lower(), flags=re.UNICODE).strip("_") or "col"
        if n[0].isdigit():
            n = "c_" + n
        n = n[:60]
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n}_{seen[n]}")
    return out


DEC_COMMA_RE = re.compile(r"^[+-]?\d+(,\d+)?$")


THOUSANDS_RE = re.compile(r"^[+-]?[1-9]\d{0,2},\d{3}$")


def _try_decimal_comma(s: pd.Series) -> pd.Series:
    """Колонка вида 117,2 (строки с десятичной запятой) -> float. Остальное не трогаем."""
    nn = s.dropna().astype(str)
    if nn.empty or not nn.str.contains(",", regex=False).any():
        return s
    if nn.map(lambda v: bool(DEC_COMMA_RE.match(v))).mean() < 0.98:
        return s
    # '1,234' может быть и 1.234, и 1234 (запятая как разделитель тысяч). Не гадаем: оставляем текстом.
    with_comma = nn[nn.str.contains(",", regex=False)]
    if with_comma.map(lambda v: bool(THOUSANDS_RE.match(v))).all():
        return s
    return pd.to_numeric(s.astype("object").map(lambda v: v.replace(",", ".") if isinstance(v, str) else v),
                         errors="coerce")


def _try_dates(s: pd.Series) -> pd.Series:
    nn = s.dropna().astype(str)
    if nn.empty:
        return s
    probe = nn.head(200)
    if probe.map(lambda v: bool(DATE_RE.match(v))).mean() < 0.9:
        return s
    dayfirst = probe.str.contains(r"\d\.\d").mean() > 0.5
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        conv = pd.to_datetime(s, errors="coerce", dayfirst=dayfirst, utc=True)
    if conv.notna().sum() >= 0.95 * s.notna().sum():
        return conv.dt.tz_localize(None)
    return s


def read_csv_smart(source) -> pd.DataFrame:
    """source: путь, bytes или файловый объект (st.file_uploader)."""
    if isinstance(source, str):
        with open(source, "rb") as f:
            raw = f.read()
    elif isinstance(source, bytes):
        raw = source
    else:
        raw = source.read()
    text = _decode(raw)
    sep = _sniff_sep(text)
    df = pd.read_csv(io.StringIO(text), sep=sep, low_memory=False)
    return _clean_chunk(df)


def table_name_from_file(filename: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", filename)
    return clean_names([stem])[0]


# ---------------------------------------------------------------- большие файлы: потоковая загрузка через COPY
# Существующие функции выше не менялись. Это отдельная функция для файлов, которые не стоит читать целиком.

def _clean_chunk(df: pd.DataFrame) -> pd.DataFrame:
    """Та же очистка колонок, что в read_csv_smart, но для одного чанка."""
    df.columns = clean_names(df.columns)
    for col in df.columns:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            df[col] = df[col].map(lambda v: v.strip() if isinstance(v, str) else v)
            df[col] = df[col].replace("", pd.NA)
            df[col] = _try_decimal_comma(df[col])
            if not pd.api.types.is_numeric_dtype(df[col]):
                df[col] = _try_dates(df[col])
    return df


def _pg_type(dtype) -> str:
    if pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(dtype):
        return "BIGINT"
    if pd.api.types.is_float_dtype(dtype):
        return "DOUBLE PRECISION"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "TIMESTAMP"
    return "TEXT"


# Порядок «расширения» типа колонки, если в поздних чанках встретились значения, не влезающие в тип первого чанка.
_WIDEN = {"BIGINT": "DOUBLE PRECISION", "DOUBLE PRECISION": "TEXT", "TIMESTAMP": "TEXT", "BOOLEAN": "TEXT"}


def _to_text(s: pd.Series) -> pd.Series:
    return s.astype("object").where(s.isna(), s.astype(str))


def _to_num(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return s
    return pd.to_numeric(s.astype("object").map(lambda v: v.replace(",", ".") if isinstance(v, str) else v),
                         errors="coerce")


def _to_ts(s: pd.Series) -> pd.Series | None:
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    if pd.api.types.is_numeric_dtype(s) and s.notna().any():
        return None                        # числа не превращаем в даты (иначе получится 1970 год)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ts = pd.to_datetime(s, errors="coerce", format="ISO8601", utc=True)   # сначала ISO: 2024-03-05 = 5 марта
        rest = ts.isna() & s.notna()
        if rest.any():                                                        # потом дд.мм.гггг
            ts[rest] = pd.to_datetime(s[rest], errors="coerce", dayfirst=True, utc=True)
    return ts.dt.tz_localize(None)


def _try_convert(s: pd.Series, pg: str) -> pd.Series | None:
    """Приводит колонку к типу pg. Возвращает None, если пришлось бы потерять или исказить значения."""
    if pg == "TEXT":
        return _to_text(s)
    if pg in ("BIGINT", "DOUBLE PRECISION"):
        num = _to_num(s)
        if (num.isna() & s.notna()).any():
            return None
        if pg == "DOUBLE PRECISION":
            return num
        vals = num.dropna()
        if len(vals) and (vals % 1 != 0).any():
            return None                    # 1.5 в целочисленной колонке: не округляем молча
        return num.astype("Int64")
    if pg == "TIMESTAMP":
        ts = _to_ts(s)
        if ts is None or (ts.isna() & s.notna()).any():
            return None
        return ts
    if pg == "BOOLEAN":
        return s if (pd.api.types.is_bool_dtype(s) or s.isna().all()) else None
    return _to_text(s)


def _coerce_to_pg(df: pd.DataFrame, pgtypes: dict) -> tuple[pd.DataFrame, dict]:
    """Приводит чанк к типам таблицы. Если значения не влезают, тип колонки расширяется
    (BIGINT -> DOUBLE PRECISION -> TEXT), а не обнуляет данные. Возвращает чанк и {колонка: новый тип}."""
    alters = {}
    for col in list(pgtypes):
        s = df[col]
        pg = pgtypes[col]
        while (conv := _try_convert(s, pg)) is None:
            pg = _WIDEN.get(pg, "TEXT")
            alters[col] = pg
            pgtypes[col] = pg
        df[col] = conv
    return df, alters


def _detect_encoding(data) -> str:
    """Проверяет кодировку по ВСЕМУ файлу (блоками), а не только по первому мегабайту."""
    def blocks():
        if isinstance(data, str):
            with open(data, "rb") as f:
                while b := f.read(1 << 20):
                    yield b
        else:
            data.seek(0)
            while b := data.read(1 << 20):
                yield b

    for enc in ("utf-8-sig", "cp1251"):
        dec = codecs.getincrementaldecoder(enc)()
        try:
            for b in blocks():
                dec.decode(b)
            dec.decode(b"", final=True)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def load_csv_to_postgres(source, engine, table: str, chunksize: int = 200_000) -> int:
    """Грузит большой CSV в Postgres через COPY кусками. Требует psycopg2.

    source: путь, bytes или файловый объект. Таблица пересоздаётся.
    Типы берутся из первого чанка; если в поздних чанках встречаются значения другого типа, колонка расширяется
    (ALTER TYPE), данные не теряются. Всё в одной транзакции: при ошибке таблицы не останется.
    Возвращает число строк.
    """
    if isinstance(source, str):
        data = source
        with open(source, "rb") as f:
            head = f.read(1 << 20)
    else:
        raw = source if isinstance(source, bytes) else source.read()
        data, head = io.BytesIO(raw), raw[: 1 << 20]
    enc = _detect_encoding(data)
    sep = _sniff_sep(head.decode(enc, errors="ignore"))
    if hasattr(data, "seek"):
        data.seek(0)

    q = lambda n: '"' + n.replace('"', '""') + '"'  # noqa: E731
    conn = engine.raw_connection()
    total, pgtypes = 0, None
    try:
        cur = conn.cursor()
        for chunk in pd.read_csv(data, sep=sep, encoding=enc, low_memory=False, chunksize=chunksize):
            chunk = _clean_chunk(chunk)
            if pgtypes is None:
                pgtypes = {c: _pg_type(t) for c, t in chunk.dtypes.items()}
                cur.execute(f"DROP TABLE IF EXISTS {q(table)}")
                cols = ", ".join(f"{q(c)} {t}" for c, t in pgtypes.items())
                cur.execute(f"CREATE TABLE {q(table)} ({cols})")
            else:
                chunk, alters = _coerce_to_pg(chunk, pgtypes)
                for col, new in alters.items():
                    log.warning("Колонка %s.%s расширена до %s: в данных встретились значения другого типа",
                                table, col, new)
                    cur.execute(f"ALTER TABLE {q(table)} ALTER COLUMN {q(col)} TYPE {new} USING {q(col)}::{new}")
            buf = io.StringIO()
            chunk.to_csv(buf, index=False, header=False, na_rep="", date_format="%Y-%m-%d %H:%M:%S.%f")
            buf.seek(0)
            cur.copy_expert(f"COPY {q(table)} FROM STDIN WITH (FORMAT csv, NULL '')", buf)
            total += len(chunk)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return total