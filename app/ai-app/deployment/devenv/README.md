# DevEnv (run services locally)

This setup is for **platform developers** who run services directly on the host (venv / IDE), while infra (Postgres/Redis/ClamAV/proxylogin) runs elsewhere — e.g. via `deployment/docker/local-infra-stack`.

## What this is
- **You run:** ingress, proc, metrics, frontend locally
- **Infra runs:** Postgres, Redis, ClamAV, proxylogin in a separate stack

## Sample envs
Use the files under `sample_env/`:

- `.env.ingress` → ingress service
- `.env.proc` → processor service
- `.env.metrics` → metrics service
- `.env.postgres.setup` → one‑shot schema bootstrap (managed DB)
- `.env.frontend` → UI dev server (Vite)

You can **symlink** them into the app folders or point your IDE run configs directly to these files.

## Frontend config examples
Templates live in `frontend/`:

- `frontend/config.json` (simple)
- `frontend/config.cognito.json`
- `frontend/config.hardcoded.json`

## Typical flow

1. Start infra (example):

```bash
cd deployment/docker/local-infra-stack
cp sample_env/.env ./.env
# adjust creds, then

docker compose up -d
```

2. Configure envs in this folder (replace placeholders).

3. Run services locally:

- Ingress: `apps/chat/api/web_app.py`
- Proc: `apps/chat/proc/web_app.py`
- Metrics: `apps/metrics/web_app.py`
- Frontend: run Vite using `.env.frontend`

## Notes

- `REDIS_HOST`/`POSTGRES_HOST` should point to your infra stack (often `localhost`).
- `KDCUBE_STORAGE_PATH` and `EXEC_WORKSPACE_ROOT` should be absolute paths on your host.
