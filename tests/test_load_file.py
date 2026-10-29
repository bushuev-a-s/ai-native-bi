"""Запуск: python -m tests.test_load_file. Выбор способа загрузки по размеру файла (без Postgres)."""
import io

from core import config
from core.loader import load_file

CSV = "id;сумма\n1;1,5\n2;2,5\n3;3,5\n".encode("utf-8")


class RecordingDB:
    """Запоминает, каким методом грузили."""
    def __init__(self):
        self.calls = []

    def load_dataframe(self, df, table):
        self.calls.append(("dataframe", table, len(df)))

    def load_csv(self, source, table):
        self.calls.append(("copy", table, None))
        return 3


class FakeUpload(io.BytesIO):
    """Как UploadedFile из Streamlit: файловый объект с атрибутом size."""
    def __init__(self, data, size=None):
        super().__init__(data)
        self.size = len(data) if size is None else size


def test_small_file_goes_through_dataframe():
    db = RecordingDB()
    table, rows, method = load_file(db, FakeUpload(CSV), "01_Продажи.csv")
    assert (method, rows) == ("dataframe", 3) and db.calls == [("dataframe", table, 3)]


def test_big_file_goes_through_copy():
    db = RecordingDB()
    big = FakeUpload(CSV, size=int(config.FAST_LOAD_MIN_MB * 1024 * 1024))     # ровно порог — уже COPY
    table, rows, method = load_file(db, big, "sales.csv")
    assert (table, rows, method) == ("sales", 3, "copy") and db.calls == [("copy", "sales", None)]


def test_threshold_can_be_overridden():
    db = RecordingDB()
    assert load_file(db, CSV, "t.csv", threshold_mb=0.000001)[2] == "copy"     # bytes, порог ~1 байт
    assert load_file(db, CSV, "t.csv", threshold_mb=1000)[2] == "dataframe"


def test_size_is_taken_from_bytes_path_and_object(tmp_path=None):
    import os
    import tempfile
    db = RecordingDB()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.csv")
        open(p, "wb").write(CSV)
        assert load_file(db, p, "x.csv", threshold_mb=0.000001)[2] == "copy"    # путь к файлу
        assert load_file(db, p, "x.csv", threshold_mb=1000)[2] == "dataframe"


def test_file_pointer_is_rewound():
    """Файл, который уже читали (указатель в конце), всё равно грузится целиком."""
    db = RecordingDB()
    f = FakeUpload(CSV)
    f.read()
    assert load_file(db, f, "t.csv")[1] == 3


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\nВсе тесты load_file пройдены: {len(tests)}")
