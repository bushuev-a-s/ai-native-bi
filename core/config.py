"""Настройки платформы. Всё переопределяется переменными окружения (см. .env.example)."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://bi:bi@localhost:5432/bi")

# --- LLM -------------------------------------------------------------------------------
# По умолчанию OpenAI-совместимый API: так подключается модель организаторов (URL + ключ), так же работают
# vLLM, LM Studio, llama.cpp server и локальный Ollama (через /v1).
#   LLM_PROVIDER=openai  LLM_BASE_URL=https://<хост>/v1  LLM_MODEL=<имя>  LLM_API_KEY=<ключ>
# Родной API Ollama (LLM_PROVIDER=ollama) оставлен для случая, когда нужно управлять LLM_NUM_CTX / LLM_KEEP_ALIVE
# со стороны клиента; у /v1 эти параметры задаются на сервере (OLLAMA_CONTEXT_LENGTH, OLLAMA_KEEP_ALIVE).
# Старые переменные OLLAMA_URL и OLLAMA_MODEL больше не читаются.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or (
    "http://localhost:11434" if LLM_PROVIDER == "ollama" else "http://localhost:11434/v1")
LLM_MODEL = os.getenv("LLM_MODEL") or "qwen2.5:7b-instruct"
LLM_API_KEY = os.getenv("LLM_API_KEY") or "not-needed"

MAX_ROWS = int(os.getenv("MAX_ROWS", "1000"))             # максимум строк в ответе запроса
QUERY_TIMEOUT_MS = int(os.getenv("QUERY_TIMEOUT_MS", "15000"))
PROFILE_SAMPLE_ROWS = int(os.getenv("PROFILE_SAMPLE_ROWS", "100000"))
RELATION_SAMPLE_VALUES = 50000
SQL_RETRIES = 2                                           # сколько раз LLM может исправить свой SQL
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "4"))       # сколько последних реплик идёт в контекст

# --- скорость и поведение агента -----------------------------------------------------------
# ROUTER_MODE=rules  намерение определяется правилами (без вызова модели); llm — как раньше, отдельный вызов модели
# NARRATION=auto     одиночные значения пересказываются шаблоном без модели; llm — всегда моделью; template — никогда моделью
# CHART_LLM=0        тип графика выбирается правилами; 1 — спрашивать модель
ROUTER_MODE = os.getenv("ROUTER_MODE", "rules").lower()
NARRATION = os.getenv("NARRATION", "auto").lower()
CHART_LLM = os.getenv("CHART_LLM", "0").lower() in ("1", "true", "yes")
SQL_MAX_TOKENS = int(os.getenv("SQL_MAX_TOKENS", "1024"))          # у моделей с «размышлениями» поднимите или отключите размышления
NARRATE_MAX_TOKENS = int(os.getenv("NARRATE_MAX_TOKENS", "220"))
OVERVIEW_MAX_TOKENS = int(os.getenv("OVERVIEW_MAX_TOKENS", "300"))
DASHBOARD_MAX_TOKENS = int(os.getenv("DASHBOARD_MAX_TOKENS", "1600"))
DASHBOARD_MAX_WIDGETS = int(os.getenv("DASHBOARD_MAX_WIDGETS", "4"))
