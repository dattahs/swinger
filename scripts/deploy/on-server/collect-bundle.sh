#!/usr/bin/env bash
# Create a diagnostic tarball on the VPS. Pulled to laptop via collect.py.

set -euo pipefail

ROOT="${SWINGER_ROOT:-/opt/swinger}"
SHARED="${ROOT}/shared"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BUNDLE_DIR="${SHARED}/bundles/swinger-bundle-${STAMP}"
ARCHIVE="${SHARED}/bundles/swinger-bundle-${STAMP}.tar.gz"

mkdir -p "${BUNDLE_DIR}/logs" "${BUNDLE_DIR}/data/live" "${BUNDLE_DIR}/data/processed" \
  "${BUNDLE_DIR}/config" "${BUNDLE_DIR}/meta"

# Version / deploy metadata
if [[ -f "${ROOT}/current/DEPLOY_VERSION.json" ]]; then
  cp "${ROOT}/current/DEPLOY_VERSION.json" "${BUNDLE_DIR}/meta/"
fi
if [[ -f "${ROOT}/current/DEPLOY_VERSION" ]]; then
  cp "${ROOT}/current/DEPLOY_VERSION" "${BUNDLE_DIR}/meta/"
fi

hostname > "${BUNDLE_DIR}/meta/hostname.txt"
date -u -Iseconds > "${BUNDLE_DIR}/meta/collected_at_utc.txt"

# Logs (last 14 days of rotated logs if present)
cp -a "${SHARED}/logs/." "${BUNDLE_DIR}/logs/" 2>/dev/null || true

# SQLite databases (consistent snapshot via .backup when sqlite3 available)
copy_db() {
  local src="$1"
  local dest="$2"
  if [[ ! -f "${src}" ]]; then
    return 0
  fi
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "${src}" ".backup '${dest}'"
  else
    cp -a "${src}" "${dest}"
  fi
}

copy_db "${SHARED}/data/live/swinger_live.db" "${BUNDLE_DIR}/data/live/swinger_live.db"
copy_db "${SHARED}/data/processed/swinger_data.db" "${BUNDLE_DIR}/data/processed/swinger_data.db"

# Token metadata only (not the secret file contents)
if [[ -f "${SHARED}/data/live/upstox_token.json" ]]; then
  python3 - <<'PY' "${SHARED}/data/live/upstox_token.json" > "${BUNDLE_DIR}/meta/token_meta.json" 2>/dev/null || true
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
raw = json.loads(p.read_text())
print(json.dumps({
    "has_access_token": bool(raw.get("access_token")),
    "obtained_at": raw.get("obtained_at"),
    "expires_at": raw.get("expires_at"),
}, indent=2))
PY
fi

# Config without secrets
if [[ -f "${SHARED}/config.yaml" ]]; then
  cp "${SHARED}/config.yaml" "${BUNDLE_DIR}/config/config.yaml"
fi
if [[ -f "${SHARED}/.env" ]]; then
  grep -E '^[A-Z_]+=' "${SHARED}/.env" | sed 's/=.*$/=***REDACTED***/' > "${BUNDLE_DIR}/meta/env_keys.txt" || true
fi

tar -czf "${ARCHIVE}" -C "${SHARED}/bundles" "swinger-bundle-${STAMP}"
rm -rf "${BUNDLE_DIR}"

echo "${ARCHIVE}"
