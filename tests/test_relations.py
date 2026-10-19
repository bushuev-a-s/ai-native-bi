"""Связи между таблицами: не должно быть ложных связей между числовыми рядами 1..N.
Запуск: uv run python -m tests.test_relations"""
import pandas as pd

from core.profiler import find_relations, profile_all
from tests.fakes import SqliteDB
from tests.test_core import make_db


def _pairs(rels):
    return {frozenset([(r["a"], r["a_col"]), (r["b"], r["b_col"])]) for r in rels}


def test_sample_data_has_only_real_relations():
    _, rels = profile_all(make_db())
    assert _pairs(rels) == {
        frozenset([("users", "user_id"), ("events", "user_id")]),
        frozenset([("users", "user_id"), ("payments", "user_id")]),
        frozenset([("events", "user_id"), ("payments", "user_id")]),
    }, "event_id/payment_id не должны связываться с user_id"


def test_generic_id_columns_are_not_linked():
    db = SqliteDB()
    db.load_dataframe(pd.DataFrame({"id": range(1, 101), "x": range(100)}), "a")
    db.load_dataframe(pd.DataFrame({"id": range(1, 101), "y": range(100)}), "b")
    profiles, _ = profile_all(db)
    assert find_relations(db, profiles) == []


def test_table_name_hint_links_id_to_foreign_key():
    db = SqliteDB()
    db.load_dataframe(pd.DataFrame({"id": range(1, 101), "name": [f"u{i}" for i in range(100)]}), "users")
    db.load_dataframe(pd.DataFrame({"order_id": range(1, 501), "user_id": [i % 100 + 1 for i in range(500)]}), "orders")
    profiles, rels = profile_all(db)
    assert _pairs(rels) == {frozenset([("users", "id"), ("orders", "user_id")])}


if __name__ == "__main__":
    for t in (test_sample_data_has_only_real_relations, test_generic_id_columns_are_not_linked,
              test_table_name_hint_links_id_to_foreign_key):
        t()
        print("PASS", t.__name__)
    print("Все тесты связей пройдены")
