"""Агент: правила маршрутизации -> text-to-SQL с самоисправлением -> короткий ответ -> график/дашборд.

Скорость: на типичный вопрос приходится 2 вызова модели (SQL и пересказ), а не 3–4. Намерение определяется правилами
(ROUTER_MODE=rules), тип графика выбирается без модели, одиночные значения пересказываются шаблоном,
виджеты дашборда считаются параллельно и кешируются. Время каждого этапа возвращается в Result.timing."""
from __future__ import annotations

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import cache, config, prompts
from .charts import ALLOWED, auto_spec, guess_axes, is_time, validate_spec
from .llm import LLMError, extract_json, extract_sql
from .profiler import facts_markdown, schema_text
from .safety import UnsafeSQL, limit_sql, validate_sql

# ---------------------------------------------------------------- правила маршрутизации
OVERVIEW_RE = re.compile(
    r"что (передо мной|есть в (этих )?данн|за данные|в этих данных)|опиши (эти )?данные|"
    r"расскажи (о|про) (этих )?данн|обзор данных|за какой период.*(данн|они)",
    re.I,
)
DASHBOARD_RE = re.compile(r"дашборд|dashboard|информационн\w+ панел|сводн\w+ панел", re.I)
EXPLAIN_RE = re.compile(
    r"что\s+(такое|значит|означает|обозначает)\b|что\s+за\s+(поле|колонк\w*|столб\w*|таблиц\w*)|"
    r"расшифруй|объясни\s+(поле|колонк\w*|столб\w*)", re.I)
CHART_WORDS_RE = re.compile(r"график|диаграмм|гистограмм|визуализ|нарисуй|наглядн|\bchart\b|\bplot\b", re.I)
RECHART_HINT_RE = re.compile(
    r"друг(ом|ой|ое|им)\s+(виде|формате|графике|способом|типе)|\bиначе\b|перестро|то\s+же\s+самое|"
    r"\bэто\s+(же\s+)?(в\s+виде|как|графиком|диаграммой)", re.I)
CHART_TYPE_PATTERNS = [
    ("pie", r"кругов|пирог|\bpie\b"),
    ("bar", r"столбч|столбик|\bbar\b"),
    ("line", r"линейн|линией|\bline\b"),
    ("histogram", r"гистограмм"),
    ("scatter", r"точечн|\bscatter\b|рассеян"),
    ("area", r"област"),
]
# слова, которые сами по себе не задают «предмет» запроса: если кроме них ничего нет, пользователь просит
# показать ПРЕДЫДУЩИЙ результат иначе, а не задаёт новый вопрос
_FILLER_RE = re.compile(
    r"^(постро\w*|сдела\w*|покаж\w*|нарису\w*|отобраз\w*|перестро\w*|преобраз\w*|график\w*|диаграмм\w*|"
    r"гистограмм\w*|линейн\w*|столбч\w*|кругов\w*|точечн\w*|област\w*|друг\w*|вид\w{0,2}|формат\w*|"
    r"наглядн\w*|визуализ\w*|пожалуйста|эт[аиоу]\w*|то|же|сам\w*|его|её|их|ещё|еще|теперь|снова|опять|"
    r"как|и|в|на|с|а|но|из|по|за|для|от|до|при|данн\w*|результат\w*|таблиц\w*|им|ей|иначе)$", re.I)
INTENTS = {"overview", "explain", "query", "rechart", "dashboard", "clarify", "reject"}


def detect_chart_type(q: str) -> str | None:
    for t, pat in CHART_TYPE_PATTERNS:
        if re.search(pat, q, re.I):
            return t
    return None


def _content_words(q: str) -> list[str]:
    return [w for w in re.findall(r"[а-яёa-z0-9_]+", q.lower()) if not _FILLER_RE.match(w)]


def classify_request(q: str, has_df: bool) -> dict:
    """Намерение по правилам, без вызова модели. has_df: есть ли предыдущий результат с таблицей."""
    if OVERVIEW_RE.search(q):
        return {"intent": "overview"}
    if DASHBOARD_RE.search(q):
        return {"intent": "dashboard"}
    ctype = detect_chart_type(q)
    chart_words = bool(CHART_WORDS_RE.search(q)) or ctype is not None
    n = len(_content_words(q))
    # «Покажи иначе» / «сделай круговой»: в сообщении нет нового предмета запроса. Если он есть («...но по регионам»,
    # «...за 2025 год»), это новый расчёт, и им занимается шаг SQL, которому доступна история диалога.
    if (RECHART_HINT_RE.search(q) or chart_words) and n == 0:
        if has_df:
            return {"intent": "rechart", "chart_type": ctype}
        return {"intent": "clarify", "message":
                "Какой график построить? Назовите показатель и период, например «динамика продаж по месяцам». "
                "Если нужно показать предыдущий результат иначе, сначала задайте вопрос по данным."}
    if EXPLAIN_RE.search(q):
        return {"intent": "explain"}
    return {"intent": "query", "chart": True if chart_words else None, "chart_type": ctype}


# ---------------------------------------------------------------- результат и вспомогательные функции
@dataclass
class Result:
    kind: str                       # overview|answer|chart|dashboard|clarify|reject|error
    text: str
    sql: str | None = None
    df: pd.DataFrame | None = None
    chart: dict | None = None
    dashboard: dict | None = None
    dashboard_id: int | None = None
    widgets: list[dict] | None = None      # уже посчитанные виджеты дашборда (интерфейсу не нужно считать заново)
    timing: dict | None = None             # llm_calls, llm_s, db_s, total_s, max_prompt_chars


class NeedClarification(Exception):
    pass


class Rejected(Exception):
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


def _fmt(v) -> str:
    if v is None or v is pd.NaT or v is pd.NA or (isinstance(v, float) and math.isnan(v)):
        return "нет данных"
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second) == (0, 0, 0) else v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, (bool, np.bool_)):
        return "да" if v else "нет"
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}".replace(",", " ")
    if isinstance(v, (float, np.floating)):
        s = f"{float(v):,.2f}".replace(",", " ").replace(".", ",")
        return s[:-3] if s.endswith(",00") else s.rstrip("0")
    return str(v)


def template_answer(df: pd.DataFrame) -> str:
    """Ответ без модели: числа берутся прямо из результата запроса."""
    if df.empty:
        return "Данных не найдено."
    if len(df) == 1:
        return "; ".join(f"{c}: {_fmt(v)}" for c, v in df.iloc[0].items())
    rows = "; ".join(" — ".join(_fmt(v) for v in r) for r in df.head(3).itertuples(index=False))
    return f"Найдено строк: {len(df)}. Первые: {rows}."


def execute_dashboard(db, spec: dict, use_cache: bool = True) -> list[dict]:
    """Результаты виджетов дашборда. Берёт из кеша (его заполняет создание дашборда и прошлые показы);
    кеш сбрасывается при загрузке новых данных и по времени."""
    out = []
    for w in spec.get("widgets", []):
        k = cache.key(db, w["sql"])
        df = cache.get(k) if use_cache else None
        err = None
        if df is None:
            try:
                _, df = run_sql(db, w["sql"])
                cache.put(k, df)
            except Exception as e:  # noqa: BLE001
                df, err = None, str(e)[:200]
        out.append({"title": w["title"], "df": df, "chart": w.get("chart") if df is not None else None, "error": err})
    return out


class Agent:
    def __init__(self, db, llm, profiles: list[dict], relations: list[dict]):
        self.db, self.llm = db, llm
        self.profiles, self.relations = profiles, relations
        self.schema = schema_text(profiles, relations)
        self.last: Result | None = None      # контекст сессии: последний результат
        self._overview_text: str | None = None
        self._tm: dict = {}

    # ------------------------------------------------------------------ измерения
    def _chat(self, messages, **kw) -> str:
        t = time.perf_counter()
        try:
            return self.llm.chat(messages, **kw)
        finally:
            self._tm["llm_calls"] += 1
            self._tm["llm_s"] += time.perf_counter() - t
            self._tm["max_prompt_chars"] = max(self._tm["max_prompt_chars"], sum(len(m["content"]) for m in messages))

    def _run_sql(self, sql: str) -> tuple[str, pd.DataFrame]:
        t = time.perf_counter()
        try:
            return run_sql(self.db, sql)
        finally:
            self._tm["db_s"] += time.perf_counter() - t

    # ------------------------------------------------------------------ вход
    def ask(self, question: str, history: list[dict] | None = None) -> Result:
        history = history or []
        self._tm = {"llm_calls": 0, "llm_s": 0.0, "db_s": 0.0, "max_prompt_chars": 0}
        t0 = time.perf_counter()
        try:
            res = self._ask(question.strip(), history)
        except LLMError as e:
            res = Result("error", f"Не удаётся связаться с моделью. {e}")
        except Exception as e:  # noqa: BLE001
            res = Result("error", f"Не получилось обработать запрос ({str(e)[:150]}). "
                                  "Попробуйте переформулировать вопрос.")
        res.timing = {**self._tm, "total_s": time.perf_counter() - t0}
        return res

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
        has_df = self.last is not None and self.last.df is not None
        route = classify_request(q, has_df)
        # правила надёжнее модели для дашбордов, обзора и «покажи иначе»; вызов модели-маршрутизатора нужен
        # только в режиме ROUTER_MODE=llm и только для остальных вопросов
        if config.ROUTER_MODE != "llm" or route["intent"] != "query":
            return route
        msgs = [
            {"role": "system", "content": prompts.ROUTER},
            {"role": "user", "content": f"История:\n{history_text(history, 2)}\n\nСообщение: {q}"},
        ]
        data = extract_json(self._chat(msgs, json_mode=True, max_tokens=200))
        if data.get("intent") not in INTENTS or data["intent"] in ("rechart", "dashboard", "overview"):
            return route
        data.setdefault("chart", route.get("chart"))
        data.setdefault("chart_type", route.get("chart_type"))
        return data

    # ------------------------------------------------------------------ сценарий 1
    def _overview(self) -> Result:
        facts = facts_markdown(self.profiles, self.relations)
        if self._overview_text is None:          # описание данных не меняется, пока не обновлён профиль: считаем один раз
            try:
                self._overview_text = self._chat(
                    [{"role": "user", "content": prompts.OVERVIEW.format(schema=self.schema)}],
                    temperature=0.2, max_tokens=config.OVERVIEW_MAX_TOKENS).strip()
            except LLMError:
                return Result("overview", f"_Модель недоступна, показываю только сводку фактов._\n\n"
                                          f"**Сводка по данным**\n\n{facts}")
        return Result("overview", f"{self._overview_text}\n\n**Сводка по данным**\n\n{facts}")

    # ------------------------------------------------------------------ сценарий 2
    def _explain(self, q: str, history: list[dict]) -> Result:
        txt = self._chat([{"role": "user", "content": prompts.EXPLAIN.format(
            schema=self.schema, history=history_text(history, 2), question=q)}],
            temperature=0.1, max_tokens=config.NARRATE_MAX_TOKENS * 2)
        return Result("answer", txt.strip())

    def _sql_messages(self, q: str, history: list[dict]) -> list[dict]:
        msgs = [{"role": "system", "content": prompts.SQL_SYSTEM.format(
            schema=self.schema, max_rows=config.MAX_ROWS)}]
        for m in history[-config.HISTORY_TURNS:]:
            msgs.append({"role": m["role"], "content": m["content"][:500]})
        msgs.append({"role": "user", "content": q})
        return msgs

    def _sql_with_repair(self, msgs: list[dict]) -> tuple[str, pd.DataFrame]:
        last_err = "неизвестная ошибка"
        for _ in range(config.SQL_RETRIES + 1):
            raw = self._chat(msgs, max_tokens=config.SQL_MAX_TOKENS)
            head = raw.strip().upper()
            if head.startswith("CLARIFY:"):
                raise NeedClarification(raw.split(":", 1)[1].strip())
            if head.startswith("REJECT:"):
                raise Rejected(raw.split(":", 1)[1].strip())
            sql = extract_sql(raw)
            try:
                return self._run_sql(sql)
            except (UnsafeSQL, Exception) as e:  # noqa: BLE001
                last_err = str(e)[:400]
                msgs = msgs + [{"role": "assistant", "content": raw},
                               {"role": "user", "content": prompts.SQL_REPAIR.format(error=last_err)}]
        raise RuntimeError(last_err)

    def _narrate(self, q: str, df: pd.DataFrame) -> str:
        if config.NARRATION == "template" or (config.NARRATION == "auto" and len(df) == 1):
            return template_answer(df)
        text = self._chat([{"role": "user", "content": prompts.ANSWER.format(
            question=q, total=len(df), shown=min(len(df), 30), table=df_preview(df))}],
            temperature=0.1, max_tokens=config.NARRATE_MAX_TOKENS).strip()
        return text or template_answer(df)

    def _query(self, q: str, history: list[dict], route: dict) -> Result:
        try:
            sql, df = self._sql_with_repair(self._sql_messages(q, history))
        except NeedClarification as e:
            return Result("clarify", str(e))
        except Rejected as e:
            return Result("reject", str(e) or "Такой запрос я выполнить не могу.")
        except RuntimeError:
            return Result("clarify", "Не удалось построить запрос по этому вопросу. Попробуйте переформулировать: "
                                     "уточните, какие данные и за какой период нужны.")
        if df.empty:
            res = Result("answer", "По этому запросу данных не найдено. Попробуйте изменить период или условия.",
                         sql=sql, df=df)
            self.last = res
            return res
        text = self._narrate(q, df)
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
        """Без вызова модели: тип называет пользователь или подбирают правила. Модель только при CHART_LLM=1."""
        if df is None or len(df) < 1:
            return None
        spec = None
        if config.CHART_LLM and not forced_type:
            try:
                raw = self._chat([{"role": "user", "content": prompts.CHART.format(
                    request=request, dtypes=dict((c, str(t)) for c, t in df.dtypes.items()),
                    head=df_preview(df, 5))}], json_mode=True, max_tokens=250)
                spec = validate_spec(extract_json(raw), df)
            except LLMError:
                pass
        spec = spec or auto_spec(df)
        if forced_type in ALLOWED:
            base = spec or guess_axes(df)
            cand = validate_spec({**base, "type": forced_type}, df) if base else None
            spec = cand or spec
        return spec

    def _alternative_spec(self, df: pd.DataFrame, current: str | None) -> dict | None:
        """«Покажи в другом виде»: другой подходящий тип графика для тех же данных."""
        base = auto_spec(df) or guess_axes(df)
        if not base:
            return None
        order = ["line", "bar", "area"] if is_time(df[base["x"]]) else ["bar", "pie", "line"]
        for t in order:
            if t != current:
                cand = validate_spec({**base, "type": t}, df)
                if cand:
                    return cand
        return None

    def _rechart(self, q: str, route: dict) -> Result:
        df = self.last.df
        forced = route.get("chart_type")
        if forced:
            spec = self._choose_chart(q, df, forced)
        else:
            spec = self._alternative_spec(df, (self.last.chart or {}).get("type"))
        if not spec:
            return Result("answer", "Эти данные неудобно показывать графиком — вот таблица.",
                          sql=self.last.sql, df=df)
        res = Result("chart", "Вот тот же результат в другом виде.", sql=self.last.sql, df=df, chart=spec)
        self.last = res
        return res

    # ------------------------------------------------------------------ дашборд
    def _run_widgets(self, sqls: list[str]) -> list[tuple[str, pd.DataFrame | None, str | None]]:
        def one(sql):
            try:
                clean, df = run_sql(self.db, sql)
                return clean, df, None
            except Exception as e:  # noqa: BLE001
                return sql, None, str(e)[:300]

        t = time.perf_counter()
        # у PostgreSQL запросы идут по отдельным соединениям параллельно; у учебных подмен (SQLite) последовательно
        if getattr(self.db, "dialect", "") == "postgresql" and len(sqls) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(sqls))) as pool:
                out = list(pool.map(one, sqls))
        else:
            out = [one(s) for s in sqls]
        self._tm["db_s"] += time.perf_counter() - t
        return out

    def _dashboard(self, q: str) -> Result:
        raw = self._chat([{"role": "user", "content": prompts.DASHBOARD.format(request=q, schema=self.schema)}],
                         json_mode=True, temperature=0.1, max_tokens=config.DASHBOARD_MAX_TOKENS)
        draft = extract_json(raw)
        items = [w for w in (draft.get("widgets") or [])[:config.DASHBOARD_MAX_WIDGETS]
                 if isinstance(w, dict) and w.get("sql")]
        results = self._run_widgets([w["sql"] for w in items])
        for i, (sql, df, err) in enumerate(results):      # одна попытка автоисправления на виджет
            if df is None and err:
                fix = self._chat([
                    {"role": "system", "content": prompts.SQL_SYSTEM.format(
                        schema=self.schema, max_rows=config.MAX_ROWS)},
                    {"role": "user", "content": f"Виджет: {items[i].get('title')}\nSQL:\n{sql}\n" +
                                                prompts.SQL_REPAIR.format(error=err[:300])}],
                    max_tokens=config.SQL_MAX_TOKENS)
                try:
                    clean, df2 = self._run_sql(extract_sql(fix))
                    results[i] = (clean, df2, None)
                except Exception as e:  # noqa: BLE001
                    results[i] = (sql, None, str(e)[:300])
        widgets, data = [], []
        for w, (sql, df, _err) in zip(items, results):
            if df is None or df.empty:
                continue
            chart = validate_spec(w.get("chart"), df) or auto_spec(df)
            title = str(w.get("title") or "Виджет")
            widgets.append({"title": title, "sql": sql, "chart": chart})
            data.append({"title": title, "df": df, "chart": chart, "error": None})
            cache.put(cache.key(self.db, sql), df)       # первый показ дашборда не пересчитывает запросы
        if not widgets:
            return Result("clarify", "Не получилось собрать дашборд по этому описанию. "
                                     "Уточните, какие показатели и за какой период нужны.")
        spec = {"title": str(draft.get("title") or "Дашборд"), "widgets": widgets}
        dash_id = self.db.save_dashboard(spec["title"], spec)
        res = Result("dashboard", f"Готово: дашборд «{spec['title']}» ({len(widgets)} виджетов). "
                                  "Он сохранён и доступен во вкладке «Дашборды».",
                     dashboard=spec, dashboard_id=dash_id, widgets=data)
        self.last = res        # после дашборда «построй график» без уточнения не повторяет дашборд
        return res
