#!/usr/bin/env bash
set -Eeuo pipefail

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Error: '$1' is required but not installed."
    exit 1
  fi
}

upsert_env() {
  local file="$1"
  local key="$2"
  local value="$3"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    echo "${key}=${value}" >>"$file"
  fi
}

require_cmd git
require_cmd python3
require_cmd sudo
require_cmd systemctl

DEFAULT_DEPLOY_USER="${SUDO_USER:-$(id -un)}"
DEFAULT_BRANCH="main"
DEFAULT_APP_DIR="/opt/avto_fix_bot"
DEFAULT_SERVICE_NAME="avto-fix-bot"
DEFAULT_REPO_URL="https://github.com/Levis-Artur/auto_fix_bot.git"

read -r -p "GitHub repo URL (https/ssh) [${DEFAULT_REPO_URL}]: " REPO_URL
REPO_URL="${REPO_URL:-$DEFAULT_REPO_URL}"

read -r -p "Branch [${DEFAULT_BRANCH}]: " BRANCH
BRANCH="${BRANCH:-$DEFAULT_BRANCH}"

read -r -p "App directory [${DEFAULT_APP_DIR}]: " APP_DIR
APP_DIR="${APP_DIR:-$DEFAULT_APP_DIR}"

read -r -p "Deploy user [${DEFAULT_DEPLOY_USER}]: " DEPLOY_USER
DEPLOY_USER="${DEPLOY_USER:-$DEFAULT_DEPLOY_USER}"

read -r -p "Systemd service name [${DEFAULT_SERVICE_NAME}]: " SERVICE_NAME
SERVICE_NAME="${SERVICE_NAME:-$DEFAULT_SERVICE_NAME}"

echo
echo "Preparing application directory: ${APP_DIR}"
sudo mkdir -p "$APP_DIR"
sudo chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "$APP_DIR"

if [[ -d "${APP_DIR}/.git" ]]; then
  echo "Repository exists. Updating from branch '${BRANCH}'..."
  sudo -u "$DEPLOY_USER" git -C "$APP_DIR" fetch --all --prune
  sudo -u "$DEPLOY_USER" git -C "$APP_DIR" checkout "$BRANCH"
  sudo -u "$DEPLOY_USER" git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
else
  echo "Cloning repository..."
  sudo -u "$DEPLOY_USER" git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

echo "Creating/updating virtual environment..."
sudo -u "$DEPLOY_USER" python3 -m venv "${APP_DIR}/.venv"
sudo -u "$DEPLOY_USER" "${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
sudo -u "$DEPLOY_USER" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

ENV_FILE="${APP_DIR}/.env"
sudo -u "$DEPLOY_USER" touch "$ENV_FILE"
sudo -u "$DEPLOY_USER" chmod 600 "$ENV_FILE"

echo
read -r -s -p "Enter BOT_TOKEN: " BOT_TOKEN
echo
read -r -p "Enter TARGET_CHAT: " TARGET_CHAT
read -r -p "Enter ADMIN_IDS (comma-separated, can be empty): " ADMIN_IDS
read -r -p "REQUEST_CONNECT_TIMEOUT [10]: " REQUEST_CONNECT_TIMEOUT
read -r -p "REQUEST_READ_TIMEOUT [25]: " REQUEST_READ_TIMEOUT
read -r -p "REQUEST_WRITE_TIMEOUT [25]: " REQUEST_WRITE_TIMEOUT
read -r -p "REQUEST_POOL_TIMEOUT [10]: " REQUEST_POOL_TIMEOUT

REQUEST_CONNECT_TIMEOUT="${REQUEST_CONNECT_TIMEOUT:-10}"
REQUEST_READ_TIMEOUT="${REQUEST_READ_TIMEOUT:-25}"
REQUEST_WRITE_TIMEOUT="${REQUEST_WRITE_TIMEOUT:-25}"
REQUEST_POOL_TIMEOUT="${REQUEST_POOL_TIMEOUT:-10}"

sudo -u "$DEPLOY_USER" bash -c "$(cat <<'EOS'
set -Eeuo pipefail
ENV_FILE="$1"
BOT_TOKEN="$2"
TARGET_CHAT="$3"
ADMIN_IDS="$4"
REQUEST_CONNECT_TIMEOUT="$5"
REQUEST_READ_TIMEOUT="$6"
REQUEST_WRITE_TIMEOUT="$7"
REQUEST_POOL_TIMEOUT="$8"

upsert_env() {
  local file="$1"
  local key="$2"
  local value="$3"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    echo "${key}=${value}" >>"$file"
  fi
}

upsert_env "$ENV_FILE" "BOT_TOKEN" "$BOT_TOKEN"
upsert_env "$ENV_FILE" "TARGET_CHAT" "$TARGET_CHAT"
upsert_env "$ENV_FILE" "ADMIN_IDS" "$ADMIN_IDS"
upsert_env "$ENV_FILE" "REQUEST_CONNECT_TIMEOUT" "$REQUEST_CONNECT_TIMEOUT"
upsert_env "$ENV_FILE" "REQUEST_READ_TIMEOUT" "$REQUEST_READ_TIMEOUT"
upsert_env "$ENV_FILE" "REQUEST_WRITE_TIMEOUT" "$REQUEST_WRITE_TIMEOUT"
upsert_env "$ENV_FILE" "REQUEST_POOL_TIMEOUT" "$REQUEST_POOL_TIMEOUT"
EOS
)" _ "$ENV_FILE" "$BOT_TOKEN" "$TARGET_CHAT" "$ADMIN_IDS" "$REQUEST_CONNECT_TIMEOUT" "$REQUEST_READ_TIMEOUT" "$REQUEST_WRITE_TIMEOUT" "$REQUEST_POOL_TIMEOUT"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "Creating/updating systemd service: ${SERVICE_FILE}"
sudo tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=${SERVICE_NAME} Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${DEPLOY_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/index.py
Restart=always
RestartSec=5
EnvironmentFile=${ENV_FILE}

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

echo
echo "Deploy complete."
echo "Status: sudo systemctl status ${SERVICE_NAME}"
echo "Logs:   sudo journalctl -u ${SERVICE_NAME} -f"
