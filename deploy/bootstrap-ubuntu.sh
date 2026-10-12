#!/usr/bin/env bash
# First installation on the dedicated Ubuntu VPS. Never prints credentials.
set -Eeuo pipefail
umask 077

DOMAIN=${1:-}
EXPECTED_IP=${2:-}
CHECK_ONLY=${3:-}
INSTALL_DIR=/opt/dola-gateway
REPOSITORY=https://github.com/cuongni1989-coder/dola-render-gateway-tram-share.git
# Application revision already passed the complete Docker/browser CI.
APP_COMMIT=14876ef9a66ceffe0e43f7cdeab6095bbb12ab11

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
trap 'printf "INSTALL_FAILED at line %s. No credentials are included in this report.\n" "$LINENO" >&2' ERR

[[ $# -ge 2 && $# -le 3 ]] || fail 'Usage: bash bootstrap-ubuntu.sh DOMAIN VPS_IPV4 [--check-only]'
[[ -z "$CHECK_ONLY" || "$CHECK_ONLY" == --check-only ]] || fail 'Unknown option'
[[ "$DOMAIN" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$ && ${#DOMAIN} -le 253 ]] || fail 'Invalid domain'
[[ "$EXPECTED_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail 'Invalid VPS IPv4'
[[ $EUID -eq 0 ]] || fail 'Run as root on the VPS'
[[ $(uname -m) == x86_64 ]] || fail 'This installer requires x86_64'
# shellcheck disable=SC1091
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || fail 'This installer is for Ubuntu 24.04'
ip -4 -o address show scope global | awk '{split($4,a,"/"); print a[1]}' | grep -Fxq "$EXPECTED_IP" || fail 'VPS IP does not match. Stop to avoid installing on another server'
[[ ! -e "$INSTALL_DIR" ]] || fail '/opt/dola-gateway already exists. Do not overwrite it; inspect the previous installation'
[[ $(awk '/MemTotal/ {print $2}' /proc/meminfo) -ge 4000000 ]] || fail 'At least 4 GB RAM is required'
[[ $(df -Pk /opt | awk 'NR==2 {print $4}') -ge 15000000 ]] || fail 'At least 15 GB of free disk is required for the initial build'
if [[ -n $(ss -H -ltn '( sport = :80 or sport = :443 or sport = :6080 )') || -n $(ss -H -lun '( sport = :443 )') ]]; then
    fail 'A required port is in use. Inspect existing services before installation'
fi
if command -v docker >/dev/null 2>&1; then
    docker compose version >/dev/null || fail 'Existing Docker lacks Compose v2; inspect it before proceeding'
else
    for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
        if [[ $(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true) == installed ]]; then
            fail "Existing package $package conflicts with official Docker; inspect it before proceeding"
        fi
    done
fi
printf 'PREFLIGHT_OK host=%s ip=%s domain=%s\n' "$(hostname)" "$EXPECTED_IP" "$DOMAIN"
[[ "$CHECK_ONLY" != --check-only ]] || exit 0

LOG_FILE="/var/log/dola-install-$(date -u +%Y%m%dT%H%M%SZ).log"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git python3
if ! command -v docker >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl --proto '=https' --tlsv1.2 -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
docker compose version
git clone --branch codex/team-vps-20261012 "$REPOSITORY" "$INSTALL_DIR"
cd "$INSTALL_DIR"
git checkout --detach "$APP_COMMIT"
python3 deploy/configure.py "$DOMAIN"
docker compose config --quiet
docker compose build
docker compose up -d --no-deps app

APP_READY=0
for attempt in $(seq 1 60); do
    if docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2)" >/dev/null 2>&1; then
        APP_READY=1
        break
    fi
    sleep 2
done
[[ "$APP_READY" == 1 ]] || fail 'The app did not become ready. Containers are retained for inspection'
docker compose up -d
docker compose ps
printf '\nAPP_READY_OK revision=%s\n' "$APP_COMMIT"
printf 'PRIVATE_BROWSER=127.0.0.1:6080 (SSH tunnel only)\n'
printf 'DNS_REQUIRED: A record %s -> %s\n' "$DOMAIN" "$EXPECTED_IP"

HTTPS_READY=0
# Never report another server's ready endpoint as this VPS's successful deployment.
DNS_IPV4=$(getent ahostsv4 "$DOMAIN" | awk '{print $1}' | sort -u || true)
if [[ "$DNS_IPV4" == "$EXPECTED_IP" ]]; then
    for attempt in $(seq 1 6); do
        if curl -4 --resolve "$DOMAIN:443:$EXPECTED_IP" --proto '=https' --tlsv1.2 -fsS --connect-timeout 5 --max-time 10 "https://$DOMAIN/ready" >/dev/null; then
            HTTPS_READY=1
            break
        fi
        sleep 5
    done
fi
if [[ "$HTTPS_READY" == 1 ]]; then
    printf 'HTTPS_IPV4_READY_OK https://%s/studio (also check any AAAA record before handover)\n' "$DOMAIN"
else
    printf 'HTTPS_PENDING: check DNS A/AAAA and inbound TCP ports 80/443 on the VPS firewall.\n'
fi
printf 'DOLA_LOGIN_PENDING: log in through the private browser before testing a real video.\n'
printf 'INSTALL_REPORT_END log=%s\n' "$LOG_FILE"
printf 'Secrets are in %s/.env.production. Do not paste this file into chat.\n' "$INSTALL_DIR"
