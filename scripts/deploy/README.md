# Swinger VPS deployment

Laptop-side Python scripts + server-side shell helpers for deploying Swinger to a Linux VPS (REQUIREMENTS v1.3 §9).

**Prerequisites (laptop):** OpenSSH client (`ssh`, `scp`), Python 3.11+, Git (recommended for versioned pushes).

**Prerequisites (VPS):** Ubuntu 22.04+ or Debian 12+, Python 3.11+, static public IP registered in Upstox developer console.

## Quick start

```powershell
cd C:\code\Swinger

# 1. Configure SSH target
copy scripts\deploy\deploy.config.example.yaml scripts\deploy\deploy.config.yaml
# Edit host, user, ssh_key, remote_root

# 2. Push code (versioned release under /opt/swinger/releases/)
python scripts/deploy/push.py

# 3. Bootstrap VPS (venv, dirs, cron, logrotate)
python scripts/deploy/remote.py setup

# 4. Add secrets on VPS (once)
python scripts/deploy/remote.py shell
# nano /opt/swinger/shared/.env
# nano /opt/swinger/shared/config.yaml   # set vps_public_ip, paper_mode, etc.

# 5. Run manually
python scripts/deploy/remote.py ingest -- --download-bhavcopy
python scripts/deploy/remote.py login
python scripts/deploy/remote.py live -v
```

## Layout on VPS

```
/opt/swinger/
  current/              -> releases/<version>/   (symlink, updated each push)
  releases/
    20260625-abc1234/
      DEPLOY_VERSION
      DEPLOY_VERSION.json
  venv/                 shared Python env
  shared/
    config.yaml         persistent config (NOT overwritten by push)
    .env                secrets (chmod 600)
    data/
      processed/swinger_data.db
      live/swinger_live.db
    logs/
      live.log
      ingest.log
    bundles/            collect-bundle archives
  bin/swinger-remote-exec.sh
```

## Commands (laptop)

| Script | Purpose |
|--------|---------|
| `python scripts/deploy/push.py` | Push git-tracked release; updates `current` symlink |
| `python scripts/deploy/push.py --version 2026-06-25-rc1` | Pin release name |
| `python scripts/deploy/remote.py setup` | Run `setup-vps.sh` on server |
| `python scripts/deploy/remote.py live` | EOD live run |
| `python scripts/deploy/remote.py live -- --date 2026-06-20 -v` | Pass flags after `--` |
| `python scripts/deploy/remote.py ingest` | Data ingest |
| `python scripts/deploy/remote.py login` | Upstox OAuth on VPS |
| `python scripts/deploy/remote.py shell` | SSH to `current/` |
| `python scripts/deploy/collect.py` | Pull log + DB bundle for local debug |
| `python scripts/deploy/restore.py deploy-bundles\*.tar.gz` | Restore DBs into local `data/` |

## Versioning

Each `push.py` run creates `releases/<timestamp>-<git-sha>/` and writes:

- `DEPLOY_VERSION` — plain text version id
- `DEPLOY_VERSION.json` — metadata for troubleshooting bundles

Old releases are pruned (`release_keep` in deploy config, default 5).

## Cron (installed by setup)

- **16:30 IST Mon–Fri** — `flock` + live run → `shared/logs/live.log`
- **17:00 IST Mon–Fri** — bhavcopy ingest → `shared/logs/ingest.log`

Edit schedules in `deploy.config.yaml` before `remote.py setup`, or adjust crontab on the server.

## Collect / restore workflow

On laptop after a failed live run:

```powershell
python scripts/deploy/collect.py
# -> deploy-bundles/swinger-bundle-<utc>.tar.gz

python scripts/deploy/restore.py deploy-bundles\swinger-bundle-....tar.gz --force
python scripts/run_live.py --config config.yaml --date 2026-06-20 -v
```

Bundles include: logs, SQLite snapshots (`.backup` when `sqlite3` available), redacted env key list, `config.yaml`, deploy version metadata. **Secrets are not exported.**

## Security notes

- Never commit `deploy.config.yaml` or `shared/.env`.
- Keep `live.paper_mode: true` until rollout gates (REQUIREMENTS §16) are satisfied.
- Playwright login on a headless VPS requires `playwright install chromium` and may need Xvfb for discretionary mode — consider logging in locally and copying `upstox_token.json` to `shared/data/live/` for early testing.
