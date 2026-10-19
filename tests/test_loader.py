"""Тесты загрузчика: десятичная запятая и потоковая загрузка через COPY (на подмене Postgres).
Запуск: uv run python -m tests.test_loader"""
import io
import os

import pandas as pd

from core.loader import load_csv_to_postgres, read_csv_smart

DATA = os.path.join(os.path.dirname(__file__), "..", "sample_data")


# ----------------------------------------------------------------- подмена Postgres
class _Cur:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql):
        self.conn.log.append(sql)

    def copy_expert(self, sql, buf):
        if self.conn.fail_on_copy and self.conn.copies:
            raise RuntimeError("упало на втором чанке")
        self.conn.copies.append(buf.read())


class _Conn:
    def __init__(self, fail_on_copy=False):
        self.log, self.copies, self.fail_on_copy = [], [], fail_on_copy
        self.committed = self.rolled = self.closed = False

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled = True

    def close(self):
        self.closed = True


class _Engine:
    def __init__(self, **kw):
        self.conn = _Conn(**kw)

    def raw_connection(self):
        return self.conn


def _load(source, chunksize, **kw):
    e = _Engine(**kw)
    n = load_csv_to_postgres(source, e, "t", chunksize=chunksize)
    return e.conn, n


def _rows(conn, names):
    return pd.concat([pd.read_csv(io.StringIO(c), header=None, names=names, keep_default_na=False,
                                  na_values=[""], dtype=str) for c in conn.copies], ignore_index=True)


def _ddl(conn):
    return [s for s in conn.log if s.startswith(("CREATE", "ALTER"))]


# ----------------------------------------------------------------- десятичная запятая
def test_decimal_comma():
    df = read_csv_smart("a;b\n117,2;x\n3,5;y\n10;z\n".encode())
    assert df["a"].dtype == "float64" and df["a"].tolist() == [117.2, 3.5, 10.0]
    assert read_csv_smart('t,n\n"a, b",1\n"c, d",2\n'.encode())["t"].tolist() == ["a, b", "c, d"]
    assert read_csv_smart("w;k\n0,125;1\n0,5;2\n".encode())["w"].tolist() == [0.125, 0.5]


def test_thousands_separator_is_not_guessed():
    # '1,234' — это 1234 или 1.234? Не гадаем: оставляем текстом, а не портим числа молча
    df = read_csv_smart('n,price\nA,"1,234"\nB,"2,500"\n'.encode())
    assert df["price"].tolist() == ["1,234", "2,500"]


# ----------------------------------------------------------------- потоковая загрузка
def test_chunked_equals_full_read():
    path = os.path.join(DATA, "payments.csv")
    conn, n = _load(path, 1000)
    ref = read_csv_smart(path)
    assert n == len(ref) == 6030 and conn.committed and conn.closed
    assert "TIMESTAMP" in _ddl(conn)[0] and "DOUBLE PRECISION" in _ddl(conn)[0]
    got = _rows(conn, list(ref.columns))
    assert (pd.to_datetime(got["paid_at"]).values == ref["paid_at"].values).all()
    assert (got["amount"].astype(float).round(2).values == ref["amount"].round(2).values).all()


def test_text_after_numbers_widens_instead_of_nulling():
    txt = "id,v\n" + "\n".join(f"{i},{i}" for i in range(1, 6)) + "\n6,A7\n7,B8\n8,9\n"
    conn, _ = _load(txt.encode(), 5)
    assert any('ALTER COLUMN "v" TYPE TEXT' in s for s in _ddl(conn))
    assert _rows(conn, ["id", "v"])["v"].tolist()[-3:] == ["A7", "B8", "9"]      # ничего не потеряно


def test_fraction_in_integer_column_widens_not_rounds():
    txt = "id,v\n" + "\n".join(f"{i},{i}" for i in range(1, 6)) + "\n6,1.5\n"
    conn, _ = _load(txt.encode(), 5)
    assert any('TYPE DOUBLE PRECISION' in s for s in _ddl(conn))
    assert float(_rows(conn, ["id", "v"])["v"].iloc[-1]) == 1.5


def test_empty_first_chunk_column_keeps_later_text():
    txt = "id,note\n" + "\n".join(f"{i}," for i in range(1, 6)) + "\n6,важно\n7,срочно\n"
    conn, _ = _load(txt.encode(), 5)
    assert _rows(conn, ["id", "note"])["note"].tolist()[-2:] == ["важно", "срочно"]


def test_iso_date_is_not_day_month_swapped():
    txt = "id,d\n" + "\n".join(f"{i},0{i}.03.2024" for i in range(1, 6)) + "\n6,2024-03-05\n7,unknown\n"
    conn, _ = _load(txt.encode(), 5)
    assert _rows(conn, ["id", "d"])["d"].iloc[5].startswith("2024-03-05")        # не 2024-05-03


def test_cp1251_detected_beyond_first_megabyte():
    body = ("id,name\n" + "".join(f"{i},abc\n" for i in range(1, 140000))).encode()
    body += "999999,Иван\n".encode("cp1251")
    assert len(body) > (1 << 20)
    conn, n = _load(body, 50000)
    assert n == 140000 and "Иван" in conn.copies[-1]


def test_special_characters_survive():
    txt = 'id,t\n1,"a, b"\n2,"say ""hi"""\n3,"line1\nline2"\n'
    conn, _ = _load(txt.encode(), 2)
    got = pd.concat([pd.read_csv(io.StringIO(c), header=None, names=["id", "t"]) for c in conn.copies])
    assert got["t"].tolist() == ["a, b", 'say "hi"', "line1\nline2"]


def test_failure_rolls_back():
    e = _Engine(fail_on_copy=True)
    try:
        load_csv_to_postgres(b"id,v\n1,1\n2,2\n3,3\n", e, "t", chunksize=1)
    except RuntimeError:
        pass
    else:
        raise AssertionError("ожидали ошибку")
    assert e.conn.rolled and not e.conn.committed and e.conn.closed


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\nВсе тесты loader пройдены: {len(tests)}")
