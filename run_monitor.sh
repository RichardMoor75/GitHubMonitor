#!/bin/bash
# GitHub Monitor - Wrapper скрипт для cron
# Автоматически активирует venv и запускает мониторинг

# Переход в директорию, где лежит этот скрипт
cd "$(dirname "$0")"

# Проверка наличия venv
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Virtual environment not found at $(pwd)/venv" >&2
    exit 1
fi

# Запуск скрипта
# Основные логи пишутся в github_monitor.log (с ротацией)
# Сюда (в cron_errors.log) попадут только критические ошибки запуска интерпретатора
python3 github_monitor.py >> cron_errors.log 2>&1