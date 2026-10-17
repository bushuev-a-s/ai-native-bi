"""Настройки платформы. Всё переопределяется переменными окружения (см. .env.example)."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://bi:bi@localhost:5432/bi")

# --- LLM: любой сервер, локальный и open source -------------------------------------
# LLM_PROVIDER=ollama  -> LLM_BASE_URL=http://localhost:11434
# LLM_PROVIDER=openai  -> OpenAI-совместимый API (vLLM, LM Studio, llama.cpp server, Ollama /v1):
#                         LLM_BASE_URL=http://localhost:8000/v1
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-needed")

MAX_ROWS = int(os.getenv("MAX_ROWS", "1000"))             # максимум строк в ответе запроса
QUERY_TIMEOUT_MS = int(os.getenv("QUERY_TIMEOUT_MS", "15000"))
PROFILE_SAMPLE_ROWS = int(os.getenv("PROFILE_SAMPLE_ROWS", "100000"))
RELATION_SAMPLE_VALUES = 50000
SQL_RETRIES = 2                                           # сколько раз LLM может исправить свой SQL
HISTORY_TURNS = 6                                         # сколько последних реплик идёт в контекст
