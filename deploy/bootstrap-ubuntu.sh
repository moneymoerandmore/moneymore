#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo bash deploy/bootstrap-ubuntu.sh" >&2
  exit 1
fi

APP_ROOT=/opt/moneymore
SOURCE="$APP_ROOT/current"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip nginx apache2-utils curl ca-certificates git

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("MoneyMore requires Python >= 3.11; use Ubuntu 24.04 or install a newer Python")
PY

if ! command -v node >/dev/null || [[ $(node -p 'Number(process.versions.node.split(".")[0])') -lt 22 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
fi

id -u moneymore >/dev/null 2>&1 || useradd --system --create-home --home-dir "$APP_ROOT" --shell /usr/sbin/nologin moneymore
mkdir -p "$APP_ROOT/backups" "$SOURCE/logs"
chown -R moneymore:moneymore "$APP_ROOT"

python3 -m venv "$APP_ROOT/venv"
"$APP_ROOT/venv/bin/pip" install --upgrade pip wheel
"$APP_ROOT/venv/bin/pip" install --index-url https://download.pytorch.org/whl/cpu torch
"$APP_ROOT/venv/bin/pip" install -e "$SOURCE[ai]"

runuser -u moneymore -- npm --prefix "$SOURCE/apps/dashboard" ci
runuser -u moneymore -- npm --prefix "$SOURCE/apps/dashboard" run build

install -m 0644 "$SOURCE/deploy/systemd/moneymore-api.service" /etc/systemd/system/
install -m 0644 "$SOURCE/deploy/systemd/moneymore-dashboard.service" /etc/systemd/system/
install -m 0644 "$SOURCE/deploy/systemd/moneymore-backup.service" /etc/systemd/system/
install -m 0644 "$SOURCE/deploy/systemd/moneymore-backup.timer" /etc/systemd/system/
install -m 0644 "$SOURCE/deploy/nginx/moneymore.conf" /etc/nginx/sites-available/moneymore
ln -sfn /etc/nginx/sites-available/moneymore /etc/nginx/sites-enabled/moneymore
rm -f /etc/nginx/sites-enabled/default
chmod +x "$SOURCE/deploy/backup.sh"
chmod 600 "$SOURCE/.env"

if [[ ! -f /etc/nginx/.htpasswd-moneymore ]]; then
  echo "missing /etc/nginx/.htpasswd-moneymore; create it with: htpasswd -c /etc/nginx/.htpasswd-moneymore USER" >&2
  exit 2
fi

nginx -t
systemctl daemon-reload
systemctl enable --now moneymore-api moneymore-dashboard moneymore-backup.timer nginx
