"""Защита: пропускаем только один безопасный SELECT."""
import re


class UnsafeSQL(ValueError):
    pass


FORBIDDEN = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate", "grant", "revoke",
    "copy", "call", "do", "execute", "set", "reset", "vacuum", "analyze", "merge", "into",
    "listen", "notify", "lock", "comment", "refresh", "reindex", "cluster", "prepare",
}
FORBIDDEN_RE = re.compile(r"\b(" + "|".join(sorted(FORBIDDEN)) + r")\b", re.I)
DANGEROUS_FUNCS_RE = re.compile(r"\b(pg_\w+|lo_\w+|dblink\w*|current_setting|set_config)\b", re.I)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def _mask_literals(sql: str) -> str:
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return re.sub(r'"(?:[^"]|"")*"', '""', sql)


def validate_sql(sql: str) -> str:
    """Возвращает очищенный SQL или бросает UnsafeSQL."""
    if not sql or not sql.strip():
        raise UnsafeSQL("Пустой запрос")
    cleaned = _strip_comments(sql).strip().rstrip(";").strip()
    masked = _mask_literals(cleaned)
    if ";" in masked:
        raise UnsafeSQL("Разрешён только один SQL-запрос")
    if not re.match(r"^(select|with)\b", masked, re.I):
        raise UnsafeSQL("Разрешены только запросы SELECT")
    m = FORBIDDEN_RE.search(masked)
    if m:
        raise UnsafeSQL(f"Запрещённое ключевое слово: {m.group(1).upper()}")
    if DANGEROUS_FUNCS_RE.search(masked):
        raise UnsafeSQL("Запрещённая системная функция")
    if "_bi_" in cleaned.lower() or "information_schema" in masked.lower():
        raise UnsafeSQL("Доступ к служебным таблицам запрещён")
    return cleaned


def limit_sql(sql: str, max_rows: int) -> str:
    return f"SELECT * FROM (\n{sql}\n) AS _q LIMIT {int(max_rows)}"
