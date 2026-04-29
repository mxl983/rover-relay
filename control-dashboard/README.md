# Rover Mission Control Dashboard

Production-grade React dashboard for controlling the Mango Rover (Pi server, ESP32, camera, drive).

## Stack

- **React 18** + **Vite 5**
- **MQTT** (HiveMQ Cloud) for auth and ESP heartbeat
- **WebSocket** for Pi stats and latency
- **WebRTC** (WHEP/WHIP) for video and audio

## Setup

```bash
cd dashboard
cp .env.example .env
# Edit .env with your VITE_PI_SERVER_IP, VITE_MQTT_HOST, and relay URL (optionally VITE_CAMERA_SECRET)
npm install
npm run dev
```

## Scripts

| Script | Description |
|--------|-------------|
| `npm run dev` | Start dev server |
| `npm run build` | Production build (output in `dist/`) |
| `npm run preview` | Preview production build locally |
| `npm run deploy` | Deploy `dist/` to GitHub Pages (`gh-pages -d dist`) |
| `npm run test` | Run Vitest once |
| `npm run test:watch` | Run Vitest in watch mode |
| `npm run lint` | Run ESLint on `src/` |

## Environment variables

All client-exposed config uses the `VITE_` prefix (Vite requirement).

| Variable | Description | Default |
|----------|-------------|---------|
| `VITE_PI_SERVER_IP` | Pi server host (no protocol) | `rover.tail9d0237.ts.net` |
| `VITE_MQTT_HOST` | MQTT broker WSS URL | HiveMQ Cloud URL in repo |
| `VITE_RELAY_BASE_URL` | Relay base URL (backup stream + telemetry history) | `https://jjcloud.tail9d0237.ts.net` |
| `VITE_BACKUP_STREAM_URL` | Optional direct backup stream endpoint override | `https://jjcloud.tail9d0237.ts.net:8787/api/cams/backup/stream` |
| `VITE_CAMERA_SECRET` | Optional camera API secret (must match server) | (none) |

- Copy `.env.example` to `.env` and set values. Do not commit `.env`.
- For production builds, set env vars in your CI or host (e.g. GitHub Actions, Netlify).

## Production notes

- **Config**: Centralized in `src/config.js`; read from `import.meta.env` with fallbacks.
- **API**: Shared client in `src/api/client.js` (timeout, retries, JSON helpers). Camera/control/system calls use it.
- **Auth**: Session is in-memory via `RoverSessionContext`; logout clears credentials.
- **Security**: Camera secret should be set via `VITE_CAMERA_SECRET` or handled server-side. Capture URL is validated to same origin before `window.open`.
- **Errors**: `ErrorBoundary` catches render errors; API/action errors surface in a dismissible banner.
- **Tests**: Vitest + React Testing Library; run `npm run test` before deploy.

## Deploy (GitHub Pages)

Build uses `base: "/rover/"` for repo subpath. After `npm run build`, `npm run deploy` pushes `dist/` to the `gh-pages` branch.
