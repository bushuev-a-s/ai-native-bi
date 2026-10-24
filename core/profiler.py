"""Профилирование таблиц и поиск связей между ними (по пересечению значений идентификаторов).
Профиль — это «память о данных»: он подаётся в LLM и показывается пользователю."""
import datetime
import math
import re
from decimal import Decimal

import numpy as np
import pandas as pd

from . import config
from .loader import _try_dates

ID_NAME_RE = re.compile(r"(^|_)(id|uuid|guid|key|код)($|_)|id$", re.I)


def _py(v):
    if v is None:
        return None
    if isinstance(v, (pd.Timestamp, datetime.datetime, datetime.date)):
        return v.isoformat()[:19]
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if math.isnan(v) else float(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (int, str, bool)):
        return v
    return str(v)


def _normalize(s: pd.Series) -> pd.Series:
    """Приводит типы из БД к аналитическим: date -> datetime, Decimal -> float, даты в тексте -> datetime."""
    if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
        return s
    nn = s.dropna()
    if nn.empty:
        return s
    first = nn.iloc[0]
    try:
        if isinstance(first, (datetime.date, datetime.datetime)):
            return pd.to_datetime(s)
        if isinstance(first, Decimal):
            return s.astype(float)
        if isinstance(first, str):
            return _try_dates(s)
    except Exception:  # noqa: BLE001
        pass
    return s


def _kind(name: str, s: pd.Series, nun: int, n: int) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    ratio = nun / n if n else 0
    name_hint = bool(ID_NAME_RE.search(name))
    if pd.api.types.is_numeric_dtype(s):
        is_float = pd.api.types.is_float_dtype(s)
        if (name_hint and nun > 1 and ratio > 0.01) or (not is_float and ratio > 0.95 and n > 20):
            return "id"
        return "numeric"
    if (name_hint and ratio > 0.01) or (ratio > 0.95 and n > 20 and s.astype(str).str.len().mean() < 40):
        return "id"
    if nun <= max(50, 0.05 * n):
        return "categorical"
    return "text"


def profile_column(name: str, db_type: str, s: pd.Series) -> dict:
    s = _normalize(s)
    nn = s.dropna()
    n = len(s)
    nun = int(nn.nunique())
    kind = _kind(name, nn, nun, len(nn)) if len(nn) else "empty"
    info = {
        "name": name, "db_type": db_type, "kind": kind,
        "null_pct": round(100 * (1 - len(nn) / n), 1) if n else 0.0,
        "n_unique": nun,
    }
    if kind in ("numeric", "id") and pd.api.types.is_numeric_dtype(nn):
        info.update(min=_py(nn.min()), max=_py(nn.max()), mean=_py(round(float(nn.mean()), 3)))
    if kind == "datetime":
        info.update(min=_py(nn.min()), max=_py(nn.max()))
    if kind in ("categorical", "boolean"):
        vc = nn.value_counts().head(5)
        info["top_values"] = [[str(k), int(v)] for k, v in vc.items()]
    if kind in ("id", "text"):
        info["examples"] = [x[:40] for x in nn.drop_duplicates().head(3).astype(str)]
    return info


def profile_table(db, table: str) -> dict:
    total = db.row_count(table)
    df = db.query(f'SELECT * FROM "{table}"', max_rows=config.PROFILE_SAMPLE_ROWS)
    types = dict(db.columns(table))
    cols = [profile_column(c, types.get(c, ""), df[c]) for c in df.columns]
    time_cols = [c for c in cols if c["kind"] == "datetime"]
    main_time = max(time_cols, key=lambda c: c["n_unique"]) if time_cols else None
    return {
        "name": table,
        "rows": total,
        "n_cols": len(cols),
        "sampled": total > len(df),
        "duplicate_rows": int(df.duplicated().sum()),
        "time_col": main_time["name"] if main_time else None,
        "period_from": main_time["min"] if main_time else None,
        "period_to": main_time["max"] if main_time else None,
        "columns": cols,
    }


GENERIC_KEY_NAMES = {"id", "index", "idx", "n", "no", "num", "number", "rowid", "row_id"}


def _stem(table: str) -> str:
    """users -> user, payments -> payment, categories -> category."""
    t = table.lower()
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith("sses"):
        return t[:-2]
    if t.endswith("s") and not t.endswith("ss") and len(t) > 2:
        return t[:-1]
    return t


def _name_link(ta: str, ca: str, tb: str, cb: str) -> bool:
    """Есть ли у пары колонок смысловая подсказка в названиях.
    Одних совпадающих значений мало: любые два ряда 1..N совпадают на 100%, но это не связь."""
    a, b = ca.lower(), cb.lower()
    if a == b:
        return a not in GENERIC_KEY_NAMES                         # user_id = user_id, но не id = id
    return b in (f"{_stem(ta)}_id", f"{_stem(ta)}id") or a in (f"{_stem(tb)}_id", f"{_stem(tb)}id")


def find_relations(db, profiles: list[dict], min_overlap: float = 0.3) -> list[dict]:
    """Связи между таблицами: ключевые колонки, у которых совпадают И названия-подсказки, И значения."""
    sets: dict[tuple[str, str], set] = {}
    for p in profiles:
        for c in p["columns"]:
            if c["kind"] == "id" or (ID_NAME_RE.search(c["name"]) and c["kind"] in ("categorical", "numeric", "text")):
                try:
                    d = db.query(f'SELECT DISTINCT "{c["name"]}" FROM "{p["name"]}" '
                                 f'WHERE "{c["name"]}" IS NOT NULL',
                                 max_rows=config.RELATION_SAMPLE_VALUES)
                    sets[(p["name"], c["name"])] = set(d.iloc[:, 0].astype(str))
                except Exception:  # noqa: BLE001
                    continue
    rels, keys = [], list(sets)
    for i, (ta, ca) in enumerate(keys):
        for tb, cb in keys[i + 1:]:
            if ta == tb or not _name_link(ta, ca, tb, cb):
                continue
            a, b = sets[(ta, ca)], sets[(tb, cb)]
            inter = len(a & b)
            score = inter / min(len(a), len(b)) if a and b else 0
            if inter >= 3 and score >= min_overlap:
                rels.append({"a": ta, "a_col": ca, "b": tb, "b_col": cb, "overlap": round(score, 2)})
    return sorted(rels, key=lambda r: -r["overlap"])


def profile_all(db) -> tuple[list[dict], list[dict]]:
    profiles = [profile_table(db, t) for t in db.tables()]
    return profiles, find_relations(db, profiles)


# --- текстовые представления ---------------------------------------------------------

KIND_RU = {"id": "идентификатор", "numeric": "число", "datetime": "дата/время",
           "categorical": "категория", "text": "текст", "boolean": "да/нет", "empty": "пусто"}


def schema_text(profiles: list[dict], relations: list[dict]) -> str:
    out = []
    for p in profiles:
        period = f", период {p['period_from']} .. {p['period_to']} по \"{p['time_col']}\"" if p["time_col"] else ""
        out.append(f'Таблица "{p["name"]}" — {p["rows"]} строк{period}')
        for c in p["columns"][:60]:
            line = f'  "{c["name"]}" {c["db_type"]} [{KIND_RU.get(c["kind"], c["kind"])}] ' \
                   f'уникальных={c["n_unique"]} пропусков={c["null_pct"]}%'
            if "top_values" in c:
                line += " топ: " + ", ".join(f"{v}({n})" for v, n in c["top_values"])
            elif "examples" in c:
                line += " примеры: " + ", ".join(c["examples"])
            elif "min" in c:
                line += f' мин={c["min"]} макс={c["max"]}' + (f' среднее={c["mean"]}' if "mean" in c else "")
            out.append(line)
    if relations:
        out.append("Связи (по совпадению значений):")
        for r in relations:
            out.append(f'  "{r["a"]}"."{r["a_col"]}" = "{r["b"]}"."{r["b_col"]}" (пересечение {int(r["overlap"] * 100)}%)')
    return "\n".join(out)


def facts_markdown(profiles: list[dict], relations: list[dict]) -> str:
    """Точные факты считаются кодом, а не LLM — чтобы модель не выдумывала цифры."""
    lines = ["| Таблица | Строк | Колонок | Период | Дубликатов строк |", "|---|---|---|---|---|"]
    for p in profiles:
        period = f"{p['period_from'][:10]} — {p['period_to'][:10]} (`{p['time_col']}`)" if p["time_col"] else "—"
        dup = f"{p['duplicate_rows']}" + (" (в выборке)" if p["sampled"] else "")
        lines.append(f"| {p['name']} | {p['rows']:,} | {p['n_cols']} | {period} | {dup} |".replace(",", " "))
    if relations:
        lines += ["", "**Связи между таблицами:**"]
        lines += [f"- `{r['a']}.{r['a_col']}` ↔ `{r['b']}.{r['b_col']}` (совпадение {int(r['overlap'] * 100)}%)"
                  for r in relations]
    return "\n".join(lines)
