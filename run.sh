# Проверяем наличие утилиты nvidia-smi и доступность GPU
if command -v nvidia-smi &> /dev/null && nvidia-smi &> /dev/null; then
    echo "[INFO] Найдена видеокарта NVIDIA. Запуск в режиме GPU..."
    
    # Запускаем и перенаправляем приложение на контейнер ollama-gpu
    export LLM_BASE_URL="http://ollama-gpu:11434"
    docker compose up -d
else
    echo "[INFO] GPU не обнаружен или драйверы не установлены. Запуск в режиме CPU..."
    
    # Запускаем CPU-сценарий (сервис ollama-gpu будет проигнорирован)
    docker compose --profile cpu up -d
fi