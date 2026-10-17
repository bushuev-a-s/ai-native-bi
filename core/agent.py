"""Агент: маршрутизация -> text-to-SQL с самоисправлением -> ответ простым языком -> график/дашборд."""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from . import config, prompts
from .charts import ALLOWED, auto_spec, validate_spec
from .llm import LLMError, extract_json, extract_sql
from .profiler import facts_markdown, schema_text
from .safety import UnsafeSQL, limit_sql, validate_sql

OVERVIEW_RE = re.compile(
    r"что (передо мной|есть в (этих )?данн|за данные|в этих данных)|опиши (эти )?данные|"
    r"расскажи (о|про) (этих )?данн|обзор данных|за какой период.*(данн|они)",
    re.I,
)
INTENTS = {"overview", "explain", "query", "rechart", "dashboard", "clarify", "reject"}


@dataclass
class Result:
    kind: str                       # overview|answer|chart|dashboard|clarify|reject|error
    text: str
    sql: str | None = None
    df: pd.DataFrame | None = None
    chart: dict | None = None
    dashboard: dict | None = None
    dashboard_id: int | None = None


class NeedClarification(Exception):
    pass


def run_sql(db, sql: str) -> tuple[str, pd.DataFrame]:
    clean = validate_sql(sql)
    return clean, db.query(limit_sql(clean, config.MAX_ROWS))


def df_preview(df: pd.DataFrame, n: int = 30) -> str:
    return df.head(n).to_string(index=False, max_colwidth=40)


def history_text(history: list[dict], n: int | None = None) -> str:
    n = n or config.HISTORY_TURNS
    lines = []
    for m in history[-n:]:
        who = "Пользователь" if m["role"] == "user" else "Ассистент"
        lines.append(f"{who}: {m['content'][:500]}")
    return "\n".join(lines) or "(диалог только начался)"


def execute_dashboard(db, spec: dict) -> list[dict]:
    """Перезапускает SQL каждого виджета — дашборд всегда показывает свежие данные."""
    out = []
    for w in spec.get("widgets", []):
        try:
            _, df = run_sql(db, w["sql"])
            out.append({"title": w["title"], "df": df, "chart": w.get("chart"), "error": None})
        except Exception as e:  # noqa: BLE001
            out.append({"title": w["title"], "df": None, "chart": None, "error": str(e)[:200]})
    return out


class Agent:
    def __init__(self, db, llm, profiles: list[dict], relations: list[dict]):
        self.db, self.llm = db, llm
        self.profiles, self.relations = profiles, relations
        self.schema = schema_text(profiles, relations)
        self.last: Result | None = None      # контекст сессии: последний результат

    # ------------------------------------------------------------------ вход
    def ask(self, question: str, history: list[dict] | None = None) -> Result:
        history = history or []
        try:
            return self._ask(question.strip(), history)
        except LLMError as e:
            return Result("error", f"Не удаётся связаться с локальной моделью. {e}")
        except Exception as e:  # noqa: BLE001
            return Result("error", f"Не получилось обработать запрос ({str(e)[:150]}). "
                                   "Попробуйте переформулировать вопрос.")

    def _ask(self, q: str, history: list[dict]) -> Result:
        if not self.profiles:
            return Result("clarify", "Пока нет данных. Загрузите CSV-файлы в боковой панели "
                                     "или подключите существующие таблицы.")
        if not q:
            return Result("clarify", "Напишите вопрос, например: «Что есть в этих данных?»")
        route = self._route(q, history)
        intent = route["intent"]
        if intent == "overview":
            return self._overview()
        if intent == "explain":
            return self._explain(q, history)
        if intent == "rechart" and self.last is not None and self.last.df is not None:
            return self._rechart(q, route)
        if intent == "dashboard":
            return self._dashboard(q)
        if intent == "reject":
            return Result("reject", route.get("message") or
                          "Такой запрос я выполнить не могу. Я помогаю анализировать загруженные данные — "
                          "спросите, например, о составе данных, трендах или распределениях.")
        if intent == "clarify":
            return Result("clarify", route.get("message") or "Уточните, пожалуйста, что именно показать?")
        return self._query(q, history, route)

    # ------------------------------------------------------------- маршрутизация
    def _route(self, q: str, history: list[dict]) -> dict:
        if OVERVIEW_RE.search(q):
            return {"intent": "overview"}
        msgs = [
            {"role": "system", "content": prompts.ROUTER},
            {"role": "user", "content": f"Предыдущий результат есть: {self.last is not None and self.last.df is not None}\n"
                                        f"История:\n{history_text(history)}\n\nСообщение: {q}"},
        ]
        data = extract_json(self.llm.chat(msgs, json_mode=True))
        if data.get("intent") not in INTENTS:
            data["intent"] = "query"
        return data

    # ------------------------------------------------------------------ сценарий 1
    def _overview(self) -> Result:
        facts = facts_markdown(self.profiles, self.relations)
        try:
            narrative = self.llm.chat([{"role": "user", "content": prompts.OVERVIEW.format(schema=self.schema)}],
                                      temperature=0.2).strip()
        except LLMError:
            narrative = "_Локальная модель недоступна, показываю только сводку фактов._"
        return Result("overview", f"{narrative}\n\n**Сводка по данным**\n\n{facts}")

    # ------------------------------------------------------------------ сценарий 2
    def _explain(self, q: str, history: list[dict]) -> Result:
        txt = self.llm.chat([{"role": "user", "content": prompts.EXPLAIN.format(
            schema=self.schema, history=history_text(history), question=q)}], temperature=0.1)
        return Result("answer", txt.strip())

    def _sql_messages(self, q: str, history: list[dict]) -> list[dict]:
        msgs = [{"role": "system", "content": prompts.SQL_SYSTEM.format(
            schema=self.schema, max_rows=config.MAX_ROWS)}]
        for m in history[-config.HISTORY_TURNS:]:
            msgs.append({"role": m["role"], "content": m["content"][:800]})
        msgs.append({"role": "user", "content": q})
        return msgs

    def _sql_with_repair(self, msgs: list[dict]) -> tuple[str, pd.DataFrame]:
        last_err = "неизвестная ошибка"
        for _ in range(config.SQL_RETRIES + 1):
            raw = self.llm.chat(msgs)
            if raw.strip().upper().startswith("CLARIFY:"):
                raise NeedClarification(raw.split(":", 1)[1].strip())
            sql = extract_sql(raw)
            try:
                return run_sql(self.db, sql)
            except (UnsafeSQL, Exception) as e:  # noqa: BLE001
                last_err = str(e)[:400]
                msgs = msgs + [{"role": "assistant", "content": raw},
                               {"role": "user", "content": prompts.SQL_REPAIR.format(error=last_err)}]
        raise RuntimeError(last_err)

    def _query(self, q: str, history: list[dict], route: dict) -> Result:
        try:
            sql, df = self._sql_with_repair(self._sql_messages(q, history))
        except NeedClarification as e:
            return Result("clarify", str(e))
        except RuntimeError:
            return Result("clarify", "Не удалось построить запрос по этому вопросу. Попробуйте переформулировать: "
                                     "уточните, какие данные и за какой период нужны.")
        if df.empty:
            res = Result("answer", "По этому запросу данных не найдено. Попробуйте изменить период или условия.",
                         sql=sql, df=df)
            self.last = res
            return res
        text = self.llm.chat([{"role": "user", "content": prompts.ANSWER.format(
            question=q, total=len(df), shown=min(len(df), 30), table=df_preview(df))}], temperature=0.1).strip()
        spec = None
        if route.get("chart"):
            spec = self._choose_chart(q, df, route.get("chart_type"))
        elif len(df) >= 3:
            spec = auto_spec(df)                       # автоматическая визуализация
        res = Result("chart" if spec else "answer", text, sql=sql, df=df, chart=spec)
        self.last = res
        return res

    # ------------------------------------------------------------------ графики
    def _choose_chart(self, request: str, df: pd.DataFrame, forced_type: str | None = None) -> dict | None:
        if df is None or len(df) < 1:
            return None
        spec = None
        try:
            raw = self.llm.chat([{"role": "user", "content": prompts.CHART.format(
                request=request, dtypes=dict((c, str(t)) for c, t in df.dtypes.items()),
                head=df_preview(df, 5))}], json_mode=True)
            spec = validate_spec(extract_json(raw), df)
        except LLMError:
            pass
        spec = spec or auto_spec(df)
        if spec and forced_type in ALLOWED:
            cand = validate_spec({**spec, "type": forced_type}, df)
            spec = cand or spec
        return spec

    def _rechart(self, q: str, route: dict) -> Result:
        spec = self._choose_chart(q, self.last.df, route.get("chart_type"))
        if not spec:
            return Result("answer", "Эти данные неудобно показывать графиком — вот таблица.",
                          sql=self.last.sql, df=self.last.df)
        res = Result("chart", "Вот тот же результат в другом виде.", sql=self.last.sql,
                     df=self.last.df, chart=spec)
        self.last = res
        return res

    # ------------------------------------------------------------------ дашборд
    def _dashboard(self, q: str) -> Result:
        raw = self.llm.chat([{"role": "user", "content": prompts.DASHBOARD.format(
            request=q, schema=self.schema)}], json_mode=True, temperature=0.1)
        draft = extract_json(raw)
        widgets = []
        for w in (draft.get("widgets") or [])[:6]:
            if not isinstance(w, dict) or not w.get("sql"):
                continue
            sql, df = w["sql"], None
            for attempt in range(2):                 # одна попытка автоисправления на виджет
                try:
                    sql, df = run_sql(self.db, sql)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 1:
                        break
                    fix = self.llm.chat([
                        {"role": "system", "content": prompts.SQL_SYSTEM.format(
                            schema=self.schema, max_rows=config.MAX_ROWS)},
                        {"role": "user", "content": f"Виджет: {w.get('title')}\nSQL:\n{sql}\n" +
                                                    prompts.SQL_REPAIR.format(error=str(e)[:300])}])
                    sql = extract_sql(fix)
            if df is None or df.empty:
                continue
            chart = validate_spec(w.get("chart"), df) or auto_spec(df)
            widgets.append({"title": str(w.get("title") or "Виджет"), "sql": sql, "chart": chart})
        if not widgets:
            return Result("clarify", "Не получилось собрать дашборд по этому описанию. "
                                     "Уточните, какие показатели и за какой период нужны.")
        spec = {"title": str(draft.get("title") or "Дашборд"), "widgets": widgets}
        dash_id = self.db.save_dashboard(spec["title"], spec)
        return Result("dashboard", f"Готово: дашборд «{spec['title']}» ({len(widgets)} виджетов). "
                                   "Он сохранён и доступен во вкладке «Дашборды».",
                      dashboard=spec, dashboard_id=dash_id)
