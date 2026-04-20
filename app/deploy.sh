#!/bin/bash
# deploy.sh — одноразовый скрипт настройки сервера на Hetzner
# Запускать от root после создания сервера Ubuntu 24.04
# Использование: bash deploy.sh

set -e

echo "=== 1. Обновление системы ==="
apt update && apt upgrade -y

echo "=== 2. Установка зависимостей ==="
apt install -y python3 python3-pip python3-venv git \
    postgresql postgresql-client redis-server \
    curl wget unzip

echo "=== 3. PostgreSQL — создание БД и пользователя ==="
sudo -u postgres psql <<EOF
CREATE USER skibot WITH PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
CREATE DATABASE skibot OWNER skibot;
GRANT ALL PRIVILEGES ON DATABASE skibot TO skibot;
EOF

echo "=== 4. Redis — запуск ==="
systemctl enable redis-server
systemctl start redis-server

echo "=== 5. Создание системного пользователя для бота ==="
useradd -m -s /bin/bash skibot || true

echo "=== 6. Клонирование репозитория ==="
# Замени на свой репозиторий
# git clone https://github.com/YOUR/ski-mvp-bot.git /home/skibot/ski-mvp-bot
# Или скопируй файлы через scp:
# scp -r ./ski-mvp-bot root@YOUR_SERVER_IP:/home/skibot/

mkdir -p /home/skibot/ski-mvp-bot
chown skibot:skibot /home/skibot/ski-mvp-bot

echo "=== 7. Python venv и зависимости ==="
cd /home/skibot/ski-mvp-bot
sudo -u skibot python3 -m venv venv
sudo -u skibot venv/bin/pip install --upgrade pip
sudo -u skibot venv/bin/pip install -r requirements.txt

echo "=== 8. Playwright — установка Chromium ==="
sudo -u skibot venv/bin/playwright install chromium
sudo -u skibot venv/bin/playwright install-deps chromium

echo "=== 9. .env файл ==="
echo "Создай /home/skibot/ski-mvp-bot/.env по шаблону env_template.txt"
echo "Пример: nano /home/skibot/ski-mvp-bot/.env"

echo "=== 10. Systemd сервис ==="
cat > /etc/systemd/system/skibot.service <<EOF
[Unit]
Description=Ski MVP Telegram Bot
After=network.target postgresql.service redis-server.service

[Service]
User=skibot
WorkingDirectory=/home/skibot/ski-mvp-bot
EnvironmentFile=/home/skibot/ski-mvp-bot/.env
ExecStart=/home/skibot/ski-mvp-bot/venv/bin/python -m app.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable skibot

echo ""
echo "=== Готово! ==="
echo "Следующие шаги:"
echo "1. Скопируй файлы бота в /home/skibot/ski-mvp-bot/"
echo "2. Создай .env файл с токенами"
echo "3. Запусти: systemctl start skibot"
echo "4. Проверь логи: journalctl -u skibot -f"
