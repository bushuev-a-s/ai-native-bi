"""Работа с PostgreSQL. Данные грузятся «как есть», запросы агента идут только на чтение."""
import json

import pandas as pd
from sqlalchemy import create_engine, inspect, text

from . import config
from .loader import load_csv_to_postgres

META_PREFIX = "_bi_"


class Database:
    dialect = "postgresql"

    def __init__(self, url: str | None = None):
        self.engine = create_engine(url or config.DATABASE_URL, pool_pre_ping=True)
        self._init_meta()

    # --- служебное -------------------------------------------------------
    def _init_meta(self):
        with self.engine.begin() as c:
            c.execute(text(
                "CREATE TABLE IF NOT EXISTS _bi_dashboards ("
                "id SERIAL PRIMARY KEY, title TEXT NOT NULL, spec TEXT NOT NULL, "
                "created_at TIMESTAMP DEFAULT now())"
            ))

    def ping(self) -> bool:
        try:
            with self.engine.connect() as c:
                c.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False

    # --- служебные запросы (история чатов и т.п.; агент сюда не попадает) ----------
    def meta_execute(self, sql: str, params: dict | None = None) -> None:
        with self.engine.begin() as c:
            c.execute(text(sql), params or {})

    def meta_insert(self, sql: str, params: dict | None = None) -> int:
        """INSERT ... и возврат id новой строки."""
        with self.engine.begin() as c:
            return int(c.execute(text(sql + " RETURNING id"), params or {}).scalar())

    def meta_fetch(self, sql: str, params: dict | None = None) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(text(sql), params or {})]

    # --- схема -------------------------------------------------------------
    def tables(self) -> list[str]:
        names = inspect(self.engine).get_table_names()
        return sorted(n for n in names if not n.startswith(META_PREFIX))

    def columns(self, table: str) -> list[tuple[str, str]]:
        return [(c["name"], str(c["type"])) for c in inspect(self.engine).get_columns(table)]

    def row_count(self, table: str) -> int:
        with self.engine.connect() as c:
            return int(c.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar())

    # --- загрузка ------------------------------------------------------------
    def load_dataframe(self, df: pd.DataFrame, table: str) -> None:
        chunk = max(1, min(5000, 30000 // max(1, len(df.columns))))
        df.to_sql(table, self.engine, if_exists="replace", index=False,
                  chunksize=chunk, method="multi")
    def load_csv(self, source, table: str, chunksize: int = 200_000, sep: str | None = None) -> int:
        """Быстрая потоковая загрузка CSV через COPY (для больших файлов).

        source: путь, bytes или файловый объект. Таблица пересоздаётся, вся загрузка в одной
        транзакции. Возвращает число загруженных строк.
        """
        return load_csv_to_postgres(source, self.engine, table, chunksize, sep)

    def drop_table(self, table: str) -> None:
        """Удаляет загруженную таблицу. Служебные `_bi_*` и несуществующие имена отклоняются."""
        if table not in self.tables():
            raise ValueError(f"Таблицы {table!r} нет среди загруженных данных")
        with self.engine.begin() as c:
            c.execute(text('DROP TABLE "' + table.replace('"', '""') + '"'))

    # --- запросы (только чтение) -----------------------------------------------
    def query(self, sql: str, max_rows: int | None = None) -> pd.DataFrame:
        from .safety import limit_sql
        if max_rows:
            sql = limit_sql(sql, max_rows)
        with self.engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text(f"SET LOCAL statement_timeout = {int(config.QUERY_TIMEOUT_MS)}"))
                return pd.read_sql(text(sql), conn)

    # --- дашборды ------------------------------------------------------------------
    def save_dashboard(self, title: str, spec: dict) -> int:
        with self.engine.begin() as c:
            return int(c.execute(
                text("INSERT INTO _bi_dashboards (title, spec) VALUES (:t, :s) RETURNING id"),
                {"t": title, "s": json.dumps(spec, ensure_ascii=False)},
            ).scalar())

    def list_dashboards(self) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(text(
                "SELECT id, title, created_at FROM _bi_dashboards ORDER BY id DESC")).fetchall()
        return [{"id": r[0], "title": r[1], "created_at": str(r[2])[:16]} for r in rows]

    def delete_dashboard(self, dash_id: int) -> None:
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM _bi_dashboards WHERE id=:i"), {"i": dash_id})

    def get_dashboard(self, dash_id: int) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(text("SELECT spec FROM _bi_dashboards WHERE id=:i"),
                            {"i": dash_id}).fetchone()
        return json.loads(row[0]) if row else None
