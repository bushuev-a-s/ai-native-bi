# Быстрый запуск и частые проблемы

## Запуск (Windows, Linux, macOS)

Установка зависимостей и проверка ядра (через `uv`, добавил Саня):
```bash
uv sync
bash run_tests.sh              # все тесты сразу (на Windows из Git Bash)
# по одному: uv run python -m tests.test_core
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