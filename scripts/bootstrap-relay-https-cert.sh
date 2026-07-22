#!/usr/bin/env bash
# One-shot bootstrap: renew relay HTTPS cert now, enable Tailscale operator
# mode for passwordless renewals, install weekly cron, restart relay.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="jjcloud.tail9d0237.ts.net"
CERT="$ROOT/certs/relay.crt"
KEY="$ROOT/certs/relay.key"
SCRIPT="$ROOT/scripts/renew-tailscale-https-cert.sh"
LOG="$ROOT/logs/cert-renew.log"
CRON_LINE="20 4 * * 0 $SCRIPT --hostname $HOST --cert $CERT --key $KEY --renew-within-days 21 --compose-dir $ROOT --compose-service relay >> $LOG 2>&1"

echo "=== Old cert ==="
openssl x509 -in "$CERT" -noout -dates -subject 2>/dev/null || echo "no cert yet"

echo "=== Enable Tailscale operator (passwordless cert renew) ==="
sudo tailscale set --operator="$USER"

echo "=== Renew cert ==="
mkdir -p "$ROOT/certs" "$ROOT/logs"
# Force renew regardless of remaining lifetime
"$SCRIPT" \
  --hostname "$HOST" \
  --cert "$CERT" \
  --key "$KEY" \
  --renew-within-days 21 \
  --compose-dir "$ROOT" \
  --compose-service relay \
  --force \
  | tee -a "$LOG"

echo "=== New cert ==="
openssl x509 -in "$CERT" -noout -dates -subject

echo "=== HTTPS probe (no -k) ==="
curl -sI --max-time 5 "https://$HOST:8787/" | head -15 || true

echo "=== Install weekly cron (Sun 04:20 UTC) ==="
existing="$(crontab -l 2>/dev/null || true)"
filtered="$(printf '%s\n' "$existing" | grep -v 'renew-tailscale-https-cert.sh' || true)"
{
  printf '%s\n' "$filtered"
  printf '%s\n' "$CRON_LINE"
} | grep -v '^$' | crontab -

echo "Installed cron line:"
echo "$CRON_LINE"
echo
echo "Current crontab:"
crontab -l
