"""Скорость и маршрутизация: правила вместо вызова модели, число вызовов на вопрос, кеш дашбордов.
Сценарии взяты из замеров команды. Запуск: uv run python -m tests.test_speed_routing"""
from core import cache
from core.agent import Agent, classify_request, execute_dashboard, template_answer
from core.profiler import profile_all
from tests.fakes import ScriptedLLM
from tests.test_core import make_db

SQL_TARIFF = '```sql\nSELECT "plan" AS "Тариф", COUNT(*) AS "Пользователей" FROM users GROUP BY 1\n```'
SQL_MONTHS = ('```sql\nSELECT strftime(\'%Y-%m\', paid_at) AS "Месяц", COUNT(*) AS "Платежей" '
              'FROM payments GROUP BY 1 ORDER BY 1\n```')
DASHBOARD_JSON = ('{"title": "Платежи", "widgets": [{"title": "По статусам", "sql": "SELECT status AS \\"Статус\\", '
                  'COUNT(*) AS \\"N\\" FROM payments GROUP BY 1", "chart": {"type": "bar", "x": "Статус", "y": "N"}}]}')


def _agent(replies, db=None):
    db = db or make_db()
    profiles, rels = profile_all(db)
    llm = ScriptedLLM(replies)
    return Agent(db, llm, profiles, rels), llm


def test_classify_request_examples():
    cases = [
        ("Что передо мной?", False, "overview"),
        ("Создай дашборд о возвратах с 3 квартала 2024 года по 2 квартал 2025", True, "dashboard"),   # был принят за «покажи иначе»
        ("Создай дашборд о возвратах с 3 квартала 2024 года по 2 квартал 2025", False, "dashboard"),
        ("Покажи это в другом виде", True, "rechart"),
        ("А теперь покажи то же самое в виде круговой диаграммы", True, "rechart"),
        ("Сделай это круговой диаграммой", True, "rechart"),
        ("Построй график", True, "rechart"),
        ("Покажи иначе", True, "rechart"),
        ("Перестрой в столбчатую", True, "rechart"),
        ("Покажи то же самое, но по регионам", True, "query"),                                        # новый предмет: новый расчёт
        ("Покажи это в другом виде за 2025 год", True, "query"),
        ("Построй график по месяцам", True, "query"),
        ("Построй график", False, "clarify"),
        ("Построй линейный график количества продаж за 2025 год", True, "query"),                   # новый вопрос, не «иначе»
        ("Покажи продажи графиком", True, "query"),
        ("Покажи динамику продаж", True, "query"),
        ("Сколько продаж было в январе?", True, "query"),
        ("Что означает поле дата_операции_мск?", False, "explain"),
    ]
    for q, has_df, expected in cases:
        got = classify_request(q, has_df)["intent"]
        assert got == expected, f"{q!r} (есть результат: {has_df}): ждали {expected}, получили {got}"
    assert classify_request("Построй линейный график количества продаж за 2025 год", True)["chart_type"] == "line"
    assert classify_request("Сделай это круговой диаграммой", True)["chart_type"] == "pie"


def test_typical_question_needs_two_model_calls_and_single_value_needs_one():
    agent, llm = _agent([SQL_TARIFF, "Больше всего пользователей на тарифе basic."])
    r = agent.ask("Сколько пользователей на каждом тарифе?")
    assert len(llm.calls) == 2 and r.timing["llm_calls"] == 2           # было 3–4 вызова
    assert r.timing["total_s"] >= r.timing["llm_s"] and r.timing["db_s"] >= 0

    agent, llm = _agent(['```sql\nSELECT COUNT(*) AS "Пользователей" FROM users\n```'])
    r = agent.ask("Сколько всего пользователей?")
    assert len(llm.calls) == 1 and "2 000" in r.text                    # одно число пересказывается шаблоном


def test_explicit_line_chart_is_a_new_query_and_needs_no_extra_chart_call():
    agent, llm = _agent([SQL_TARIFF, "ответ 1", SQL_MONTHS, "ответ 2"])
    agent.ask("Сколько пользователей на каждом тарифе?")                # теперь есть «последний результат»
    n = len(llm.calls)
    r = agent.ask("Построй линейный график количества платежей по месяцам за 2024 год")
    assert len(llm.calls) - n == 2                                      # SQL и пересказ (раньше ещё маршрутизатор и график)
    assert r.chart["type"] == "line" and "Месяц" in r.df.columns


def test_dashboard_request_after_a_result_is_not_mistaken_for_rechart():
    agent, llm = _agent([SQL_TARIFF, "ответ", DASHBOARD_JSON])
    agent.ask("Сколько пользователей на каждом тарифе?")
    r = agent.ask("Создай дашборд о возвратах с 3 квартала 2024 года по 2 квартал 2025")
    assert r.kind == "dashboard" and len(r.widgets) == 1


def test_chart_request_right_after_a_dashboard_asks_for_clarification():
    agent, llm = _agent([DASHBOARD_JSON])
    agent.ask("Создай дашборд по платежам")
    n = len(llm.calls)
    r = agent.ask("Построй график")
    assert r.kind == "clarify" and len(llm.calls) == n                  # не создаёт ещё один дашборд и не тратит вызов модели


def test_other_view_without_type_picks_a_different_chart():
    agent, llm = _agent([SQL_TARIFF, "ответ"])
    first = agent.ask("Сколько пользователей на каждом тарифе?")
    r = agent.ask("Покажи это в другом виде")
    assert r.chart["type"] != first.chart["type"] and len(llm.calls) == 2


def test_overview_text_is_generated_once_per_profile():
    agent, llm = _agent(["Это данные о пользователях, событиях и платежах."])
    a = agent.ask("Что передо мной?")
    b = agent.ask("Что есть в этих данных?")
    assert len(llm.calls) == 1 and a.text == b.text


def test_dashboard_results_are_cached_until_data_changes():
    db = make_db()
    counter = {"n": 0}
    orig = db.query

    def counting(sql, max_rows=None):
        counter["n"] += 1
        return orig(sql, max_rows)

    db.query = counting
    cache.bump()
    spec = {"title": "t", "widgets": [{"title": "w", "chart": {"type": "bar", "x": "Статус", "y": "N"},
                                       "sql": 'SELECT status AS "Статус", COUNT(*) AS "N" FROM payments GROUP BY 1'}]}
    a = execute_dashboard(db, spec)
    execute_dashboard(db, spec)                                         # перерисовка интерфейса: запрос не повторяется
    assert counter["n"] == 1 and a[0]["df"] is not None
    cache.bump()                                                        # загружены новые данные
    execute_dashboard(db, spec)
    assert counter["n"] == 2


def test_creating_a_dashboard_fills_the_cache_for_its_first_display():
    db = make_db()
    counter = {"n": 0}
    orig = db.query

    def counting(sql, max_rows=None):
        counter["n"] += 1
        return orig(sql, max_rows)

    db.query = counting
    agent, _ = _agent([DASHBOARD_JSON], db=db)
    r = agent.ask("Создай дашборд по платежам")
    after_create = counter["n"]
    execute_dashboard(db, db.get_dashboard(r.dashboard_id))
    assert counter["n"] == after_create


def test_template_answer_formats_numbers_from_the_result():
    import pandas as pd
    assert template_answer(pd.DataFrame({"Выручка": [1234567.5]})) == "Выручка: 1 234 567,5"
    assert template_answer(pd.DataFrame({"a": [1, 2, 3, 4], "b": ["x", "y", "z", "w"]})).startswith("Найдено строк: 4.")
    assert template_answer(pd.DataFrame({"a": []})) == "Данных не найдено."


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\nВсе тесты скорости и маршрутизации пройдены: {len(tests)}")
