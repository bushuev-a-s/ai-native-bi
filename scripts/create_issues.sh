#!/usr/bin/env bash
# Создаёт Issues из плана задач. Нужен GitHub CLI (gh) и вход: gh auth login
# Запускать из папки клонированного репозитория.
set -euo pipefail
mk() { gh issue create --title "$1" --body "$2" --label "$3" >/dev/null && echo "+ $1"; }
for l in P0 P1 P2 agent data ui analytics org; do gh label create "$l" --force >/dev/null 2>&1 || true; done

mk "Задать вопросы менторам, записать ответы" "docs/MENTORS.md" "P0,org"
mk "Каждый запускает проект у себя и описывает ошибки" "README, быстрый старт" "P0,org"
mk "Выбрать модель под железо, проверить скорость" "LLM_* в .env" "P0,agent"
mk "Подобрать промпты для слабых моделей (7–8B)" "core/prompts.py" "P0,agent"
mk "Контрольный набор вопросов + скрипт оценки точности SQL" "30–50 вопросов с эталонными ответами" "P0,agent"
mk "Загрузить реальные данные, исправить загрузчик" "десятичная запятая, большие файлы (COPY)" "P0,data"
mk "Проверить профиль и связи на реальных данных" "core/profiler.py" "P0,data"
mk "Довести чат и графики до удобства для бизнес-пользователя" "app.py, core/charts.py" "P0,ui"
mk "Демо-сценарий и презентация" "6–8 вопросов, один «неудобный»" "P0,org"
mk "Выбор релевантных таблиц для больших схем" "schema linking" "P1,agent"
mk "Набор «неудобных» вопросов и проверка отказов" "вне данных, расплывчатые, удаление" "P1,agent"
mk "Подключение к существующей БД из интерфейса" "форма с URL" "P1,data"
mk "Семантический слой: описание колонок с кешем" "LLM описывает колонки один раз" "P1,data"
mk "Дашборды: редактирование и фильтры" "add/remove widget в чате" "P1,ui"
mk "Дубликаты и аномалии" "core/analytics.py" "P1,analytics"
mk "Сезонность и паттерны" "core/analytics.py" "P1,analytics"
mk "Корреляции и лаги" "core/analytics.py" "P1,analytics"
mk "README и запуск одной командой" "docker compose" "P1,org"
mk "Сегментация" "кластеризация" "P2,analytics"
mk "Прогноз и «что если»" "Prophet/statsmodels" "P2,analytics"
mk "ClickHouse (только если нужно)" "второй бэкенд" "P2,data"
