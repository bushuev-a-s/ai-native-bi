"""Загрузка сырых CSV: автоопределение кодировки, разделителя, дат. Всё внутри платформы."""
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
            df[col] = _try_dates(df[col])
    return df


def table_name_from_file(filename: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", filename)
    return clean_names([stem])[0]
