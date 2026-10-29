# Быстрый запуск и частые проблемы

## Запуск (Windows, Linux, macOS)

Установка зависимостей и проверка ядра (через `uv`, добавил Саня):
```bash
uv sync
bash run_tests.sh              # все тесты сразу (на Windows из Git Bash)
# по одному: uv run python -m tests.test_core
```
Новая библиотека: `uv add <пакет>`. Не забудьте закоммитить `pyproject.toml` и `uv.lock`.

Полный запуск в Docker:
```bash
cp .env.example .env     # заполнить модель
bash run.sh              # http://localhost:8501
```
`run.sh` выбирает режим по `LLM_BASE_URL`: внешний адрес (модель организаторов) → без Ollama; не задан → Ollama в контейнере
(`--gpu` / `--cpu` принудительно, `--remote` только БД и приложение).

Запуск приложения без Docker (БД и модель подняты отдельно):
```bash
docker compose up -d db
uv run streamlit run app.py        # http://localhost:8501
```
По умолчанию провайдер `openai` (OpenAI-совместимый API), БД на `localhost:5432`, модель на `http://localhost:11434/v1`.
Модель организаторов: в `.env` задать `LLM_BASE_URL` (с `/v1`), `LLM_MODEL`, `LLM_API_KEY`. Все переменные описаны в `.env.example`.

## Частые проблемы

**Индикатор модели красный.** Текст рядом подскажет причину: неверный `LLM_BASE_URL` (для openai обычно нужен `/v1`), неверное
имя `LLM_MODEL`, неверный `LLM_API_KEY` (ошибка 401). Если запущена локальная модель в docker compose, при первом запуске она
скачивается несколько минут: подождите и обновите страницу (`docker compose logs -f ollama-cpu`).

**Ответы обрываются на полуслове.** У некоторых серверов маленький лимит длины ответа по умолчанию: задайте `LLM_MAX_TOKENS=2048`.

**Скрипты `.sh` на Windows: `invalid option name: set: pipefail`.**
Причина: Git превратил переводы строк в Windows-формат (CRLF). Файл `.gitattributes` из этого обновления это исправляет.
После его добавления один раз выполните: `git add --renormalize .` и закоммитьте.
Запускать скрипты на Windows нужно из Git Bash: `"C:\Program Files\Git\bin\bash.exe" create_issues.sh`.

**WSL: «Конфигурация прокси-сервера localhost обнаружена, но не отражена в WSL».**
Это предупреждение про прокси. Если сеть работает, можно игнорировать; если нет, запускайте скрипты из Git Bash (см. выше).

**Linux: `Could not resolve host: github.com` при работе `git` и `gh`.**
Мешает IPv6. Временное исправление (до перезагрузки):
`sudo sysctl -w net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1`

**`ModuleNotFoundError: No module named 'pandas'`.** Не поставлены зависимости: выполните `uv sync` и запускайте через `uv run`.

## Docker и uv

`Dockerfile` ставит зависимости через `uv sync --frozen` из `pyproject.toml` и `uv.lock`, `requirements.txt` не нужен.
После `uv add` / `uv remove` обязательно коммитьте обновлённый `uv.lock`, иначе сборка образа упадёт.
В образ не попадают `.venv`, `.git`, `.env` и папка `data/` (см. `.dockerignore`).

## Как вносить изменения

Ветка от `dev` → правки → `bash run_tests.sh` → Pull Request в `dev`. Подробнее в `CONTRIBUTING.md`.
