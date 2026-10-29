#!/usr/bin/env bash
# Запуск платформы. Режим модели выбирается по настройкам LLM_BASE_URL (из окружения или файла .env):
#
#   bash run.sh            авто: внешний адрес модели (организаторы, vLLM и т.п.) -> без Ollama;
#                          адрес не задан или локальный -> Ollama в контейнере (gpu, если есть NVIDIA, иначе cpu)
#   bash run.sh --remote   только БД и приложение, модель по LLM_* из .env
#   bash run.sh --gpu      принудительно Ollama с видеокартой
#   bash run.sh --cpu      принудительно Ollama на процессоре
set -euo pipefail
cd "$(dirname "$0")"

# Значение переменной: окружение важнее файла .env (кавычки и переводы строк Windows убираем)
get_var() {
    local v="${!1:-}"
    if [ -z "$v" ] && [ -f .env ]; then
        v=$(grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | tr -d '\r' \
            | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" || true)
    fi
    printf '%s' "$v"
}

pick_local_profile() {
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        echo gpu
    else
        echo cpu
    fi
}

mode="${1:---auto}"
case "$mode" in
    --remote) mode=remote ;;
    --gpu)    mode=gpu ;;
    --cpu)    mode=cpu ;;
    --auto)
        base="$(get_var LLM_BASE_URL)"
        case "$base" in
            ""|*localhost*|*127.0.0.1*|*ollama:11434*) mode="$(pick_local_profile)" ;;
            *)                                         mode=remote ;;
        esac
        ;;
    *) echo "Использование: bash run.sh [--auto|--remote|--gpu|--cpu]" >&2; exit 2 ;;
esac

if [ "$mode" = remote ]; then
    echo "[INFO] Внешняя модель: $(get_var LLM_BASE_URL) (контейнер Ollama не запускается)"
    [ -n "$(get_var LLM_BASE_URL)" ] || echo "[WARN] LLM_BASE_URL не задан: приложение будет искать модель по умолчанию (http://ollama:11434/v1), которой нет"
    [ -n "$(get_var LLM_API_KEY)" ]  || echo "[WARN] LLM_API_KEY не задан: внешний сервер, скорее всего, ответит 401"
    docker compose up -d --build
else
    echo "[INFO] Локальная модель Ollama, режим: $mode$([ "$mode" = cpu ] && echo ' (без видеокарты большие модели медленные)')"
    docker compose --profile "$mode" up -d --build
    echo "Модель скачивается при первом запуске. Прогресс: docker compose logs -f ollama-$mode"
fi

echo
echo "Приложение: http://localhost:8501"
