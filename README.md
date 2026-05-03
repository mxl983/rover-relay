# Rover relay

Small **Node + Express** service that runs on a machine reachable over **Tailscale** (or the public internet). It complements the onboard stack described in [mxl983/rover](https://github.com/mxl983/rover): telemetry storage, ESP32 backup camera relay, and aggregated rover presence / boot / battery estimates.

By default this relay serves **HTTPS** (TLS).

## Why this exists

| Concern | On the Pi today | On the relay |
|--------|-------------------|--------------|
| Telemetry SQLite | `server/src/services/telemetryService.js` writes `/app/data/telemetry.db` | Same schema, central history, less SD wear on the Pi |
| Backup cam | ESP32 on LAN (`192.168.1.220:81/stream`) | HTTP proxy from a URL the **relay** can reach (often via Pi Tailscale IP + port forward, or same LAN) |
| Online / boot / battery math | Not centralized | `GET /api/rover/state` |

## Quick start

```bash
cp .env.example .env
# edit .env — set ROVER_API_TOKEN, BACKUP_CAM_STREAM_URL, CORS_ORIGINS
npm install
npm test
npm run dev
```

## HTTPS setup (recommended)

Generate cert/key for your Tailscale name on the host:

```bash
sudo mkdir -p certs
sudo tailscale cert \
  --cert-file certs/relay.crt \
  --key-file certs/relay.key \
  jjcloud.tail9d0237.ts.net
sudo chown -R $USER:$USER certs
```

Then run relay (compose already mounts `./certs` to `/certs` in the container).

If you want an HTTP listener that only redirects to HTTPS, set:

```bash
HTTP_REDIRECT_ENABLED=true
HTTP_REDIRECT_PORT=8080
```

Docker:

```bash
export ROVER_API_TOKEN='your-long-secret'
docker compose up --build -d
```

This starts both:

- relay API + telemetry dashboard on `https://<host>` (port 443) and `https://<host>:8787`
- control dashboard service on `http://<host>:5174` (default port)

## HTTPS API

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/healthz` | no | Liveness |
| GET | `/api/telemetry?limit=&since=` | no | Same shape as rover `GET /api/telemetry` |
| POST | `/api/telemetry/ingest` | Bearer if `ROVER_API_TOKEN` set | Body: `{ "health": { ... }, "event": "optional" }` — same `health` fields as [rover `recordTelemetry`](https://github.com/mxl983/rover/blob/main/server/src/services/telemetryService.js) |
| POST | `/api/telemetry/client-connection` | Bearer | Body matches rover `recordClientConnection` |
| POST | `/api/rover/heartbeat` | Bearer | Body: `{ "phase": "booting"\|"ready", "bootStartedAt": "<ISO>", "health": { "battery": 88, "videoOn": true, ... } }` |
| POST | `/api/rover/pulse` | Bearer | **Recommended:** one request = `recordTelemetry` + heartbeat (same body keys as heartbeat + `event` + full `health`) |
| GET | `/api/rover/state` | no | Online, last seen, last boot (`ready`), boot % (~50s window), battery drain estimate |
| GET | `/api/cams/backup/stream` | optional (`BACKUP_CAM_STREAM_AUTH`) | Proxies MJPEG (or raw) from `BACKUP_CAM_STREAM_URL` |

### Boot percentage

Primary mode: relay subscribes to MQTT boot topic (`rover/power/pi`) and when payload starts with `On`, it records that timestamp and computes progress as elapsed / `ROVER_BOOT_TOTAL_MS` (default **50s**).

Fallback mode: if no MQTT boot signal exists, `phase: "booting"` + `bootStartedAt` from heartbeat is still supported.

### Battery estimate

Uses heartbeats in `BATTERY_DRAIN_WINDOW_MS` (default **2 minutes**) where `videoOn` is true. Fits battery % vs time; if draining, extrapolates `estimatedMinutesRemainingActiveVideo`. Needs several samples with variation; otherwise fields are `null`.

### Backup camera URL

The relay process must be able to open `BACKUP_CAM_STREAM_URL`. If the relay is **not** on the same LAN as the ESP32, point this at something reachable (for example an `socat` or nginx stream forward on the Pi’s Tailscale IP).

## Onboard Pi: optional dual-write

Your rover already records telemetry in `server.js` on a timer and on lifecycle events. To **also** send to the relay, add a `fetch` to your relay base URL (Tailscale IP of the relay host) from the same places `recordTelemetry` runs, or call **`POST /api/rover/pulse`** every 15–30s with the same `health` object you pass to `recordTelemetry`, plus `phase` / `bootStartedAt` during boot.

Example pulse payload:

```json
{
  "health": {
    "battery": 82.1,
    "voltage": 12.3,
    "videoOn": true
  },
  "event": "health_report_scheduled",
  "phase": "ready"
}
```

Set `TELEMETRY_ENABLED=false` on the Pi only if you want to **fully** stop local SQLite there; otherwise keep it on for redundancy.

## Dashboard

- Telemetry: point your dashboard API base to the relay and use `GET /api/telemetry` (or keep the Pi for control and only query relay for history).
- Backup view: `<img src="https://<relay-tailscale>:8787/api/cams/backup/stream" />`.
- Built-in relay dashboard: open `https://<relay-tailscale>:8787/dashboard` for live status cards, battery trend, and recent telemetry rows. Charger LED status also streams on WebSocket `/ws/rover` (`relay.rover.heartbeat` messages with `rover.charging`).
- Control dashboard service: `docker-compose.yml` includes `control-dashboard` (source mirrored from [mxl983/rover dashboard](https://github.com/mxl983/rover/tree/main/dashboard)).
  - Internal service URL: `http://<relay-host>:5174`
  - Relay-proxied URL (same TLS cert/domain): `https://<relay-host>:8787/mangomate`
  - Configure via `.env` keys:
    - `CONTROL_DASHBOARD_PORT`
    - `CONTROL_DASHBOARD_BASE_PATH`
    - `CONTROL_DASHBOARD_PROXY_ENABLED`
    - `CONTROL_DASHBOARD_PROXY_BASE_PATH`
    - `CONTROL_DASHBOARD_PROXY_TARGET`
    - `CONTROL_DASHBOARD_PI_SERVER_IP`
    - `CONTROL_DASHBOARD_MQTT_HOST`
    - `CONTROL_DASHBOARD_RELAY_BASE_URL`
    - `CONTROL_DASHBOARD_BACKUP_STREAM_URL`
    - `CONTROL_DASHBOARD_CAMERA_SECRET`

## Environment

See `.env.example` and `docker-compose.yml`.

Retention controls (to prevent DB growth):

- `TELEMETRY_RETENTION_DAYS` (default `14`): telemetry, heartbeats, client connections, mqtt boot events

Important MQTT boot env vars:

- `MQTT_BOOT_ENABLED=true`
- `MQTT_BOOT_URL` (HiveMQ WSS URL)
- `MQTT_BOOT_USER`, `MQTT_BOOT_PASS`
- `MQTT_BOOT_TOPIC=rover/power/pi`
- `MQTT_BOOT_PAYLOAD_PREFIX=On`
