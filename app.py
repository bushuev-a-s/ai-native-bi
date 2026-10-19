"""Streamlit-интерфейс: чат с агентом + вкладка с сохранёнными дашбордами."""
import pandas as pd
import streamlit as st

from core import config
from core.agent import Agent, execute_dashboard
from core.charts import make_figure
from core.db import Database
from core.llm import LLMClient
from core.loader import read_csv_smart, table_name_from_file
from core.profiler import profile_all

st.set_page_config(page_title="AI BI Agent", page_icon="📊", layout="wide")


@st.cache_resource
def get_db():
    return Database()


@st.cache_resource
def get_llm():
    return LLMClient()


db, llm = get_db(), get_llm()
ss = st.session_state
ss.setdefault("messages", [])
ss.setdefault("agent", None)


def refresh_agent():
    with st.spinner("Изучаю данные…"):
        profiles, relations = profile_all(db)
    ss.agent = Agent(db, llm, profiles, relations)


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

    st.divider()
    files = st.file_uploader("Загрузить CSV (сырые данные)", type=["csv"], accept_multiple_files=True)
    if files and st.button("Загрузить в базу", type="primary"):
        for f in files:
            with st.spinner(f"Загружаю {f.name}…"):
                db.load_dataframe(read_csv_smart(f), table_name_from_file(f.name))
        refresh_agent()
        st.success(f"Загружено файлов: {len(files)}")
    if st.button("Обновить профиль данных"):
        refresh_agent()
    if st.button("Очистить диалог"):
        ss.messages = []
        if ss.agent:
            ss.agent.last = None
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
        render_message(ss.messages[-1], len(ss.messages) - 1)
        if ss.agent is None:
            res_msg = {"role": "assistant", "text": "Сначала загрузите данные (CSV) в боковой панели."}
        else:
            history = [{"role": m["role"],
                        "content": m["text"] + (f"\n[SQL: {m['sql']}]" if m.get("sql") else "")}
                       for m in ss.messages[:-1]]
            with st.spinner("Думаю…"):
                res = ss.agent.ask(prompt, history)
            res_msg = {"role": "assistant", "text": res.text, "sql": res.sql, "df": res.df,
                       "chart": res.chart, "dashboard": res.dashboard}
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
            render_dashboard(spec, f"saved{choice['id']}")
