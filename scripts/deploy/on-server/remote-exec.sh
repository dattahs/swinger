#!/usr/bin/env bash
# Run Swinger commands on the VPS (invoked via SSH or cron wrapper).

set -euo pipefail

ROOT="${SWINGER_ROOT:-/opt/swinger}"
VENV="${SWINGER_VENV:-${ROOT}/venv}"
APP="${ROOT}/current"
CONFIG="${ROOT}/shared/config.yaml"
ENV_FILE="${ROOT}/shared/.env"

if [[ ! -d "${APP}" ]]; then
  echo "ERROR: ${APP} missing — deploy code first (python scripts/deploy/push.py)" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${VENV}/bin/activate"
cd "${APP}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

# Live run opens many SQLite read handles (universe + sector lookups); default 1024 is tight.
ulimit -n 8192 2>/dev/null || true

CMD="${1:-}"
shift || true

case "${CMD}" in
  live)
    exec python scripts/run_live.py --config "${CONFIG}" "$@"
    ;;
  ingest)
    exec python scripts/ingest_all.py --config "${CONFIG}" "$@"
    ;;
  login)
    exec python scripts/upstox_login.py --config "${CONFIG}" --force "$@"
    ;;
  collect-bundle)
    exec "${APP}/scripts/deploy/on-server/collect-bundle.sh" "$@"
    ;;
  setup)
    export SWINGER_ROOT="${ROOT}"
    export SWINGER_PYTHON="${SWINGER_PYTHON:-python3}"
    exec bash "${APP}/scripts/deploy/setup-vps.sh"
    ;;
  test-sandbox)
    exec python scripts/test_sandbox_order.py --config "${CONFIG}" "$@"
    ;;
  test-live-gtt)
    exec python scripts/test_live_gtt.py --config "${CONFIG}" "$@"
    ;;
  *)
    echo "Usage: $0 {live|ingest|login|collect-bundle|setup|test-sandbox|test-live-gtt} [args...]" >&2
    exit 1
    ;;
esac
