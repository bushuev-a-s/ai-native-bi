"""История диалогов с агентом: чаты и сообщения хранятся в БД и переживают перезапуск.

Таблицы (служебные, префикс `_bi_` — агенту и SQL-валидатору недоступны):
  _bi_chats    (id, title, created_at, updated_at)
  _bi_messages (id, chat_id, role, kind, text, sql, chart, dashboard_id, created_at)

Таблицу результата не храним: храним SQL и пересчитываем его при открытии чата
(`hydrate`), поэтому данные в старых чатах всегда актуальны и БД не раздувается.

Модуль работает через три метода объекта БД: meta_execute, meta_insert, meta_fetch
(Database для PostgreSQL, SqliteDB в тестах), параметры — именованные (:name).
"""
from __future__ import annotations

import json
from datetime import datetime

DEFAULT_TITLE = "Новый чат"
TITLE_MAX = 60


def make_title(question: str) -> str:
    """Название чата по первому вопросу: одна строка, не длиннее TITLE_MAX."""
    t = " ".join((question or "").split())
    if not t:
        return DEFAULT_TITLE
    return t if len(t) <= TITLE_MAX else t[: TITLE_MAX - 1].rstrip() + "…"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ChatHistory:
    def __init__(self, db):
        self.db = db
        self._init_tables()

    # ------------------------------------------------------------------ схема
    def _init_tables(self):
        pk = "SERIAL PRIMARY KEY" if getattr(self.db, "dialect", "postgresql") == "postgresql" \
            else "INTEGER PRIMARY KEY AUTOINCREMENT"
        self.db.meta_execute(
            f"CREATE TABLE IF NOT EXISTS _bi_chats (id {pk}, title TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        self.db.meta_execute(
            f"CREATE TABLE IF NOT EXISTS _bi_messages (id {pk}, chat_id INTEGER NOT NULL, "
            "role TEXT NOT NULL, kind TEXT, text TEXT NOT NULL, sql TEXT, chart TEXT, "
            "dashboard_id INTEGER, created_at TEXT NOT NULL)")
        self.db.meta_execute(
            "CREATE INDEX IF NOT EXISTS _bi_messages_chat_idx ON _bi_messages (chat_id, id)")

    # ------------------------------------------------------------------ чаты
    def create_chat(self, title: str = DEFAULT_TITLE) -> int:
        now = _now()
        return self.db.meta_insert(
            "INSERT INTO _bi_chats (title, created_at, updated_at) VALUES (:t, :c, :u)",
            {"t": title, "c": now, "u": now})

    def list_chats(self) -> list[dict]:
        """Чаты от свежих к старым: [{id, title, created_at, updated_at, messages}]."""
        return self.db.meta_fetch(
            "SELECT c.id, c.title, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM _bi_messages m WHERE m.chat_id = c.id) AS messages "
            "FROM _bi_chats c ORDER BY c.updated_at DESC, c.id DESC")

    def get_chat(self, chat_id: int) -> dict | None:
        rows = self.db.meta_fetch(
            "SELECT id, title, created_at, updated_at FROM _bi_chats WHERE id = :i", {"i": chat_id})
        return rows[0] if rows else None

    def rename_chat(self, chat_id: int, title: str) -> None:
        self.db.meta_execute("UPDATE _bi_chats SET title = :t WHERE id = :i",
                             {"t": make_title(title), "i": chat_id})

    def delete_chat(self, chat_id: int) -> None:
        self.db.meta_execute("DELETE FROM _bi_messages WHERE chat_id = :i", {"i": chat_id})
        self.db.meta_execute("DELETE FROM _bi_chats WHERE id = :i", {"i": chat_id})

    # ------------------------------------------------------------------ сообщения
    def add_message(self, chat_id: int, role: str, text: str, *, kind: str | None = None,
                    sql: str | None = None, chart: dict | None = None,
                    dashboard_id: int | None = None) -> int:
        """Сохраняет реплику. Название чата берётся из первого вопроса пользователя."""
        now = _now()
        mid = self.db.meta_insert(
            "INSERT INTO _bi_messages (chat_id, role, kind, text, sql, chart, dashboard_id, created_at) "
            "VALUES (:chat, :role, :kind, :text, :sql, :chart, :dash, :now)",
            {"chat": chat_id, "role": role, "kind": kind, "text": text or "", "sql": sql,
             "chart": json.dumps(chart, ensure_ascii=False) if chart else None,
             "dash": dashboard_id, "now": now})
        self.db.meta_execute("UPDATE _bi_chats SET updated_at = :u WHERE id = :i",
                             {"u": now, "i": chat_id})
        if role == "user":
            chat = self.get_chat(chat_id)
            if chat and chat["title"] == DEFAULT_TITLE:
                self.db.meta_execute("UPDATE _bi_chats SET title = :t WHERE id = :i",
                                     {"t": make_title(text), "i": chat_id})
        return mid

    def get_messages(self, chat_id: int) -> list[dict]:
        rows = self.db.meta_fetch(
            "SELECT id, role, kind, text, sql, chart, dashboard_id, created_at "
            "FROM _bi_messages WHERE chat_id = :i ORDER BY id", {"i": chat_id})
        for r in rows:
            r["chart"] = json.loads(r["chart"]) if r["chart"] else None
        return rows


# ---------------------------------------------------------------------- восстановление
def hydrate(db, rows: list[dict]) -> list[dict]:
    """Превращает сохранённые строки в сообщения для интерфейса.

    Таблица результата пересчитывается по сохранённому SQL, дашборд берётся по id.
    Если пересчитать не удалось (например, таблицу удалили) — сообщение остаётся,
    но с пометкой `stale`, а не падает.
    """
    from .agent import run_sql          # локальный импорт: history не зависит от agent при импорте

    out = []
    for r in rows:
        m = {"role": r["role"], "text": r["text"], "kind": r.get("kind"), "sql": r.get("sql"),
             "chart": r.get("chart"), "df": None, "dashboard": None, "stale": False}
        if r.get("sql"):
            try:
                _, m["df"] = run_sql(db, r["sql"])
            except Exception:  # noqa: BLE001
                m["stale"], m["chart"] = True, None
        if r.get("dashboard_id"):
            m["dashboard"] = db.get_dashboard(r["dashboard_id"])
            m["stale"] = m["stale"] or m["dashboard"] is None
        out.append(m)
    return out


def restore_agent_context(agent, messages: list[dict]) -> None:
    """Возвращает агенту «последний результат», чтобы работало «покажи то же самое иначе»."""
    from .agent import Result

    agent.last = None
    for m in reversed(messages):
        if m["role"] == "assistant" and m.get("df") is not None and m.get("sql"):
            agent.last = Result(m.get("kind") or "answer", m["text"], sql=m["sql"],
                                df=m["df"], chart=m.get("chart"))
            return
