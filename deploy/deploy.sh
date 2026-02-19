#!/usr/bin/env bash
set -Eeuo pipefail

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Error: '$1' is required but not installed."; exit 1; }
}

# safe upsert KEY=VALUE into env file
upsert_env() {
  local file="$1" key="$2" value="$3"
  touch "$file"
  if grep -qE "^${key}=" "$file" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    printf "%s=%s\n" "$key" "$value" >>"$file"
  fi
}

mask_len() {
  # prints length only (no secrets)
  local s="${1:-}"
  echo "${#s}"
}

require_cmd git
require_cmd python3
require_cmd sudo
require_cmd systemctl

DEFAULT_BRANCH="main"
DEFAULT_REPO_URL="https://github.com/Levis-Artur/auto_fix_bot.git"
DEFAULT_BASE_DIR="/home/${SUDO_USER:-$(id -un)}/projects/tg_auto_bots"
DEFAULT_DEPLOY_USER="${SUDO_USER:-$(id -un)}"

# Bots list (space-separated service names)
DEFAULT_BOTS="uzhhorod_auto_bot vinnytsia_auto_bot volyn_auto_bot zaporozhye_auto_bot zhytomyr_auto_bot dnipropetrovsk_auto_bot ivano_frankivsk_auto_bot kirovograd_auto_bot kryvyirih_auto_bot lviv_auto_bot"

read -r -p "Bots (space-separated) [${DEFAULT_BOTS}]: " BOTS
BOTS="${BOTS:-$DEFAULT_BOTS}"

read -r -p "GitHub repo URL (https/ssh) [${DEFAULT_REPO_URL}]: " REPO_URL
REPO_URL="${REPO_URL:-$DEFAULT_REPO_URL}"

read -r -p "Branch [${DEFAULT_BRANCH}]: " BRANCH
BRANCH="${BRANCH:-$DEFAULT_BRANCH}"

read -r -p "Base dir [${DEFAULT_BASE_DIR}]: " BASE_DIR
BASE_DIR="${BASE_DIR:-$DEFAULT_BASE_DIR}"

read -r -p "Deploy user [${DEFAULT_DEPLOY_USER}]: " DEPLOY_USER
DEPLOY_USER="${DEPLOY_USER:-$DEFAULT_DEPLOY_USER}"

echo
echo "== Plan =="
echo "Bots:      ${BOTS}"
echo "Repo:      ${REPO_URL}"
echo "Branch:    ${BRANCH}"
echo "Base dir:  ${BASE_DIR}"
echo "User:      ${DEPLOY_USER}"
echo

sudo mkdir -p "$BASE_DIR"
sudo chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "$BASE_DIR"

# Stop bots first (optional but prevents flapping)
echo "Stopping selected bot services (if running)..."
for s in $BOTS; do
  sudo systemctl stop "$s" >/dev/null 2>&1 || true
done
sudo systemctl reset-failed || true

for s in $BOTS; do
  APP_DIR="${BASE_DIR}/${s}/auto_fix_bot"
  ENV_FILE="${APP_DIR}/.env"
  SERVICE_FILE="/etc/systemd/system/${s}.service"

  echo "=============================="
  echo "Deploying: $s"
  echo "APP_DIR:   $APP_DIR"

  sudo mkdir -p "$APP_DIR"
  sudo chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${BASE_DIR}/${s}"

  if [[ -d "${APP_DIR}/.git" ]]; then
    echo "Repo exists. Updating..."
    sudo -u "$DEPLOY_USER" git -C "$APP_DIR" fetch --all --prune
    sudo -u "$DEPLOY_USER" git -C "$APP_DIR" checkout "$BRANCH"
    sudo -u "$DEPLOY_USER" git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
  else
    echo "Cloning..."
    sudo -u "$DEPLOY_USER" git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  fi

  echo "Venv + deps..."
  if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
    sudo -u "$DEPLOY_USER" python3 -m venv "${APP_DIR}/.venv"
  fi
  sudo -u "$DEPLOY_USER" "${APP_DIR}/.venv/bin/python" -m pip install -U pip >/dev/null
  sudo -u "$DEPLOY_USER" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt" >/dev/null

  echo "Ensuring .env..."
  sudo -u "$DEPLOY_USER" touch "$ENV_FILE"
  sudo -u "$DEPLOY_USER" chmod 600 "$ENV_FILE"

  # Put defaults but do NOT force tokens here
  # You will fill BOT_TOKEN / TARGET_CHAT manually later if empty.
  sudo -u "$DEPLOY_USER" bash -c "
    set -Eeuo pipefail
    f='$ENV_FILE'
    grep -q '^BOT_TOKEN=' \"\$f\" || echo 'BOT_TOKEN=' >>\"\$f\"
    grep -q '^TARGET_CHAT=' \"\$f\" || echo 'TARGET_CHAT=' >>\"\$f\"
    grep -q '^ADMIN_IDS=' \"\$f\" || echo 'ADMIN_IDS=6881142873' >>\"\$f\"
    grep -q '^REQUEST_CONNECT_TIMEOUT=' \"\$f\" || echo 'REQUEST_CONNECT_TIMEOUT=10' >>\"\$f\"
    grep -q '^REQUEST_READ_TIMEOUT=' \"\$f\" || echo 'REQUEST_READ_TIMEOUT=25' >>\"\$f\"
    grep -q '^REQUEST_WRITE_TIMEOUT=' \"\$f\" || echo 'REQUEST_WRITE_TIMEOUT=25' >>\"\$f\"
    grep -q '^REQUEST_POOL_TIMEOUT=' \"\$f\" || echo 'REQUEST_POOL_TIMEOUT=10' >>\"\$f\"
  "

  echo "Writing systemd unit: $SERVICE_FILE"
  sudo tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=${s} Telegram Bot
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
  sudo systemctl enable "$s" >/dev/null

  # Start ONLY if token is set (avoid endless restarts)
  BOT_TOKEN_LEN=$(awk -F= '/^BOT_TOKEN=/{print length($2)}' "$ENV_FILE" | head -n1)
  if [[ "${BOT_TOKEN_LEN:-0}" -ge 20 ]]; then
    echo "Starting $s (token length ${BOT_TOKEN_LEN})..."
    sudo systemctl start "$s" || true
    sleep 2
    sudo systemctl is-active "$s" && echo "RUNNING ✅" || echo "NOT RUNNING ❌ (check logs)"
  else
    echo "Skipping start for $s (BOT_TOKEN is empty). Fill ${ENV_FILE} then: sudo systemctl start ${s}"
  fi
done

echo
echo "Deploy done."
echo "Tip: check bots status:"
echo "  systemctl list-units --type=service | grep auto_bot"
echo
echo "Tip: tail logs:"
echo "  sudo journalctl -u <bot> -f --no-pager"
