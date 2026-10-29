"""Запуск: python -m tests.test_sniff. Определение разделителя CSV и удаление таблицы (без Postgres)."""
import sys
import types

from core.loader import _sniff_sep, load_file, read_csv_smart

try:
    import sqlalchemy  # noqa: F401
except ImportError:                                    # окружение без SQLAlchemy: подставляем заглушку для импорта core.db
    stub = types.ModuleType("sqlalchemy")
    stub.create_engine = stub.inspect = None
    stub.text = lambda s: s
    sys.modules["sqlalchemy"] = stub
from core.db import Database  # noqa: E402


def test_basic_separators():
    assert _sniff_sep("a,b,c\n1,2,3\n4,5,6\n") == ","
    assert _sniff_sep("a;b;c\n1;2;3\n4;5;6\n") == ";"
    assert _sniff_sep("a\tb\tc\n1\t2\t3\n") == "\t"
    assert _sniff_sep("a|b|c\n1|2|3\n") == "|"


def test_semicolon_with_decimal_comma():
    """Десятичная запятая не должна перебивать точку с запятой."""
    text = "id;сумма;город\n1;1,5;Москва\n2;22,75;Тверь\n3;3;Казань\n4;4,25;Омск\n"
    assert _sniff_sep(text) == ";"


def test_semicolon_with_commas_in_every_text_value():
    text = "имя;комментарий\n" + "".join(f"u{i};хорошо, но дорого\n" for i in range(20))
    assert _sniff_sep(text) == ";"


def test_comma_with_semicolons_inside_quotes():
    text = 'id,note,x\n1,"a;b;c",5\n2,"d;e",6\n3,"f;g;h;i",7\n'
    assert _sniff_sep(text) == ","


def test_comma_file_with_free_text_containing_semicolon():
    text = "id,text,n\n" + "".join(f"{i},a;b;c;d,{i}\n" for i in range(10))
    assert _sniff_sep(text) == ","


def test_first_line_is_not_decisive():
    """Заголовок с запятой в названии колонки (в кавычках) и данные через ;."""
    text = '"Сумма, руб";"Город"\n10;Москва\n20;Тверь\n30;Омск\n'
    assert _sniff_sep(text) == ";"


def test_title_lines_above_the_table_do_not_decide():
    """Выгрузки из 1С/Excel: над таблицей строки-«шапка» с запятыми. Прежний алгоритм (csv.Sniffer) тут ошибался."""
    rows = "".join(f"{i};текст;{i},5\n" if i % 3 == 0 else f"{i};текст;{i}\n" for i in range(30))
    text = "Отчёт о продажах, 2024\nСформирован: 01.02.2024, автор: Иванов\nid;t;n\n" + rows
    assert _sniff_sep(text) == ";"
    tab = "Отчёт, 2024\nid\tp\tq\n" + "".join(f"{i}\ta, b\tc\n" for i in range(30))
    assert _sniff_sep(tab) == "\t"


def test_ragged_rows_with_title_line():
    """Часть строк с лишним полем + строка-заголовок отчёта: берём разделитель, дающий самую ровную таблицу."""
    rows = "".join(f"{i};a;b" + (";c" if i % 7 == 0 else "") + "\n" for i in range(40))
    assert _sniff_sep("Итоги, за год\nid;p;q\n" + rows) == ";"


def test_excel_sep_hint_line():
    assert _sniff_sep("sep=;\nid;t\n1;a\n2;b\n") == ";"


def test_single_column_falls_back_to_comma():
    assert _sniff_sep("name\nAnna\nBoris\n") == ","
    assert _sniff_sep("") == ","


def test_truncated_sample_does_not_break_choice():
    row = "1;абв,где;3\n"
    text = "a;b;c\n" + row * 20000                     # длиннее выборки: последняя строка выборки обрезана
    assert _sniff_sep(text) == ";"


def test_manual_separator_overrides_detection():
    data = "a;b\n1;2\n".encode()
    assert list(read_csv_smart(data).columns) == ["a", "b"]
    assert list(read_csv_smart(data, sep=",").columns) == ["a_b"]      # принудительно «не тот» — ровно то, что просили


def test_load_file_passes_sep_only_when_given():
    calls = []

    class DB:
        def load_csv(self, source, table, *a, **kw):
            calls.append(kw)
            return 1

    db, data = DB(), b"a;b\n1;2\n"
    load_file(db, data, "t.csv", threshold_mb=0.000001)
    load_file(db, data, "t.csv", threshold_mb=0.000001, sep=";")
    assert calls == [{}, {"sep": ";"}]


class _Conn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *a):
        self.log.append(str(sql))


class _Engine:
    def __init__(self):
        self.log = []

    def begin(self):
        return _Conn(self.log)


def _db(tables):
    db = Database.__new__(Database)
    db.engine, db.tables = _Engine(), (lambda: tables)
    return db


def test_drop_table_only_drops_loaded_tables():
    db = _db(["users", "events"])
    db.drop_table("users")
    assert db.engine.log == ['DROP TABLE "users"']
    for bad in ("_bi_chats", "_bi_dashboards", 'users"; DROP TABLE events; --', "нет_такой"):
        try:
            db.drop_table(bad)
        except ValueError:
            continue
        raise AssertionError(f"удалилось лишнее: {bad}")
    assert db.engine.log == ['DROP TABLE "users"']


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\nВсе тесты sniff пройдены: {len(tests)}")
