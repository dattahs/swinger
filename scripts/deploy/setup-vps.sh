#!/usr/bin/env bash
# Idempotent VPS bootstrap for Swinger. Run from laptop:
#   python scripts/deploy/remote.py setup
# Or directly on the server after copying the repo.

set -euo pipefail

REMOTE_ROOT="${SWINGER_ROOT:-/opt/swinger}"
PYTHON="${SWINGER_PYTHON:-python3}"
VENV_DIR="${REMOTE_ROOT}/venv"
SHARED="${REMOTE_ROOT}/shared"
CURRENT="${REMOTE_ROOT}/current"
DEPLOY_SCRIPTS="${CURRENT}/scripts/deploy"

echo "==> Swinger VPS setup (root=${REMOTE_ROOT})"

if command -v timedatectl >/dev/null 2>&1; then
  echo "==> Setting timezone to Asia/Kolkata (IST) for market cron"
  sudo timedatectl set-timezone Asia/Kolkata 2>/dev/null || true
fi

if ! command -v "${PYTHON}" >/dev/null 2>&1; then
  echo "ERROR: ${PYTHON} not found. Install Python 3.11+ first." >&2
  exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  echo "==> Installing apt packages (may prompt for sudo)"
  sudo apt-get update -qq
  sudo apt-get install -y -qq \
    "${PYTHON}" "${PYTHON}-venv" python3-pip \
    git sqlite3 curl rsync \
    libsqlite3-0 util-linux
fi

sudo mkdir -p "${REMOTE_ROOT}/releases" "${SHARED}/data/processed" "${SHARED}/data/live" \
  "${SHARED}/logs" "${SHARED}/bundles" "${SHARED}/backtest_outputs"
sudo chown -R "$(whoami):$(whoami)" "${REMOTE_ROOT}" 2>/dev/null || true

mkdir -p "${SHARED}/data/live/warmup_cache" "${SHARED}/data/.upstox_browser"

if [[ ! -f "${SHARED}/config.yaml" ]]; then
  if [[ -f "${DEPLOY_SCRIPTS}/config.vps.template.yaml" ]]; then
    cp "${DEPLOY_SCRIPTS}/config.vps.template.yaml" "${SHARED}/config.yaml"
    echo "==> Created ${SHARED}/config.yaml from template — edit vps_public_ip and strategy knobs"
  else
    echo "WARN: config.vps.template.yaml not found; create ${SHARED}/config.yaml manually"
  fi
fi

if [[ ! -f "${SHARED}/.env" ]]; then
  touch "${SHARED}/.env"
  chmod 600 "${SHARED}/.env"
  echo "==> Created empty ${SHARED}/.env (chmod 600) — add UPSTOX_* secrets"
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "==> Creating venv at ${VENV_DIR}"
  "${PYTHON}" -m venv "${VENV_DIR}"
fi

if [[ -L "${CURRENT}" || -d "${CURRENT}" ]]; then
  echo "==> Installing Python dependencies"
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
  pip install -q --upgrade pip
  pip install -q -r "${CURRENT}/requirements.txt"
  if [[ -f "${CURRENT}/requirements-live.txt" ]]; then
    pip install -q -r "${CURRENT}/requirements-live.txt"
  fi
  if command -v apt-get >/dev/null 2>&1; then
    echo "==> Installing system Chromium (Playwright bundle unsupported on this OS)"
    sudo apt-get install -y -qq chromium 2>/dev/null || true
  fi
  if command -v playwright >/dev/null 2>&1; then
    playwright install chromium 2>/dev/null || echo "NOTE: using system Chromium at /snap/bin/chromium"
  fi
else
  echo "WARN: ${CURRENT} not deployed yet — run push from laptop first, then re-run setup"
fi

LOCK_FILE="${SHARED}/swinger-live.lock"
CRON_LOGIN="${SWINGER_CRON_LOGIN:-45 8 * * 1-5}"
CRON_LIVE="${SWINGER_CRON_LIVE:-30 16 * * 1-5}"
CRON_INGEST="${SWINGER_CRON_INGEST:-0 17 * * 1-5}"

WRAPPER="${REMOTE_ROOT}/bin/swinger-remote-exec.sh"
mkdir -p "${REMOTE_ROOT}/bin"
cat > "${WRAPPER}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export SWINGER_ROOT="${REMOTE_ROOT}"
export SWINGER_VENV="${VENV_DIR}"
export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH="\${PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH:-/snap/bin/chromium}"
exec "${REMOTE_ROOT}/current/scripts/deploy/on-server/remote-exec.sh" "\$@"
EOF
chmod +x "${WRAPPER}"
chmod +x "${DEPLOY_SCRIPTS}/on-server/"*.sh 2>/dev/null || true

CRON_MARK="# swinger-deploy"
(crontab -l 2>/dev/null | grep -v "${CRON_MARK}" || true; cat <<EOF
${CRON_LOGIN} ${WRAPPER} login >> ${SHARED}/logs/login.log 2>&1 ${CRON_MARK}
${CRON_LIVE} flock -n ${LOCK_FILE} ${WRAPPER} live >> ${SHARED}/logs/live.log 2>&1 ${CRON_MARK}
${CRON_INGEST} ${WRAPPER} ingest >> ${SHARED}/logs/ingest.log 2>&1 ${CRON_MARK}
EOF
) | crontab -

if [[ -f "${DEPLOY_SCRIPTS}/logrotate/swinger" ]]; then
  sudo cp "${DEPLOY_SCRIPTS}/logrotate/swinger" /etc/logrotate.d/swinger 2>/dev/null || \
    echo "NOTE: copy ${DEPLOY_SCRIPTS}/logrotate/swinger to /etc/logrotate.d/ manually"
fi

echo "==> Setup complete"
echo "    current -> ${CURRENT}"
echo "    config  -> ${SHARED}/config.yaml"
echo "    secrets -> ${SHARED}/.env"
echo "    logs    -> ${SHARED}/logs/"
