#!/usr/bin/env bash
# Запуск платформы: сам выбирает профиль GPU или CPU.
set -euo pipefail

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    PROFILE=gpu
    echo "[INFO] Найдена видеокарта NVIDIA: режим GPU"
else
    PROFILE=cpu
    echo "[INFO] GPU не найдена: режим CPU (большие модели будут медленными)"
fi

docker compose --profile "$PROFILE" up -d --build

echo
echo "Приложение: http://localhost:8501"
echo "Модель скачивается при первом запуске. Прогресс: docker compose logs -f ollama-$PROFILE"
