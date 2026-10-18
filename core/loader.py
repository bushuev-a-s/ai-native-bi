"""Загрузка сырых CSV: автоопределение кодировки, разделителя, дат. Всё внутри платформы."""
import codecs
import csv
import io
import re
import warnings

import pandas as pd

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


def _try_decimal_comma(s: pd.Series) -> pd.Series:
    """Колонка вида 117,2 (строки с десятичной запятой) -> float. Остальное не трогаем."""
    nn = s.dropna().astype(str)
    if nn.empty or not nn.str.contains(",", regex=False).any():
        return s
    if nn.map(lambda v: bool(DEC_COMMA_RE.match(v))).mean() < 0.98:
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
    df.columns = clean_names(df.columns)
    for col in df.columns:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            df[col] = df[col].map(lambda v: v.strip() if isinstance(v, str) else v)
            df[col] = df[col].replace("", pd.NA)
            df[col] = _try_decimal_comma(df[col])
            if not pd.api.types.is_numeric_dtype(df[col]):
                df[col] = _try_dates(df[col])
    return df


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


def _coerce_like(df: pd.DataFrame, dtypes: dict) -> pd.DataFrame:
    """Приводим чанк к типам первого чанка, чтобы COPY не падал из-за «поплывшего» типа."""
    for col, dt in dtypes.items():
        s = df[col]
        if s.dtype == dt:
            continue
        if pd.api.types.is_bool_dtype(dt):
            continue
        if pd.api.types.is_integer_dtype(dt):
            df[col] = pd.to_numeric(s, errors="coerce").round().astype("Int64")
        elif pd.api.types.is_float_dtype(dt):
            if not pd.api.types.is_numeric_dtype(s):
                s = s.astype("object").map(lambda v: v.replace(",", ".") if isinstance(v, str) else v)
            df[col] = pd.to_numeric(s, errors="coerce")
        elif pd.api.types.is_datetime64_any_dtype(dt):
            if not pd.api.types.is_datetime64_any_dtype(s):
                df[col] = pd.to_datetime(s, errors="coerce", dayfirst=True)
        else:
            df[col] = s.astype("object").where(s.isna(), s.astype(str))
    return df


def load_csv_to_postgres(source, engine, table: str, chunksize: int = 200_000) -> int:
    """Грузит большой CSV в Postgres через COPY кусками. Требует psycopg2.

    source: путь, bytes или файловый объект. Таблица пересоздаётся.
    Всё в одной транзакции: при ошибке на середине таблицы не останется. Возвращает число строк.
    """
    if isinstance(source, str):
        data = source
        with open(source, "rb") as f:
            head = f.read(1 << 20)
    else:
        raw = source if isinstance(source, bytes) else source.read()
        data, head = io.BytesIO(raw), raw[: 1 << 20]
    enc = "latin-1"
    for e in ("utf-8-sig", "cp1251"):
        try:
            codecs.getincrementaldecoder(e)().decode(head, final=False)  # обрезанный на границе символ не страшен
            enc = e
            break
        except UnicodeDecodeError:
            continue
    sep = _sniff_sep(head.decode(enc, errors="ignore"))
    if hasattr(data, "seek"):
        data.seek(0)

    q = lambda n: '"' + n.replace('"', '""') + '"'  # noqa: E731
    conn = engine.raw_connection()
    total, dtypes = 0, None
    try:
        cur = conn.cursor()
        for chunk in pd.read_csv(data, sep=sep, encoding=enc, low_memory=False, chunksize=chunksize):
            chunk = _clean_chunk(chunk)
            if dtypes is None:
                dtypes = dict(chunk.dtypes)
                cur.execute(f"DROP TABLE IF EXISTS {q(table)}")
                cols = ", ".join(f"{q(c)} {_pg_type(t)}" for c, t in dtypes.items())
                cur.execute(f"CREATE TABLE {q(table)} ({cols})")
            else:
                chunk = _coerce_like(chunk, dtypes)
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
