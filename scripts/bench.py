"""Замер скорости: прогоняет вопросы через агента и печатает время по этапам.
Нужны PostgreSQL с загруженными данными и модель (настройки из окружения / .env, как у приложения).

  uv run python scripts/bench.py                      вопросы по умолчанию
  uv run python scripts/bench.py "Вопрос 1" "Вопрос 2"
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DEFAULT_QUESTIONS = [
    "Что передо мной?",
    "Сколько строк в каждой таблице?",
    "Создай дашборд по основным показателям",
    "Построй график",
]


def main(questions: list[str]) -> None:
    from core.agent import Agent
    from core.db import Database
    from core.llm import LLMClient
    from core.profiler import profile_all

    db, llm = Database(), LLMClient()
    ok, msg = llm.available()
    print(f"Модель: {msg} ({'доступна' if ok else 'НЕДОСТУПНА'})")
    t = time.perf_counter()
    profiles, relations = profile_all(db)
    print(f"Профиль данных: {time.perf_counter() - t:.1f} с, таблиц: {len(profiles)}\n")

    agent = Agent(db, llm, profiles, relations)
    history: list[dict] = []
    print(f"{'вопрос':<46} {'итого':>7} {'модель':>8} {'вызовов':>8} {'БД':>6}  вид")
    for q in questions:
        res = agent.ask(q, history)
        tm = res.timing
        print(f"{q[:45]:<46} {tm['total_s']:>6.1f}с {tm['llm_s']:>7.1f}с {tm['llm_calls']:>8} {tm['db_s']:>5.1f}с  {res.kind}")
        history += [{"role": "user", "content": q}, {"role": "assistant", "content": res.text}]


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT_QUESTIONS)
