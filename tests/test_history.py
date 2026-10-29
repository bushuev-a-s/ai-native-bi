"""Запуск: python -m tests.test_history. Без Postgres и Ollama (SQLite-заглушка)."""
import os

from core.agent import Agent
from core.history import DEFAULT_TITLE, ChatHistory, hydrate, make_title, restore_agent_context
from core.loader import read_csv_smart, table_name_from_file
from core.profiler import profile_all
from tests.fakes import ScriptedLLM, SqliteDB

DATA = os.path.join(os.path.dirname(__file__), "..", "sample_data")


def make_db():
    db = SqliteDB()
    for f in ("users.csv", "events.csv", "payments.csv"):
        db.load_dataframe(read_csv_smart(os.path.join(DATA, f)), table_name_from_file(f))
    return db


def test_create_list_and_title_from_first_question():
    h = ChatHistory(SqliteDB())
    cid = h.create_chat()
    assert h.get_chat(cid)["title"] == DEFAULT_TITLE
    h.add_message(cid, "user", "Сколько пользователей на каждом тарифе?")
    h.add_message(cid, "assistant", "Больше всего на basic.")
    h.add_message(cid, "user", "А платежей?")                       # название больше не меняется
    assert h.get_chat(cid)["title"] == "Сколько пользователей на каждом тарифе?"
    chats = h.list_chats()
    assert len(chats) == 1 and chats[0]["messages"] == 3


def test_long_title_is_shortened():
    t = make_title("очень " * 40)
    assert len(t) <= 60 and t.endswith("…")
    assert make_title("  ") == DEFAULT_TITLE


def test_messages_roundtrip_keeps_order_sql_and_chart():
    h = ChatHistory(SqliteDB())
    cid = h.create_chat()
    chart = {"type": "bar", "x": "Тариф", "y": "Пользователей", "title": "Тарифы"}
    h.add_message(cid, "user", "тарифы")
    h.add_message(cid, "assistant", "Вот.", kind="chart", sql='SELECT 1 AS "a"', chart=chart)
    h.add_message(cid, "assistant", "Дашборд готов", kind="dashboard", dashboard_id=7)
    msgs = h.get_messages(cid)
    assert [m["role"] for m in msgs] == ["user", "assistant", "assistant"]
    assert msgs[1]["sql"] == 'SELECT 1 AS "a"' and msgs[1]["chart"] == chart
    assert msgs[0]["chart"] is None and msgs[2]["dashboard_id"] == 7


def test_chats_are_isolated_and_delete_removes_messages():
    h = ChatHistory(SqliteDB())
    a, b = h.create_chat(), h.create_chat()
    h.add_message(a, "user", "вопрос A")
    h.add_message(b, "user", "вопрос B")
    h.delete_chat(a)
    assert h.get_chat(a) is None and h.get_messages(a) == []
    assert [m["text"] for m in h.get_messages(b)] == ["вопрос B"]
    assert [c["id"] for c in h.list_chats()] == [b]


def test_recently_used_chat_goes_first():
    import core.history as hist
    stamps = iter(["2024-01-01 10:00:00", "2024-01-01 10:00:01", "2024-01-01 10:00:02",
                   "2024-01-01 10:00:03", "2024-01-01 10:00:04"])
    orig, hist._now = hist._now, lambda: next(stamps)
    try:
        h = ChatHistory(SqliteDB())
        a, b = h.create_chat(), h.create_chat()                     # b создан позже
        assert h.list_chats()[0]["id"] == b
        h.add_message(a, "user", "дописали в старый чат")           # a стал самым свежим
        assert h.list_chats()[0]["id"] == a
    finally:
        hist._now = orig


def test_rename():
    h = ChatHistory(SqliteDB())
    cid = h.create_chat()
    h.rename_chat(cid, "Платежи за июль")
    assert h.get_chat(cid)["title"] == "Платежи за июль"


def test_history_survives_restart():
    """«Закрыли вкладку, перезапустили приложение — чат на месте»: новый ChatHistory на той же БД."""
    db = SqliteDB()
    cid = ChatHistory(db).create_chat()
    ChatHistory(db).add_message(cid, "user", "привет")
    h2 = ChatHistory(db)                                            # повторный запуск не стирает таблицы
    assert h2.get_messages(cid)[0]["text"] == "привет" and len(h2.list_chats()) == 1


def test_hydrate_recomputes_table_and_restores_agent_context():
    db = make_db()
    sql = 'SELECT "plan" AS "Тариф", COUNT(*) AS "Пользователей" FROM "users" GROUP BY 1'
    chart = {"type": "bar", "x": "Тариф", "y": "Пользователей"}
    h = ChatHistory(db)
    cid = h.create_chat()
    h.add_message(cid, "user", "Сколько пользователей на каждом тарифе?")
    h.add_message(cid, "assistant", "Больше всего на basic.", kind="chart", sql=sql, chart=chart)

    msgs = hydrate(db, h.get_messages(cid))
    assert msgs[0]["df"] is None
    assert len(msgs[1]["df"]) == 3 and msgs[1]["chart"] == chart and not msgs[1]["stale"]

    profiles, rels = profile_all(db)
    agent = Agent(db, ScriptedLLM([]), profiles, rels)
    restore_agent_context(agent, msgs)
    assert agent.last is not None and agent.last.sql == sql and agent.last.df is msgs[1]["df"]

    # «покажи то же самое иначе» работает в восстановленном чате
    agent.llm.replies += ['{"intent": "rechart", "chart_type": "pie"}',
                          '{"type": "pie", "x": "Тариф", "y": "Пользователей", "title": "Доли"}']
    r = agent.ask("А теперь покажи то же самое в другом виде")
    assert r.chart["type"] == "pie" and len(r.df) == 3


def test_hydrate_does_not_fail_on_broken_sql_or_missing_dashboard():
    db = make_db()
    h = ChatHistory(db)
    cid = h.create_chat()
    h.add_message(cid, "assistant", "старый ответ", kind="chart", sql='SELECT x FROM "нет_такой"',
                  chart={"type": "bar", "x": "a", "y": "b"})
    h.add_message(cid, "assistant", "дашборд", kind="dashboard", dashboard_id=99)
    msgs = hydrate(db, h.get_messages(cid))
    assert msgs[0]["stale"] and msgs[0]["df"] is None and msgs[0]["chart"] is None
    assert msgs[1]["stale"] and msgs[1]["dashboard"] is None


def test_deleted_dashboard_is_marked_stale_in_history():
    db = make_db()
    d1 = db.save_dashboard("А", {"title": "А", "widgets": []})
    d2 = db.save_dashboard("Б", {"title": "Б", "widgets": []})
    db.delete_dashboard(d1)
    assert db.get_dashboard(d1) is None and [d["id"] for d in db.list_dashboards()] == [d2]
    h = ChatHistory(db)
    cid = h.create_chat()
    h.add_message(cid, "assistant", "дашборд А", kind="dashboard", dashboard_id=d1)
    h.add_message(cid, "assistant", "дашборд Б", kind="dashboard", dashboard_id=d2)
    msgs = hydrate(db, h.get_messages(cid))
    assert msgs[0]["stale"] and msgs[0]["dashboard"] is None      # удалённый — пометка, без падения
    assert not msgs[1]["stale"] and msgs[1]["dashboard"]["title"] == "Б"


def test_restore_context_without_results_leaves_none():
    db = make_db()
    profiles, rels = profile_all(db)
    agent = Agent(db, ScriptedLLM([]), profiles, rels)
    restore_agent_context(agent, [{"role": "user", "text": "привет"},
                                  {"role": "assistant", "text": "Что показать?", "kind": "clarify"}])
    assert agent.last is None


def test_service_tables_are_closed_for_agent_sql():
    """Агент не должен читать чужие диалоги через SQL."""
    from core.safety import UnsafeSQL, validate_sql
    for t in ("_bi_chats", "_bi_messages"):
        try:
            validate_sql(f"SELECT * FROM {t}")
        except UnsafeSQL:
            continue
        raise AssertionError(f"агент получил доступ к {t}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\nВсе тесты history пройдены: {len(tests)}")
