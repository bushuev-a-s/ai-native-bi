"""Небольшой кеш результатов виджетов дашбордов. Интерфейс перерисовывается при каждом действии пользователя,
и без кеша каждый дашборд в истории заново гонял бы свои запросы по таблице.
Кеш сбрасывается при загрузке новых данных (Database.load_* вызывает bump) и по времени."""
import os
import threading
import time

TTL = int(os.getenv("DASHBOARD_CACHE_TTL", "300"))      # секунды
MAX_ENTRIES = 200

_lock = threading.Lock()
_data: dict = {}
_version = 0


def version() -> int:
    return _version


def key(db, sql: str) -> tuple:
    return (_version, id(db), sql)


def get(k):
    with _lock:
        item = _data.get(k)
        if item and time.time() - item[0] < TTL:
            return item[1]
        _data.pop(k, None)
        return None


def put(k, value) -> None:
    with _lock:
        if len(_data) >= MAX_ENTRIES:
            _data.pop(next(iter(_data)))
        _data[k] = (time.time(), value)


def bump() -> None:
    """Данные изменились: все сохранённые результаты недействительны."""
    global _version
    with _lock:
        _version += 1
        _data.clear()
