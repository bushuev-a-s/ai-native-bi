# Быстрый запуск и частые проблемы

## Запуск (Windows, Linux, macOS)

Установка зависимостей и проверка ядра (через `uv`, добавил Саня):
```bash
uv sync
uv run python -m tests.test_core
uv run python -m tests.test_analytics
uv run python -m tests.test_loader   # после слияния fix/loader
```
Новая библиотека: `uv add <пакет>`. Не забудьте закоммитить `pyproject.toml` и `uv.lock`.

Полный запуск (нужны Docker для PostgreSQL и модели):
```bash
docker compose up -d db ollama
docker compose exec ollama ollama pull qwen2.5:7b-instruct
uv run streamlit run app.py        # http://localhost:8501
```
Приложение по умолчанию ищет БД на `localhost:5432` и модель на `localhost:11434`. Настройки в `.env` (образец `.env.example`).

## Частые проблемы

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

В `Dockerfile` из первого варианта зависимости ставятся из `requirements.txt`. Если файл удалили в пользу `pyproject.toml`,
сборка образа сломается. Два выхода:
1. Оставить `requirements.txt`, обновляя его командой `uv export --no-hashes -o requirements.txt`.
2. Переписать `Dockerfile` под `uv`:
```dockerfile
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev
COPY . .
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
EXPOSE 8501
CMD ["uv", "run", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
```
Перед этим убедитесь, что `streamlit`, `plotly`, `sqlalchemy`, `psycopg2-binary` есть в `pyproject.toml`.

## Как залить это обновление в репозиторий

```bash
git checkout dev && git pull
git checkout -b chore/plan-v2
# распакуйте bi-agent-update.zip в корень репозитория с заменой файлов
git add --renormalize .
git add .
git commit -m "docs: план v2, SETUP, .gitattributes, каркас analytics"
git push -u origin chore/plan-v2
```
Затем Pull Request в `dev`, другой участник нажимает Merge.
