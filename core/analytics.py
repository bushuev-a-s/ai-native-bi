"""Аналитические инструменты для сценариев 3+. Считает КОД, модель только пересказывает результат.

Контракт, чтобы работать параллельно с агентом:
  - каждая функция принимает (db, table, **параметры) и возвращает AnalysisResult;
  - summary — факты простым языком, уже посчитанные кодом (числа берутся отсюда, а не придумываются моделью);
  - df — таблица результата; chart — спецификация графика как в core/charts.py (или None);
  - новый инструмент добавляется в TOOLS с описанием — по нему агент будет выбирать инструмент.
Подключение к агенту (новое намерение "analyze" в core/agent.py) — отдельная задача."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import config


@dataclass
class AnalysisResult:
    title: str
    summary: str
    df: pd.DataFrame
    chart: dict | None = None


def load_table(db, table: str, max_rows: int | None = None) -> pd.DataFrame:
    """Читает таблицу (или её часть) в pandas для анализа."""
    return db.query(f'SELECT * FROM "{table}"', max_rows=max_rows or config.PROFILE_SAMPLE_ROWS)


def find_duplicates(db, table: str, subset: list[str] | None = None) -> AnalysisResult:
    """Полные дубликаты строк (или по набору колонок subset)."""
    df = load_table(db, table)
    cols = subset or list(df.columns)
    extra = int(df.duplicated(subset=cols, keep="first").sum())
    pct = round(100 * extra / len(df), 2) if len(df) else 0.0
    if extra == 0:
        return AnalysisResult("Дубликаты", f"В таблице «{table}» дубликатов не найдено.", pd.DataFrame())
    groups = (df[df.duplicated(subset=cols, keep=False)]
              .groupby(cols, dropna=False).size().reset_index(name="Повторов")
              .sort_values("Повторов", ascending=False).head(50))
    summary = (f"В таблице «{table}» найдено {extra} лишних повторяющихся строк ({pct}% от {len(df)} "
               f"проверенных). Показаны самые частые повторы.")
    return AnalysisResult("Дубликаты", summary, groups)


# TODO (Рамазан): find_anomalies, seasonality, correlations, lags
# TODO (Данил, позже): segments, forecast

TOOLS = {
    "find_duplicates": {
        "fn": find_duplicates,
        "description": "Найти повторяющиеся строки в таблице (параметры: table, subset — список колонок или все).",
    },
}
