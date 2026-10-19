"""Запуск: python -m tests.test_analytics  (или uv run python -m tests.test_analytics)."""
from core.analytics import TOOLS, find_duplicates
from tests.test_core import make_db


def test_find_duplicates_on_sample():
    db = make_db()
    r = find_duplicates(db, "payments")
    assert "30" in r.summary and not r.df.empty                  # в тестовых данных 30 дубликатов
    assert find_duplicates(db, "users").df.empty                  # в users дубликатов нет


def test_tools_registry_has_descriptions():
    for name, t in TOOLS.items():
        assert callable(t["fn"]) and t["description"], name


if __name__ == "__main__":
    test_find_duplicates_on_sample()
    test_tools_registry_has_descriptions()
    print("Все тесты analytics пройдены")
