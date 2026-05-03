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

Repo: **[mxl983/rover-relay](https://github.com/mxl983/rover-relay)**.

### Recommended: GitHub Actions (use this if the site is 404)

Branch-only publishing (**Settings → Pages → Deploy from branch → `gh-pages`**) sometimes never serves the site (**`404`** at `https://mxl983.github.io/rover-relay/` even when [commits exist on `gh-pages`](https://github.com/mxl983/rover-relay/commits/gh-pages)). The reliable fix is GitHub’s **Actions → GitHub Pages** integration:

1. **Settings → Pages → Build and deployment → Source:** choose **GitHub Actions** (not “Deploy from a branch”).
2. Merge/push **`.github/workflows/deploy-github-pages.yml`** on **`main`**. It installs **`control-dashboard`**, runs **`npm run build:github-pages`** with **`VITE_BASE_PATH=/rover-relay/`**, and deploys **`dist/`**.
3. Open **Actions**, run **Deploy GitHub Pages**, and approve the **`github-pages`** environment if GitHub asks on first run.

After a green run, the live URL should respond (**may take a minute**).

### Optional: publish from your machine (`gh-pages` branch)

You can still run **`npm run deploy`** from **`control-dashboard/`** — it uses [gh-pages](https://github.com/tschaub/gh-pages) to push **`dist/`** to the **`gh-pages`** branch (`--nojekyll`, **`--remove "**/*"`**). That only affects the branch; **GitHub will only serve it if Pages source is still “Deploy from branch.”** If you switched to **GitHub Actions**, use pushes to **`main`** (or **workflow_dispatch**) instead.

**Relay CORS** — In relay `.env`, include **`https://mxl983.github.io`** in **`CORS_ORIGINS`**.

Preview locally: **`npm run preview:github-pages`**.

To deploy into a **different** repo, set **`git remote origin`** to that repo (or edit **`deploy:github-pages`** to pass **`gh-pages -r <repo-url>`**) and set **`VITE_BASE_PATH`** to `/<that-repo-name>/`.

### Troubleshooting: “I don’t see a deployment on GitHub”

1. **`gh-pages` is a branch, not a GitHub “Deployment”**  
   The [gh-pages](https://github.com/tschaub/gh-pages) CLI **pushes a new branch** called `gh-pages`. It does **not** create an entry on the **Actions → Deployments** / Environments view (that is mostly for **GitHub Actions**).  
   **Check:** on the repo **Code** tab, open the **branch dropdown** and look for **`gh-pages`**. If that branch does not exist, the push did not happen.

2. **Push target = `git remote get-url origin`**  
   You need **push** access to whatever repo **`origin`** points to (expected: **`mxl983/rover-relay`**). If you changed remotes or see **“Remote url mismatch”** from `gh-pages`, run **`npx gh-pages-clean`** (or delete **`node_modules/.cache/gh-pages`**) and deploy again.

3. **Turn on GitHub Pages** — repo **`rover-relay`**  
   Prefer **Source: GitHub Actions** (see above). Legacy option: **branch `gh-pages`**, folder **`/ (root)`** — if you still get **404** on the live URL, switch to **Actions**.

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
