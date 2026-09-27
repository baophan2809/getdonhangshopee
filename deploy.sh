#!/usr/bin/env bash
# ============================================================================
#  Cài NPA Tracking Bot thành service systemd trên Oracle Cloud (Ubuntu/Oracle Linux)
#  Chạy:  bash deploy.sh
#
#  An toàn khi máy đang chạy bot khác: service tên riêng
#  "npa-tracking", bot dùng long-polling nên KHÔNG cần mở port, không đụng
#  chạm gì tới bot đang có.
# ============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="npa-tracking"
RUN_USER="$(whoami)"

echo "==> Thư mục cài đặt: $APP_DIR"

# ---------- 1. Cài Python nếu thiếu ----------
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y -qq
    sudo apt-get install -y -qq python3 python3-venv python3-pip
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3.11 python3.11-pip >/dev/null 2>&1 \
        || sudo dnf install -y python3 python3-pip
fi

PY="$(command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)"
echo "==> Dùng Python: $PY ($("$PY" --version 2>&1))"

# ---------- 2. Kiểm tra .env ----------
if [ ! -f "$APP_DIR/.env" ]; then
    if [ -f "$APP_DIR/.env.example" ]; then
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
        echo "📝 Đã tạo file .env từ .env.example"
    else
        echo "❌ Không thấy file .env trong $APP_DIR"
        exit 1
    fi
fi
if ! grep -Eq '^BOT_TOKEN=..+' "$APP_DIR/.env"; then
    echo "❌ Chưa điền BOT_TOKEN trong file .env"
    echo "   Mở .env, dán token lấy từ @BotFather rồi chạy lại: bash deploy.sh"
    exit 1
fi

# ---------- 3. Tạo venv + cài thư viện ----------
if [ ! -d "$APP_DIR/venv" ]; then
    "$PY" -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
echo "==> Đã cài xong thư viện Python."

# ---------- 4. Tạo service systemd ----------
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=NPA Tracking Bot (SPX & GHN)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"
sleep 2
sudo systemctl --no-pager --full status "${SERVICE_NAME}" || true

echo ""
echo "✅ Xong! Bot đang chạy dưới service: ${SERVICE_NAME}"
echo "   Xem log trực tiếp : journalctl -u ${SERVICE_NAME} -f"
echo "   Khởi động lại     : sudo systemctl restart ${SERVICE_NAME}"
echo "   Dừng bot          : sudo systemctl stop ${SERVICE_NAME}"
