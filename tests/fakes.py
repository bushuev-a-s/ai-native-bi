"""Заглушки для тестов без Postgres и Ollama: SQLite с тем же интерфейсом и скриптовая LLM."""
import json
import sqlite3

import pandas as pd


class SqliteDB:
    dialect = "sqlite"

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.dashboards = {}

    # служебные запросы для core/history.py (аналог Database.meta_*)
    def meta_execute(self, sql, params=None):
        self.conn.execute(sql, params or {})
        self.conn.commit()

    def meta_insert(self, sql, params=None):
        cur = self.conn.execute(sql, params or {})
        self.conn.commit()
        return cur.lastrowid

    def meta_fetch(self, sql, params=None):
        cur = self.conn.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def tables(self):
        r = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return [x[0] for x in r]

    def columns(self, table):
        return [(r[1], r[2]) for r in self.conn.execute(f'PRAGMA table_info("{table}")')]

    def row_count(self, table):
        return self.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    def load_dataframe(self, df, table):
        df.to_sql(table, self.conn, if_exists="replace", index=False)

    def query(self, sql, max_rows=None):
        if max_rows:
            sql = f"SELECT * FROM (\n{sql}\n) AS _q LIMIT {int(max_rows)}"
        df = pd.read_sql_query(sql, self.conn)
        for c in df.columns:      # SQLite хранит даты текстом — как делает «сырой» CSV
            pass
        return df

    def save_dashboard(self, title, spec):
        i = max(self.dashboards, default=0) + 1       # id не переиспользуются после удаления
        self.dashboards[i] = json.dumps(spec, ensure_ascii=False)
        return i

    def list_dashboards(self):
        return [{"id": i, "title": json.loads(s)["title"]} for i, s in self.dashboards.items()]

    def delete_dashboard(self, i):
        self.dashboards.pop(i, None)

    def get_dashboard(self, i):
        return json.loads(self.dashboards[i]) if i in self.dashboards else None


class ScriptedLLM:
    """Возвращает заранее заданные ответы по очереди; запоминает вызовы."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, temperature=0.0, json_mode=False):
        self.calls.append(messages)
        return self.replies.pop(0)
