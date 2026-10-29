#!/usr/bin/env bash
# Запускает ВСЕ тесты проекта (tests/test_*.py) и печатает итоговую сводку.
#
#   bash run_tests.sh                     все тесты
#   bash run_tests.sh test_llm test_core  только перечисленные
#   RUNNER="python3" bash run_tests.sh    свой запуск Python (по умолчанию: uv run python, иначе python3)
#
# Windows: запускайте из Git Bash. Код возврата: 0 — всё прошло, 1 — есть упавшие.
# Новые файлы tests/test_*.py подхватываются автоматически.

set -uo pipefail

# Жестко прописываем путь к uv для WSL, если он там есть
if [ -f "$HOME/.local/bin/uv" ]; then
    UV_BIN="$HOME/.local/bin/uv"
elif command -v uv >/dev/null 2>&1; then
    UV_BIN="uv"
else
    UV_BIN=""
fi

if [ -n "${RUNNER:-}" ]; then
    run=($RUNNER)
elif [ -n "$UV_BIN" ]; then
    run=($UV_BIN run python)
elif command -v python3 >/dev/null 2>&1; then
    run=(python3)
else
    run=(python)
fi
echo "Запуск через: ${run[*]}"
echo

if [ "$#" -gt 0 ]; then
    names=("$@")
else
    names=()
    # Самый надежный способ проверить маску файлов в Bash:
    for f in tests/test_*.py; do
        if [ -f "$f" ]; then
            names+=("$(basename "$f" .py)")
        fi
    done
    if [ ${#names[@]} -eq 0 ]; then
        echo "Ошибка: В папке '$(pwd)/tests' не найдено файлов, подходящих под шаблон test_*.py"
        echo "Пожалуйста, проверь, что папка 'tests' существует и в ней есть файлы вида test_something.py"
        exit 1
    fi
fi

passed=()
failed=()

for t in "${names[@]}"; do
    echo "=== $t ==="
    if PYTHONPATH=. "${run[@]}" -m "tests.$t"; then
        passed+=("$t")
    else
        failed+=("$t")
    fi
    echo
done

# Проверка docker-compose.yml, если Docker установлен (не нужна для обычной работы)
if command -v docker >/dev/null 2>&1; then
    echo "=== docker compose config ==="
    if docker compose config -q; then
        passed+=("docker-compose.yml")
    else
        failed+=("docker-compose.yml")
    fi
    echo
else
    echo "(Docker не найден: проверка docker-compose.yml пропущена)"
    echo
fi

echo "============ ИТОГ ============"
echo "Прошли: ${#passed[@]}"
for t in ${passed[@]+"${passed[@]}"}; do echo "  ✓ $t"; done
if [ "${#failed[@]}" -gt 0 ]; then
    echo "Упали: ${#failed[@]}"
    for t in "${failed[@]}"; do echo "  ✗ $t"; done
    exit 1
fi
echo "Все проверки пройдены."
