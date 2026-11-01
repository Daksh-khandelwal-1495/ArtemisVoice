# Docker

The root `compose.yaml` runs the whole platform. Ports and env vars follow
[the integration contract](INTEGRATION_CONTRACT.md).

| Service | Image built from | Host port | Notes |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | 5433 | Studio database (volume `studio-pgdata`) |
| `studio-api` | `voiceflow-studio/backend/Dockerfile` (context `voiceflow-studio/`) | 8000 | `DATABASE_URL` overridden to `postgres:5432`; artifacts in volume `studio-artifacts` |
| `studio-ui` | `voiceflow-studio/frontend/Dockerfile` | 3000 | Next.js production build (`next start`) |
| `artemis-api` | root `Dockerfile` | 8001 → 8000 | SQLite in volume `artemis-data`; `STUDIO_API_URL=http://studio-api:8000` |
| `artemis-dashboard` | `frontend/Dockerfile` | 5173 → 80 | nginx; proxies `/api/*` → `artemis-api:8000` |

Every service has a healthcheck. `make up` waits for all of them.

## Prerequisites

1. **Pipecat submodule.** The Studio image installs Pipecat from `voiceflow-studio/vendor/pipecat`:
   ```bash
   git submodule update --init voiceflow-studio/vendor/pipecat   # or: make setup
   ```
   `make up` checks this first. The Dockerfile also fails with the same instruction if the
   folder is empty.
2. **Keys (optional).** `studio-api` reads `voiceflow-studio/backend/.env` if it exists
   (`env_file … required: false`). `artemis-api` reads the root `.env` if it exists. Copy the
   matching `.env.example` and fill it in. Env files are passed at runtime only: every
   `.dockerignore` excludes `.env*`, so keys are never baked into an image. Without keys the stack
   still starts; `/api/health` reports which providers are configured.

## Commands

```bash
make up                 # docker compose up --build -d --wait, then prints URLs
make ps                 # status + health
make logs SERVICE=studio-api
make demo-seed          # upsert demo bots/datasets/oracles inside studio-api
make down               # stop (keeps volumes)
docker compose down -v  # stop and wipe Postgres, artifacts and Artemis SQLite
```

Useful overrides (shell env or root `.env`):

| Variable | Default | Effect |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Studio/Artemis log level (`DEBUG` shows Pipecat frames) |
| `API_CALL_MOCK` | `true` | Studio `api_call` nodes return mock responses |
| `REPLAY_LLM_SEED` | `7` | Seed for replay LLM calls |
| `STUDIO_PUBLIC_API_URL` | `http://localhost:8000` | Browser URL of the Studio API, **baked into the Studio UI and dashboard at build time** |
| `STUDIO_PUBLIC_UI_URL` | `http://localhost:3000` | Studio UI links from Artemis (build arg + `STUDIO_UI_URL`) |
| `ARTEMIS_PUBLIC_DASHBOARD_URL` | `http://localhost:5173` | "Open in Artemis" links in the Studio UI (build arg) |
| `ARTEMIS_STUDIO_API_URL` | `http://studio-api:8000` | Where `artemis-api` reaches Studio (hybrid mode: `http://host.docker.internal:8000`) |

`NEXT_PUBLIC_*` and `VITE_*` values are inlined into the browser bundles, so they must be URLs the
browser can reach (host ports), never compose service names. Rebuild after changing them:
`docker compose build studio-ui artemis-dashboard`.

## Live microphone calls (WebRTC)

Studio test calls use SmallWebRTC (aiortc): the browser sends media over UDP straight to the
API process. On **Docker Desktop for macOS and Windows** the container's ICE candidates are
private VM addresses with random UDP ports. The browser cannot reach them, so the call hangs on
"Connecting" or connects with no audio. REST, simulations, replays and diagnosis are unaffected.

For live calls, run the Studio API on the host:

- **`make dev`**: everything on the host except Postgres (recommended for the demo), or
- **hybrid**: `make up-hybrid` runs Postgres, Studio UI, Artemis API and the dashboard in Docker
  and points `artemis-api` at `host.docker.internal:8000`. Then run `make studio-api` in another
  terminal. The host API uses `DATABASE_URL=…@localhost:5433` from `backend/.env`, which is the
  same Postgres as the containers.

Alternatives, with caveats:

- `VOICE_ICE_SERVERS` (in `backend/.env`, comma-separated URLs such as
  `stun:stun.l.google.com:19302`) adds STUN-derived candidates. This helps when browser and API are
  on different networks, but does not fix Docker Desktop's NAT for UDP. Only TURN URLs without
  credentials can be expressed this way.
- `network_mode: host` makes the container use the host network on **Linux** only. On Docker
  Desktop it does not expose the host's interfaces. If you use it on Linux, drop the `ports:`
  mapping for `studio-api` and point `DATABASE_URL` at `localhost:5433`.

## Standalone Studio compose

`voiceflow-studio/docker-compose.yml` still runs only Postgres plus the Studio API (same ports;
project `voiceflow-studio`, with its own volumes). It is handy when working only on Studio
(`cd voiceflow-studio && docker compose up -d postgres`). Do not run it at the same time as the
root stack: both publish 5433 and 8000.

## Build contexts and what is excluded

- Root context (`artemis-api`): `.dockerignore` excludes `voiceflow-studio/` (51 MB of Pipecat),
  `frontend/`, `.venv`, `node_modules`, docs and every `.env*`.
- `voiceflow-studio/` context (`studio-api`): `voiceflow-studio/.dockerignore` excludes
  `backend/.env`, host `.venv`/`node_modules`, `frontend/`, runtime artifacts, and the Pipecat
  `examples/ tests/ docs/ evals/ .git`.
- `voiceflow-studio/frontend` and `frontend` contexts exclude `node_modules`, build output and `.env*`.
