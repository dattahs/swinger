#!/usr/bin/env bash
# ONE-TIME: Run this in your VPS provider's web console (as root) if SSH key login fails.
# Then retry from laptop: python scripts/deploy/push.py

set -euo pipefail
mkdir -p /root/.ssh
chmod 700 /root/.ssh
KEY='ssh-ed25519 AAAA...your-public-key... you@example.com'
grep -qxF "${KEY}" /root/.ssh/authorized_keys 2>/dev/null || echo "${KEY}" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
echo "SSH key installed. Test from laptop: ssh root@YOUR_VPS_IP echo OK"
