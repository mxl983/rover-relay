# Rover Mission Control Dashboard

Production-grade React dashboard for controlling the Mango Rover (Pi server, ESP32, camera, drive).

In **[rover-relay](https://github.com/mxl983/rover-relay)** the Git repo root is the whole relay project and this app is the **`control-dashboard/`** subfolder. That is fine: **`npm run deploy`** runs from here and publishes only **`dist/`** to the **`gh-pages`** branch. The live URL is still **`https://mxl983.github.io/rover-relay/`** — GitHub Pages paths use the **repository name**, not whether the sources live at repo root or in a subdirectory.

## Stack

- **React 18** + **Vite 5**
- **MQTT** (HiveMQ Cloud) for auth and ESP heartbeat
- **WebSocket** for Pi stats and latency
- **WebRTC** (WHEP/WHIP) for video and audio

## Setup

```bash
cd control-dashboard
cp .env.example .env
# Edit .env with your VITE_PI_SERVER_IP, VITE_MQTT_HOST, and relay URL (optionally VITE_CAMERA_SECRET)
npm install
npm run dev
```

## Scripts

| Script | Description |
|--------|-------------|
| `npm run dev` | Start dev server |
| `npm run build` | Production build (default `base` `/`, output in `dist/`) |
| `npm run build:github-pages` | Build for **`https://mxl983.github.io/rover-relay/`** (`base` `/rover-relay/`) |
| `npm run preview` | Preview production build locally |
| `npm run preview:github-pages` | Build with `/rover-relay/` base, then preview at `/rover-relay/` |
| `npm run deploy` / `deploy:github-pages` | `build:github-pages` then push `dist/` to **`gh-pages`** on **`mxl983/rover-relay`** |
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

Live URL: **[https://mxl983.github.io/rover-relay/](https://mxl983.github.io/rover-relay/)**

Same repo as source: **[mxl983/rover-relay](https://github.com/mxl983/rover-relay)**. Deploy pushes **`dist/`** to the **`gh-pages`** branch using your repo’s **`origin`** remote (run **`git remote get-url origin`** from the relay repo — it must point at **`mxl983/rover-relay`**). A **`.nojekyll`** file is added so GitHub Pages does not run Jekyll on the static assets.

1. **Relay CORS** — In relay `.env`, include **`https://mxl983.github.io`** in `CORS_ORIGINS` so the hosted dashboard can call your relay.
2. **Git auth** — You must be able to **`git push`** to **`mxl983/rover-relay`** (HTTPS + PAT or SSH). Fix Cursor credential-helper issues first if `npm run deploy` fails (see troubleshooting below).
3. **Publish** — From `control-dashboard/` with `.env` set for Pi / relay / MQTT:
   ```bash
   cd control-dashboard
   npm run deploy
   ```
   This builds with **`VITE_BASE_PATH=/rover-relay/`**, then [gh-pages](https://github.com/tschaub/gh-pages) pushes **`dist/`** to **`origin`** (your GitHub remote).
4. **Enable Pages** — Repo **[rover-relay](https://github.com/mxl983/rover-relay)** → **Settings → Pages** → branch **`gh-pages`**, folder **`/ (root)`**.

Preview locally: `npm run preview:github-pages`.

If the **`gh-pages`** branch on GitHub still shows **extra** folders (for example `control-dashboard/`, `vendor-rover/`) from an old publish, run **`npm run deploy`** again after pulling latest **`package.json`** — deploy uses **`--remove "**/*"`** so each publish replaces the whole branch with **`dist/`** only (plus `.nojekyll`).

To deploy into a **different** repo, set **`git remote origin`** to that repo (or edit **`deploy:github-pages`** to pass **`gh-pages -r <repo-url>`**) and set **`VITE_BASE_PATH`** to `/<that-repo-name>/`.

### Troubleshooting: “I don’t see a deployment on GitHub”

1. **`gh-pages` is a branch, not a GitHub “Deployment”**  
   The [gh-pages](https://github.com/tschaub/gh-pages) CLI **pushes a new branch** called `gh-pages`. It does **not** create an entry on the **Actions → Deployments** / Environments view (that is mostly for **GitHub Actions**).  
   **Check:** on the repo **Code** tab, open the **branch dropdown** and look for **`gh-pages`**. If that branch does not exist, the push did not happen.

2. **Push target = `git remote get-url origin`**  
   You need **push** access to whatever repo **`origin`** points to (expected: **`mxl983/rover-relay`**). If you changed remotes or see **“Remote url mismatch”** from `gh-pages`, run **`npx gh-pages-clean`** (or delete **`node_modules/.cache/gh-pages`**) and deploy again.

3. **Turn on GitHub Pages** — open repo **`rover-relay`**  
   **Settings → Pages** → branch **`gh-pages`**, folder **`/ (root)`**. Site: **`https://mxl983.github.io/rover-relay/`**.

4. **Run deploy from `control-dashboard/`**  
   So `dist/` is built in the right place.

5. **“Authentication failed” / `ECONNREFUSED` … `vscode-git-…sock` / `No anonymous write access`**  
   Pushing to GitHub needs **credentials**. If Git is set to use **Cursor/VS Code’s** credential helper, it talks to a socket that often **does not exist** in a normal terminal, so the push fails.

   **Option A — SSH (recommended for daily use)**  
   1. [Add an SSH key to GitHub](https://docs.github.com/en/authentication/connecting-to-github-with-ssh).  
   2. Ensure `ssh -T git@github.com` works, then run `npm run deploy` again.

   **Option B — HTTPS with a token**  
   1. Create a [Personal Access Token](https://github.com/settings/tokens) with `repo` scope.  
   2. Push once from a terminal (not relying on the VS Code helper), e.g. use **GitHub CLI**: `gh auth login`, then `gh auth setup-git`, then `npm run deploy`.  
   Or use **Git’s credential store** for HTTPS so a normal prompt or saved token is used instead of the missing socket:
   ```bash
   git config --global credential.helper store
   # then: git push (or npm run deploy) and paste PAT when asked for password
   ```

   **Option C**  
   Run `npm run deploy` from **Cursor’s integrated terminal** (where the VS Code Git helper may be available). This is flakier than A or B.

   To see which helper is in play: `git config --show-origin --get-all credential.helper`
