"""Запуск: python -m tests.test_core  (или pytest). Не требует сети, Postgres и Ollama."""
import os

import pandas as pd

from core.agent import Agent
from core.charts import auto_spec, validate_spec
from core.llm import extract_json, extract_sql
from core.loader import read_csv_smart, table_name_from_file
from core.profiler import profile_all
from core.safety import UnsafeSQL, validate_sql
from tests.fakes import ScriptedLLM, SqliteDB

DATA = os.path.join(os.path.dirname(__file__), "..", "sample_data")


def make_db():
    db = SqliteDB()
    for f in ("users.csv", "events.csv", "payments.csv"):
        db.load_dataframe(read_csv_smart(os.path.join(DATA, f)), table_name_from_file(f))
    return db


def test_loader_detects_separator_and_dates():
    df = read_csv_smart(os.path.join(DATA, "payments.csv"))
    assert list(df.columns) == ["payment_id", "user_id", "paid_at", "amount", "status"]
    assert pd.api.types.is_datetime64_any_dtype(df["paid_at"])      # 13.03.2024 распознана как дата
    assert df["paid_at"].dt.month.between(1, 12).all()


def test_profile_and_relations():
    db = make_db()
    profiles, rels = profile_all(db)
    by = {p["name"]: p for p in profiles}
    assert by["users"]["rows"] == 2000 and by["events"]["rows"] == 20000
    assert by["payments"]["duplicate_rows"] == 30                    # найдены дубликаты
    assert by["events"]["time_col"] == "ts"
    kinds = {c["name"]: c["kind"] for c in by["users"]["columns"]}
    assert kinds["user_id"] == "id" and kinds["plan"] == "categorical"
    pairs = {frozenset([(r["a"], r["a_col"]), (r["b"], r["b_col"])]) for r in rels}
    assert frozenset([("users", "user_id"), ("payments", "user_id")]) in pairs   # связь через user_id


def test_safety():
    assert validate_sql('SELECT 1;').startswith("SELECT")
    assert validate_sql('WITH a AS (SELECT 1) SELECT * FROM a')
    assert validate_sql("SELECT 'drop table x' AS t")                # слово внутри строки — не угроза
    for bad in ("DROP TABLE users", "SELECT 1; DELETE FROM users", "UPDATE users SET a=1",
                "SELECT * INTO t2 FROM users", "SELECT pg_sleep(10)", "SELECT * FROM _bi_dashboards",
                "SELECT 1 -- x\n; DROP TABLE a", ""):
        try:
            validate_sql(bad)
        except UnsafeSQL:
            continue
        raise AssertionError(f"пропустил опасный SQL: {bad!r}")


def test_helpers():
    assert extract_sql("текст ```sql\nSELECT 1\n``` ещё") == "SELECT 1"
    assert extract_json('мусор {"a": 1} мусор') == {"a": 1}
    df = pd.DataFrame({"m": ["2024-01", "2024-02", "2024-03"], "n": [1, 2, 3]})
    assert auto_spec(df)["type"] == "line"
    assert validate_spec({"type": "bar", "x": "nope", "y": "n"}, df) is None


def _agent(replies):
    db = make_db()
    profiles, rels = profile_all(db)
    llm = ScriptedLLM(replies)
    return Agent(db, llm, profiles, rels), llm


def test_overview_scenario_1():
    agent, llm = _agent(["Это данные о пользователях, событиях и платежах."])
    r = agent.ask("Что передо мной?")
    assert r.kind == "overview" and "20 000" in r.text and "users" in r.text
    assert len(llm.calls) == 1                                       # роутер не вызывался (быстрый путь)


def test_query_scenario_2_with_self_repair_and_context():
    agent, llm = _agent([
        '```sql\nSELECT planx FROM "users"\n```',                              # неверная колонка
        '```sql\nSELECT "plan" AS "Тариф", COUNT(*) AS "Пользователей" FROM "users" GROUP BY 1\n```',
        "Больше всего пользователей на тарифе basic.",                          # ответ
    ])
    r = agent.ask("Сколько пользователей на каждом тарифе?")
    assert r.df is not None and len(r.df) == 3 and "basic" in r.text
    assert r.chart and r.chart["type"] == "bar"                                  # авто-визуализация
    assert any("Ошибка" in m["content"] for m in llm.calls[1])                  # агент увидел ошибку и исправил
    assert len(llm.calls) == 3                                                   # SQL, исправление, пересказ: без вызова маршрутизатора

    # диалог с контекстом: «в виде круговой диаграммы» работает с прошлым результатом и не требует вызова модели
    n = len(llm.calls)
    r2 = agent.ask("А теперь покажи то же самое в виде круговой диаграммы")
    assert r2.chart["type"] == "pie" and r2.df is r.df and len(llm.calls) == n


def test_unsafe_sql_from_llm_is_blocked_and_clarified():
    agent, _ = _agent(['```sql\nDROP TABLE users\n```'] * 3)
    r = agent.ask("удали пользователей")
    assert r.kind == "clarify"
    assert "users" in agent.db.tables()


def test_clarify_and_reject_come_from_the_sql_step():
    agent, _ = _agent(["CLARIFY: За какой период показать?", "REJECT: Я не меняю данные."])
    r = agent.ask("пokажи динамику")
    assert r.text == "За какой период показать?", r.text
    r = agent.ask("очисти таблицу")
    assert r.kind == "reject" and "не меняю" in r.text


def test_llm_router_mode_is_still_available():
    from core import config
    old = config.ROUTER_MODE
    config.ROUTER_MODE = "llm"
    try:
        agent, llm = _agent(['{"intent": "clarify", "message": "За какой период показать?"}'])
        assert agent.ask("покажи динамику").text == "За какой период показать?"
        assert len(llm.calls) == 1
    finally:
        config.ROUTER_MODE = old


def test_dashboard_saved_and_reusable():
    spec = ('{"title": "Платежи по месяцам", "widgets": ['
            '{"title": "Сумма по статусам", "sql": "SELECT status AS \\"Статус\\", SUM(amount) AS \\"Сумма\\" '
            'FROM payments GROUP BY 1", "chart": {"type": "bar", "x": "Статус", "y": "Сумма"}},'
            '{"title": "Битый виджет", "sql": "SELECT nope FROM payments", "chart": {}}]}')
    agent, llm = _agent([spec, "SELECT nope2 FROM payments"])        # дашборд и единственная попытка исправить битый виджет
    r = agent.ask("Создай дашборд по платежам")
    assert r.kind == "dashboard" and len(r.dashboard["widgets"]) == 1       # битый виджет отброшен
    assert r.widgets and r.widgets[0]["df"] is not None                     # данные уже посчитаны, интерфейсу не нужно считать заново
    saved = agent.db.get_dashboard(r.dashboard_id)
    from core.agent import execute_dashboard
    out = execute_dashboard(agent.db, saved)
    assert out[0]["error"] is None and len(out[0]["df"]) == 3


def test_llm_providers_with_mocked_http():
    import core.llm as llm_mod

    class Resp:
        def __init__(self, code, data):
            self.status_code, self._d = code, data

        def json(self):
            return self._d

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json))
        if url.endswith("/chat/completions"):
            if json.get("response_format"):                    # сервер не знает response_format
                return Resp(400, {})
            return Resp(200, {"choices": [{"message": {"content": "ok-openai"}}]})
        return Resp(200, {"message": {"content": "ok-ollama"}})

    orig = llm_mod.requests.post
    llm_mod.requests.post = fake_post
    try:
        o = llm_mod.LLMClient("ollama", "http://x:11434", "m")
        assert o.chat([{"role": "user", "content": "hi"}]) == "ok-ollama"
        a = llm_mod.LLMClient("openai", "http://x:8000/v1", "m")
        assert a.chat([{"role": "user", "content": "hi"}], json_mode=True) == "ok-openai"
        assert calls[-1][0] == "http://x:8000/v1/chat/completions" and "response_format" not in calls[-1][1]
        try:
            llm_mod.LLMClient("gpt-cloud")
        except ValueError:
            pass
        else:
            raise AssertionError("неизвестный провайдер должен отвергаться")
    finally:
        llm_mod.requests.post = orig


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\nВсе тесты пройдены: {len(tests)}")
