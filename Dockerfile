# 1. Используем образ с Python 3.14 (slim)
FROM python:3.14-slim

# Копируем uv напрямую из официального образа
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Для Python 3.14 компиляторам часто нужны базовые C-библиотеки сборки,
# если для какой-то зависимости еще нет собранного wheel-пакета на PyPI
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Окружение: байт-код, копирование ссылок, переменные Streamlit и PATH
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=python3.14 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PATH="/app/.venv/bin:$PATH"

# 2. Манифесты зависимостей
COPY pyproject.toml uv.lock ./

# 3. Синхронизация зависимостей с кэшированием
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 4. Исходный код
COPY . .

# 5. Установка проекта
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]