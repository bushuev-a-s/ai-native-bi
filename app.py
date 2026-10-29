"""Streamlit-интерфейс: чат с агентом + вкладка с сохранёнными дашбордами."""
import pandas as pd
import streamlit as st

from core import config
from core.agent import Agent, execute_dashboard
from core.charts import make_figure
from core.db import Database
from core.history import ChatHistory, hydrate, restore_agent_context
from core.llm import LLMClient
from core.loader import load_file
from core.profiler import profile_all

st.set_page_config(page_title="AI BI Agent", page_icon="📊", layout="wide")


@st.cache_resource
def get_db():
    return Database()


@st.cache_resource
def get_llm():
    return LLMClient()


@st.cache_resource
def get_history():
    return ChatHistory(get_db())


db, llm = get_db(), get_llm()
ss = st.session_state
ss.setdefault("messages", [])
ss.setdefault("agent", None)
ss.setdefault("chat_id", None)       # id открытого чата в БД; None — новый чат (создаётся с первым вопросом)


def refresh_agent():
    with st.spinner("Изучаю данные…"):
        profiles, relations = profile_all(db)
    ss.agent = Agent(db, llm, profiles, relations)
    if ss.messages:                                   # открыт старый чат — вернуть агенту контекст
        restore_agent_context(ss.agent, ss.messages)


# --- история диалогов (callbacks берут историю через get_history(), а не из глобальных переменных) ---
def new_chat():
    ss.chat_id, ss.messages = None, []
    if ss.agent:
        ss.agent.last = None


def open_chat(chat_id: int):
    ss.chat_id = chat_id
    ss.messages = hydrate(get_db(), get_history().get_messages(chat_id))   # таблицы пересчитываются по SQL
    if ss.agent:
        restore_agent_context(ss.agent, ss.messages)


def delete_chat(chat_id: int):
    get_history().delete_chat(chat_id)
    if ss.chat_id == chat_id:
        new_chat()


def delete_dashboard(dash_id: int):
    get_db().delete_dashboard(dash_id)


def save_message(role: str, text: str, **extra):
    """Сохраняет реплику в историю; сбой записи не должен ломать сам диалог."""
    try:
        if ss.chat_id is None:
            ss.chat_id = get_history().create_chat()
        get_history().add_message(ss.chat_id, role, text, **extra)
    except Exception as e:  # noqa: BLE001
        st.toast(f"История не сохранилась: {str(e)[:100]}", icon="⚠️")


def render_chart(df: pd.DataFrame, spec: dict | None, key: str):
    if spec:
        try:
            st.plotly_chart(make_figure(df, spec), use_container_width=True, key=key)
        except Exception as e:  # noqa: BLE001
            st.caption(f"Не удалось построить график ({e}). Показываю таблицу.")
            st.dataframe(df, use_container_width=True)


def render_dashboard(spec: dict, prefix: str):
    st.subheader(spec["title"])
    widgets = execute_dashboard(db, spec)        # данные всегда свежие
    cols = st.columns(2)
    for i, w in enumerate(widgets):
        with cols[i % 2]:
            st.markdown(f"**{w['title']}**")
            if w["error"]:
                st.warning("Виджет не загрузился: " + w["error"])
            else:
                render_chart(w["df"], w["chart"], f"{prefix}-w{i}")
                with st.expander("Данные"):
                    st.dataframe(w["df"], use_container_width=True)


def render_message(m: dict, idx: int):
    with st.chat_message(m["role"]):
        st.markdown(m["text"])
        if m.get("stale"):
            st.caption("⚠️ Это сообщение из истории: данные по нему не удалось пересчитать "
                       "(таблица или дашборд изменились или удалены).")
        if m.get("dashboard"):
            render_dashboard(m["dashboard"], f"msg{idx}")
        if m.get("chart") is not None and m.get("df") is not None:
            render_chart(m["df"], m["chart"], f"msg{idx}-chart")
        if m.get("df") is not None and not m["df"].empty and not m.get("dashboard"):
            with st.expander("Таблица с данными"):
                st.dataframe(m["df"], use_container_width=True)
        if m.get("sql"):
            with st.expander("Как посчитано (SQL)"):
                st.code(m["sql"], language="sql")
        if m.get("analysis"):
            with st.expander("📊 Детальный анализ данных"):
                a = m["analysis"]
                
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Дубликаты строк", a["duplicates"]["duplicate_rows"])
                    st.metric("Столбцов с аномалиями", len(a["anomalies"]))
                    
                with col2:
                    if a.get("seasonality", {}).get("is_seasonal"):
                        st.success("✅ Обнаружена сезонность")
                    else:
                        st.info("⚪ Сезонность не выражена")
                        
                if a["correlations"].get("strong_pairs"):
                    st.subheader("🔗 Сильные корреляции")
                    for pair in a["correlations"]["strong_pairs"]:
                        st.caption(f"`{pair['col1']}` ↔ `{pair['col2']}` : **{pair['correlation']}**")
                        
                if a.get("lags", {}).get("values"):
                    st.subheader("⏳ Автокорреляция (Лаги)")
                    st.json(a["lags"]["values"])


# ------------------------------------------------------------------ боковая панель
with st.sidebar:
    st.title("📊 AI BI Agent")
    db_ok = db.ping()
    llm_ok, llm_msg = llm.available()
    st.markdown(f"{'🟢' if db_ok else '🔴'} PostgreSQL  \n{'🟢' if llm_ok else '🔴'} Модель: `{llm_msg}`")
    if not db_ok:
        st.error("Нет подключения к БД. Проверьте DATABASE_URL и docker compose up -d db")
        st.stop()

    hist = get_history()
    st.divider()
    st.caption("История диалогов")
    st.button("➕ Новый чат", on_click=new_chat, use_container_width=True)
    for c in hist.list_chats()[:30]:
        col_open, col_del = st.columns([5, 1])
        col_open.button(c["title"], key=f"chat-open-{c['id']}", on_click=open_chat, args=(c["id"],),
                        use_container_width=True, type="primary" if c["id"] == ss.chat_id else "secondary",
                        help=f"{c['updated_at'][:16]} · сообщений: {c['messages']}")
        col_del.button("🗑", key=f"chat-del-{c['id']}", on_click=delete_chat, args=(c["id"],),
                       help="Удалить чат")

    st.divider()
    files = st.file_uploader("Загрузить CSV (сырые данные)", type=["csv"], accept_multiple_files=True)
    if files and st.button("Загрузить в базу", type="primary"):
        done = []
        for f in files:
            with st.spinner(f"Загружаю {f.name} ({f.size / 1024 / 1024:.1f} МБ)…"):
                try:
                    table, rows, method = load_file(db, f, f.name, size=f.size)
                except Exception as e:  # noqa: BLE001 — один плохой файл не должен останавливать остальные
                    st.error(f"{f.name}: не удалось загрузить — {str(e)[:200]}")
                    continue
            done.append(f"• **{table}** — {rows:,} строк".replace(",", " ")
                        + (" (быстрая загрузка COPY)" if method == "copy" else ""))
        if done:
            refresh_agent()
            st.success("Загружено:\n\n" + "\n".join(done))
    if st.button("Обновить профиль данных"):
        refresh_agent()
    if st.button(" Обновить глубокий анализ"):
        refresh_agent()
        st.rerun()

    if ss.agent is None and db.tables():
        refresh_agent()
    if ss.agent:
        st.divider()
        st.caption("Таблицы в базе")
        for p in ss.agent.profiles:
            st.write(f"• **{p['name']}** — {p['rows']:,} строк".replace(",", " "))

# ------------------------------------------------------------------ вкладки
tab_chat, tab_dash = st.tabs(["💬 Чат с аналитиком", "📈 Дашборды"])

with tab_chat:
    if not ss.messages:
        st.info("Загрузите CSV слева и спросите, например: **«Что есть в этих данных?»**, "
                "**«Сколько пользователей на каждом тарифе?»**, **«Создай дашборд по платежам»**.")
    for i, m in enumerate(ss.messages):
        render_message(m, i)

    if prompt := st.chat_input("Задайте вопрос о данных…"):
        ss.messages.append({"role": "user", "text": prompt})
        save_message("user", prompt)
        render_message(ss.messages[-1], len(ss.messages) - 1)
        if ss.agent is None:
            res_msg = {"role": "assistant", "text": "Сначала загрузите данные (CSV) в боковой панели.",
                       "kind": "clarify"}
            save_message("assistant", res_msg["text"], kind="clarify")
        else:
            history = [{"role": m["role"],
                        "content": m["text"] + (f"\n[SQL: {m['sql']}]" if m.get("sql") else "")}
                       for m in ss.messages[:-1]]
            with st.spinner("Думаю…"):
                res = ss.agent.ask(prompt, history)
            res_msg = {"role": "assistant", "text": res.text, "sql": res.sql, "df": res.df,
                       "chart": res.chart, "dashboard": res.dashboard, "kind": res.kind}
            save_message("assistant", res.text, kind=res.kind, sql=res.sql, chart=res.chart,
                         dashboard_id=res.dashboard_id)
        ss.messages.append(res_msg)
        st.rerun()

with tab_dash:
    dashes = db.list_dashboards()
    if not dashes:
        st.info("Дашбордов пока нет. Попросите агента в чате: «Создай дашборд с ежемесячной статистикой …»")
    else:
        choice = st.selectbox("Сохранённые дашборды", dashes,
                              format_func=lambda d: f"#{d['id']} · {d['title']} · {d['created_at']}")
        spec = db.get_dashboard(choice["id"])
        if spec:
            with st.popover("🗑 Удалить дашборд"):
                st.write(f"Удалить дашборд «{choice['title']}»? Это действие нельзя отменить.")
                st.button("Да, удалить", type="primary", key=f"dash-del-{choice['id']}",
                          on_click=delete_dashboard, args=(choice["id"],))
            render_dashboard(spec, f"saved{choice['id']}")
