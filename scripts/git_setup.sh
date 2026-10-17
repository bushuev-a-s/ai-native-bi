#!/usr/bin/env bash
# Использование: bash scripts/git_setup.sh https://github.com/<логин>/<репозиторий>.git
# Создаёт репозиторий, первый коммит и ветки main/dev. Токены сюда не вставляйте —
# GitHub спросит авторизацию при push (токен или SSH-ключ).
set -euo pipefail
REMOTE="${1:?Укажите URL пустого репозитория GitHub}"
git init
git add .
git commit -m "Первый тестовый вариант платформы (прототип)"
git branch -M main
git remote add origin "$REMOTE"
git push -u origin main
git checkout -b dev
git push -u origin dev
echo "Готово: ветки main и dev созданы. Дальше — Pull Request'ы из feat/* в dev (см. CONTRIBUTING.md)."
