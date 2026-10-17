"""Генерирует тестовые CSV (users, events, payments), связанные только через user_id.
Есть сезонность (просадка платежей в июле), дубликаты и пропуски — как в «грязных» данных."""
import os

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)
out = os.path.join(os.path.dirname(__file__), "..", "sample_data")
os.makedirs(out, exist_ok=True)

N = 2000
users = pd.DataFrame({
    "user_id": np.arange(1, N + 1),
    "signup_date": pd.to_datetime("2023-01-01") + pd.to_timedelta(rng.integers(0, 700, N), unit="D"),
    "plan": rng.choice(["basic", "pro", "enterprise"], N, p=[0.6, 0.3, 0.1]),
    "country": rng.choice(["RU", "KZ", "BY", "DE", "US"], N, p=[0.5, 0.2, 0.1, 0.1, 0.1]),
    "role": rng.choice(["analyst", "manager", "developer"], N),
})
users.loc[rng.choice(N, 80, replace=False), "country"] = np.nan

M = 20000
ev_users = rng.choice(users["user_id"], M)
events = pd.DataFrame({
    "event_id": np.arange(1, M + 1),
    "user_id": ev_users,
    "ts": pd.to_datetime("2023-01-01") + pd.to_timedelta(rng.integers(0, 730 * 24 * 60, M), unit="m"),
    "event_type": rng.choice(["view", "click", "search", "export"], M, p=[0.5, 0.3, 0.15, 0.05]),
})

P = 6000
months = pd.date_range("2023-01-01", "2024-12-01", freq="MS")
w = np.array([0.4 if m.month == 7 else (1.6 if m.month == 12 else 1.0) for m in months])
paid_month = rng.choice(months, P, p=w / w.sum())
payments = pd.DataFrame({
    "payment_id": np.arange(1, P + 1),
    "user_id": rng.choice(users["user_id"], P),
    "paid_at": paid_month + pd.to_timedelta(rng.integers(0, 28, P), unit="D"),
    "amount": np.round(rng.gamma(2.0, 40.0, P), 2),
    "status": rng.choice(["paid", "refunded", "failed"], P, p=[0.88, 0.05, 0.07]),
})
payments = pd.concat([payments, payments.sample(30, random_state=1)], ignore_index=True)  # дубликаты

users.to_csv(os.path.join(out, "users.csv"), index=False)
events.to_csv(os.path.join(out, "events.csv"), index=False)
payments["paid_at"] = payments["paid_at"].dt.strftime("%d.%m.%Y")        # даты в «неудобном» формате
payments.to_csv(os.path.join(out, "payments.csv"), index=False, sep=";")  # другой разделитель
print("OK:", os.path.abspath(out))
