#!/usr/bin/env bash
# Renew Tailscale HTTPS cert for the rover relay when nearing expiry.
# Safe for cron: skips when remaining lifetime is above the threshold.
set -euo pipefail

HOSTNAME="jjcloud.tail9d0237.ts.net"
CERT_PATH=""
KEY_PATH=""
RENEW_WITHIN_DAYS=21
COMPOSE_DIR=""
COMPOSE_SERVICE="relay"
FORCE=0

usage() {
  cat <<'EOF'
Usage: renew-tailscale-https-cert.sh [options]

Options:
  --hostname NAME              Tailscale MagicDNS / cert CN (required)
  --cert PATH                  Destination certificate path (required)
  --key PATH                   Destination private key path (required)
  --renew-within-days N        Renew when fewer than N days remain (default: 21)
  --compose-dir PATH           docker compose project directory (required to restart)
  --compose-service NAME       Service to restart after install (default: relay)
  --force                      Renew even if still fresh
  -h, --help                   Show this help
EOF
}

log() {
  printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname)
      HOSTNAME="${2:-}"; shift 2 ;;
    --cert)
      CERT_PATH="${2:-}"; shift 2 ;;
    --key)
      KEY_PATH="${2:-}"; shift 2 ;;
    --renew-within-days)
      RENEW_WITHIN_DAYS="${2:-}"; shift 2 ;;
    --compose-dir)
      COMPOSE_DIR="${2:-}"; shift 2 ;;
    --compose-service)
      COMPOSE_SERVICE="${2:-}"; shift 2 ;;
    --force)
      FORCE=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      die "unknown argument: $1" ;;
  esac
done

[[ -n "$HOSTNAME" ]] || die "--hostname is required"
[[ -n "$CERT_PATH" ]] || die "--cert is required"
[[ -n "$KEY_PATH" ]] || die "--key is required"
[[ "$RENEW_WITHIN_DAYS" =~ ^[0-9]+$ ]] || die "--renew-within-days must be an integer"
[[ -n "$COMPOSE_DIR" ]] || die "--compose-dir is required"
[[ -d "$COMPOSE_DIR" ]] || die "compose dir not found: $COMPOSE_DIR"

SECONDS_WITHIN=$((RENEW_WITHIN_DAYS * 86400))

if [[ "$FORCE" -eq 0 && -f "$CERT_PATH" ]]; then
  if openssl x509 -in "$CERT_PATH" -noout -checkend "$SECONDS_WITHIN" >/dev/null 2>&1; then
    not_after="$(openssl x509 -in "$CERT_PATH" -noout -enddate 2>/dev/null | cut -d= -f2- || true)"
    log "skip: cert still valid beyond ${RENEW_WITHIN_DAYS}d (notAfter=${not_after:-unknown})"
    exit 0
  fi
fi

if [[ -f "$CERT_PATH" ]]; then
  old_not_after="$(openssl x509 -in "$CERT_PATH" -noout -enddate 2>/dev/null | cut -d= -f2- || echo unknown)"
  log "renewing: current notAfter=${old_not_after}"
else
  log "renewing: no existing cert at $CERT_PATH"
fi

command -v tailscale >/dev/null 2>&1 || die "tailscale not found in PATH"
command -v openssl >/dev/null 2>&1 || die "openssl not found in PATH"
command -v docker >/dev/null 2>&1 || die "docker not found in PATH"

cert_dir="$(dirname -- "$CERT_PATH")"
key_dir="$(dirname -- "$KEY_PATH")"
mkdir -p -- "$cert_dir" "$key_dir"

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/relay-cert-renew.XXXXXX")"
cleanup() {
  rm -rf -- "$tmp_dir"
}
trap cleanup EXIT

tmp_cert="$tmp_dir/relay.crt"
tmp_key="$tmp_dir/relay.key"

# Prefer operator mode (no root). Only use non-interactive sudo as a fallback
# so cron never blocks on a password prompt.
if ! tailscale cert --cert-file "$tmp_cert" --key-file "$tmp_key" "$HOSTNAME" 2>"$tmp_dir/cert.err"; then
  if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo tailscale cert --cert-file "$tmp_cert" --key-file "$tmp_key" "$HOSTNAME"
  else
    err="$(tr '\n' ' ' <"$tmp_dir/cert.err" | sed 's/[[:space:]]\+/ /g')"
    die "tailscale cert failed (${err:-access denied}). Run once: sudo tailscale set --operator=\$USER"
  fi
fi

[[ -s "$tmp_cert" && -s "$tmp_key" ]] || die "tailscale cert produced empty files"
openssl x509 -in "$tmp_cert" -noout -subject >/dev/null 2>&1 || die "new cert failed openssl parse"

# Atomic install: write siblings then rename into place.
install_cert="$cert_dir/.relay.crt.new.$$"
install_key="$key_dir/.relay.key.new.$$"
cp -f -- "$tmp_cert" "$install_cert"
cp -f -- "$tmp_key" "$install_key"
chmod 644 -- "$install_cert"
chmod 600 -- "$install_key"
mv -f -- "$install_cert" "$CERT_PATH"
mv -f -- "$install_key" "$KEY_PATH"

new_not_after="$(openssl x509 -in "$CERT_PATH" -noout -enddate | cut -d= -f2-)"
log "installed: notAfter=${new_not_after}"

(
  cd -- "$COMPOSE_DIR"
  docker compose restart "$COMPOSE_SERVICE"
)
log "restarted docker compose service: $COMPOSE_SERVICE"
