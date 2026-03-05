# Secrets Storage (Draft)
Date: 2026-03-05

This document proposes a single, consistent secrets model that works for:
- Local development (docker-compose and running services from CLI/IDE).
- Shared dev on EC2.
- Production (Kubernetes / managed secrets).

## Preferred Long-Term Direction (Config + Secrets Files)
We want to converge on **two files** as the canonical deployment interface:

- `config.yaml` (non-secrets, component-scoped, ConfigMap-friendly)
- `secrets.yaml` (secrets only, Secret-friendly)

Services would load both on startup and merge them into the same runtime config.
This aligns directly with Kubernetes:
- ConfigMap → `config.yaml`
- Secret → `secrets.yaml`

This also works for docker-compose and local runs by mounting or pointing to files:
- `KDCUBE_CONFIG_PATH=/path/to/config.yaml`
- `KDCUBE_SECRETS_PATH=/path/to/secrets.yaml`

The `*_FILE` model below remains a **short-term bridge** and a compatibility layer,
but the end-state should be **config + secrets files** everywhere.

## Goals
- Avoid plain-text secrets in `.env` by default.
- Keep one configuration shape across environments.
- Support both docker-compose and direct service runs (CLI/IDE).
- Work on macOS, Windows, and Linux (Ubuntu), and on AWS EC2.
- Provide a clear bootstrap path with minimal friction.

## Non-Goals
- Implement a secret manager inside KDCube.
- Support every OS-specific secret store on day one.

## Proposed Model (One Shape, Multiple Backends)
We standardize on **`*_FILE` variables** for all secrets.

Examples:
- `POSTGRES_PASSWORD_FILE=/path/to/secrets/postgres_password`
- `REDIS_PASSWORD_FILE=/path/to/secrets/redis_password`
- `OPENAI_API_KEY_FILE=/path/to/secrets/openai_api_key`

When `*_FILE` is present, the service reads the secret from the file.
If the file is missing, the service should fail fast with a clear error.

### Why `*_FILE`
- Works with docker-compose secrets.
- Works with Kubernetes secrets (mounted files).
- Works with local dev: we can store secrets as files with strict permissions.
- Keeps `.env` non-sensitive while still supporting `dotenv` flows.

## Bootstrap Path (User Choice)
During install/first run, the user chooses a **secrets backend**:

1. **Local file store (default for dev)**
   - Secrets live in `<workdir>/config/secrets/`
   - Files are `600` and directory is `700`
   - `.env` contains only `*_FILE` pointers
   - Works for docker-compose and for running services locally

2. **OS keychain (optional, later)**
   - macOS Keychain / Windows Credential Manager / Linux Secret Service
   - `.env` still uses `*_FILE`, but files are generated at runtime by a small
     helper that materializes secrets into a temp directory for the process.
   - This keeps a single config shape.

3. **Docker / ECS / Kubernetes secrets**
   - Docker compose `secrets:` or K8s Secret volumes
   - `.env` keeps `*_FILE` pointing to the mounted paths
   - Example: `/run/secrets/postgres_password`

4. **Plain env vars (allowed, but not recommended)**
   - If `POSTGRES_PASSWORD` is present, it can be used as a fallback
   - This is only for quick tests and should be documented as insecure

## Local Development (Docker Compose)
Recommended:
- Installer writes secrets to `config/secrets/*`
- Installer writes `*_FILE` variables to `.env`, `.env.ingress`, `.env.proc`, etc.
- Docker compose uses `--env-file` as it does today

This keeps secrets outside the repo and outside `.env`.

## Local Development (CLI/IDE)
Running from terminal or IntelliJ still works:
- Load the same `.env` and `*_FILE` values resolve to local files.
- The service reads the secret file at startup.

## Production (Kubernetes)
Use mounted secrets:
- `POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password`
- `OPENAI_API_KEY_FILE=/run/secrets/openai_api_key`

This uses the exact same config shape as local.

## Required Changes (Implementation Plan)
1. **Service-side support for `*_FILE`**
   - For each secret env var, add `*_FILE` support in config resolution.
   - Fail fast when `*_FILE` is set but the file is missing or unreadable.

2. **Installer**
   - Ask user for secrets
   - Store in `config/secrets/*` with secure permissions
   - Write `*_FILE` entries into `.env` files (never the secrets themselves)

3. **Docs and Samples**
   - Update sample envs to use `*_FILE`
   - Add security notes (plain env is dev-only)
   - Provide docker-compose and Kubernetes examples

## Secret Inventory (Initial)
Core / common:
- `POSTGRES_PASSWORD`
- `REDIS_PASSWORD`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `HUGGING_FACE_API_TOKEN` (optional)
- `BRAVE_API_KEY` (optional)
- `GEMINI_API_KEY` (optional)
- `OIDC_SERVICE_ADMIN_PASSWORD` (if OIDC / KB integrations enabled)
- `PGADMIN_DEFAULT_PASSWORD` (if pgAdmin used)

Integrations / optional:
- `STRIPE_SECRET_KEY` or `STRIPE_API_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` (if S3 or AWS-backed services)
- `COGNITO_CLIENTSECRET` (if ProxyLogin uses Cognito)
- `APP_NEO4J_PASSWORD` / `N4J_PASSWORD` (if Neo4j is enabled)

Each secret should have a `*_FILE` variant (e.g., `POSTGRES_PASSWORD_FILE`).

## Docker Compose Example (Secrets)
```yaml
services:
  chat-ingress:
    env_file: ./config/.env.ingress
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
      REDIS_PASSWORD_FILE: /run/secrets/redis_password
      OPENAI_API_KEY_FILE: /run/secrets/openai_api_key
    secrets:
      - postgres_password
      - redis_password
      - openai_api_key

secrets:
  postgres_password:
    file: ./config/secrets/postgres_password
  redis_password:
    file: ./config/secrets/redis_password
  openai_api_key:
    file: ./config/secrets/openai_api_key
```

## Kubernetes Example (Secrets)
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: kdcube-secrets
type: Opaque
data:
  postgres_password: <base64>
  redis_password: <base64>
  openai_api_key: <base64>
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: chat-ingress
spec:
  template:
    spec:
      containers:
        - name: chat-ingress
          env:
            - name: POSTGRES_PASSWORD_FILE
              value: /run/secrets/postgres_password
            - name: REDIS_PASSWORD_FILE
              value: /run/secrets/redis_password
            - name: OPENAI_API_KEY_FILE
              value: /run/secrets/openai_api_key
          volumeMounts:
            - name: kdcube-secrets
              mountPath: /run/secrets
              readOnly: true
      volumes:
        - name: kdcube-secrets
          secret:
            secretName: kdcube-secrets
```

## UX Notes
- Provide a clear “Secrets backend: [local files | keychain | docker/k8s]” choice.
- Always print where secrets are stored and how to rotate them.
- Never echo secret values back to the console.

## Open Questions
- Which keychain library should we standardize on for cross-platform?
- Do we want a small helper to materialize keychain secrets into temp files
  for `*_FILE` compatibility?
- How do we handle secret rotation for running services (reload vs restart)?
