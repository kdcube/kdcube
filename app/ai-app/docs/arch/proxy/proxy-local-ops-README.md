---
id: ks:docs/arch/proxy/proxy-local-ops-README.md
title: "Proxy Local Ops"
summary: "Ops guide for the OpenResty reverse proxy in local/all-in-one Docker Compose setup: responsibilities, routing, CORS, and differences from the EC2/SSL deployment."
tags: ["proxy", "openresty", "ops", "local", "docker-compose", "nginx", "dev"]
keywords: ["OpenResty", "nginx_proxy.conf", "all_in_one_kdcube", "docker-compose", "CORS", "no-auth", "local dev", "HTTP"]
see_also:
  - ks:docs/arch/proxy/proxy-ops-README.md
  - ks:docs/arch/proxy/nginx_proxy.conf
  - ks:docs/arch/configuration/bundle-configuration-README.md
---
# Proxy Local Ops Guide (OpenResty — local / all-in-one)

This guide covers the **local development and all-in-one Docker Compose** deployment of the OpenResty proxy.

For the **production (EC2 + SSL + proxylogin)** setup, see:
[docs/arch/proxy/proxy-ops-README.md](proxy-ops-README.md).

---

## Key differences from the EC2 deployment

| | Local (this doc) | EC2 / SSL |
|---|---|---|
| SSL/TLS | None — HTTP :80 only | TLS termination, HTTPS redirect |
| Auth | None — no `proxylogin` | Cookie unmask via `proxylogin` |
| `server_name` | `_` (any host) | Explicit domain |
| IP block | Not present | Direct IP → `444` |
| HSTS | Not present | Present |
| CORS | Handled at proxy for integrations routes | Delegated to backend |
| Infrastructure | postgres, redis, pgadmin included | External (managed infra) |
| Backends | 4 (no `proxylogin`) | 5 (includes `proxylogin`) |
| Port exposure | `:80` only | `:80` + `:443` |

---

## Responsibilities overview

The local proxy runs inbound requests through four phases (SSL and auth unmask phases are absent):

1. **Security headers + compression** — injects `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`; gzip on all text responses. HSTS is omitted (no TLS).
2. **Rate limiting** — `limit_req` zones defined for chat, KB, upload, and monitoring routes. Currently commented out on most locations — enable per location as needed.
3. **CORS preflight handling** — `OPTIONS` responses handled at the proxy for `/api/integrations/`, `/admin/integrations/`, `/api/opex/`, and `/api/admin/control-plane`. This is a local-only concern; on EC2 the backend handles CORS.
4. **Path-based routing** — dispatches to four upstream backends by URL prefix.
5. **Protocol handling** — SSE (buffering off, 600 s timeout), WebSocket upgrade, SPA 404 fallback.

There is no TLS termination and no auth unmask step. The proxy accepts any `Host` header (`server_name _`), which is appropriate for local development but must never be used in production.

---

## Upstream backends

| Backend | Address | Routes |
|---|---|---|
| `web-ui` | `web-ui:80` | `/chatbot/*`, SPA fallback |
| `chat-ingress` | `chat-ingress:8010` | `/sse/`, `/api/chat/`, `/api/cb/*`, `/admin/*`, `/monitoring`, `/cb/socket.io/`, `/api/opex/`, `/api/admin/control-plane` |
| `chat-proc` | `chat-proc:8020` | `/api/integrations/`, `/admin/integrations/` |
| `kb` | `kb:8000` | `/api/kb/` (dynamic `$kb_backend` variable) |

`proxylogin` is **not present** in this deployment. The `/auth/*` route group does not exist.

---

## Docker Compose topology

The all-in-one compose includes managed infrastructure that is external in the EC2 deployment:

| Service | Image | Purpose |
|---|---|---|
| `postgres-db` | `pgvector/pgvector:pg16` | Primary database |
| `pgadmin` | `dpage/pgadmin4` | DB admin UI at `:5050` |
| `redis` | `redis:latest` | Cache + pub/sub |
| `kdcube-secrets` | `kdcube-secrets` | Secrets sidecar |
| `clamav` | `clamav/clamav:stable` | Antivirus (required by `chat-ingress`) |
| `postgres-setup` | `kdcube-postgres-setup` | One-shot schema migration |
| `chat-ingress` | `kdcube-chat-ingress` | Chat API + SSE gateway |
| `chat-proc` | `kdcube-chat-proc` | Agentic processor |
| `metrics` | `kdcube-metrics` | Metrics service at `:8090` |
| `web-ui` | `kdcube-web-ui` | React SPA |
| `web-proxy` | `kdcube-web-proxy` | This proxy (OpenResty) |

`proxylogin` and `kb`/`dramatiq` services are commented out in this compose file.

### Network layout

```
kdcube-external   ← web-proxy only (receives external traffic)
kdcube-internal   ← all application services
kdcube-secrets    ← kdcube-secrets sidecar + services that read secrets (internal: true)
```

Unlike the EC2 compose, `chat-ingress` and `chat-proc` ports are bound without `127.0.0.1:` prefix, so they are accessible on all interfaces on the host. Tighten to `127.0.0.1:` if exposing the host to a network.

### Startup order

```
postgres-db (healthy)
  └─ postgres-setup (completed)
       └─ chat-ingress ──┐
  redis (healthy)         ├─ web-proxy
       └─ chat-proc      │
  web-ui ────────────────┘
  kdcube-secrets (started) → chat-ingress, chat-proc
  clamav (healthy) → chat-ingress
```

---

## Proxy image (Dockerfile)

The proxy image is a thin wrapper over `openresty/openresty:alpine`. The config is baked in at build time via a build arg:

```dockerfile
FROM openresty/openresty:alpine
ARG NGINX_CONFIG_FILE_PATH
COPY ${NGINX_CONFIG_FILE_PATH} /usr/local/openresty/nginx/conf/nginx.conf
EXPOSE 80
CMD ["openresty", "-g", "daemon off;"]
```

The same Dockerfile is used for both local and EC2 deployments. The difference is which config file is passed via `NGINX_CONFIG_FILE_PATH`:
- **Local**: `nginx_proxy.conf` (no SSL, no Lua auth block)
- **EC2**: `nginx_proxy_ssl_cognito.conf` (SSL + proxylogin Lua)

At runtime the config can also be overridden by mounting a file over `/usr/local/openresty/nginx/conf/nginx.conf` via the compose volume:

```yaml
volumes:
  - ${NGINX_PROXY_RUNTIME_CONFIG_PATH:-./config/nginx_proxy.conf}:/usr/local/openresty/nginx/conf/nginx.conf:ro
```

This means you can swap configs without rebuilding the image, which is useful for local iteration.

---

## CORS handling

The local config handles `OPTIONS` preflight responses at the proxy layer for routes that front-end tooling calls cross-origin during development. EC2 does not do this — CORS is handled by the backend there.

Affected routes:

```nginx
location ^~ /api/integrations/ { ... }
location /admin/integrations/  { ... }
location ^~ /api/opex/         { ... }
location ^~ /api/admin/control-plane { ... }
```

Each applies the same preflight block:

```nginx
if ($request_method = OPTIONS) {
    add_header Access-Control-Allow-Origin      $http_origin always;
    add_header Access-Control-Allow-Methods     "GET, POST, PUT, PATCH, DELETE, OPTIONS" always;
    add_header Access-Control-Allow-Headers     "Accept,Authorization,Cache-Control,Content-Type,DNT,If-Modified-Since,Keep-Alive,Origin,User-Agent,X-Requested-With" always;
    add_header Access-Control-Allow-Credentials "true" always;
    return 204;
}
```

If you add a new route that the front end calls cross-origin in local dev, add this block to that location.

---

## Rate limiting

The same zones as the EC2 deployment are defined but most `limit_req` directives are commented out. For local development this is generally fine. If you are load-testing locally, uncomment per location:

```nginx
limit_req zone=chat_api_zone burst=20 nodelay;
limit_req_status 429;
```

---

## Enabling proxylogin locally

`proxylogin` is commented out in the all-in-one compose but is supported. To enable:

1. Uncomment the `proxylogin` service block in `docker-compose.yml`.
2. Uncomment the `proxylogin` entry in `web-proxy.depends_on`.
3. Switch the runtime config to the delegated-auth variant:
   ```
   NGINX_PROXY_RUNTIME_CONFIG_PATH=./config/nginx_proxy_ssl_delegated_auth.conf
   ```
4. Provide `.env.proxylogin` with the required Cognito / auth configuration.

Without step 3, OpenResty will fail to start because the SSL/delegated-auth config references `proxy_login` upstream which would not exist.

---

## References (code)

- Proxy config (local): `deployment/docker/all_in_one_kdcube/nginx_proxy.conf`
- Proxy config (EC2): `deployment/docker/custom-ui-managed-infra/nginx_proxy_ssl_cognito.conf`
- Proxy Dockerfile: `deployment/docker/Dockerfile_ProxyOpenResty`
- All-in-one compose: `deployment/docker/all_in_one_kdcube/docker-compose.yml`
- EC2 compose: `deployment/docker/custom-ui-managed-infra/docker-compose.yml`
- Chat ingress: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/`
- Chat processor: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py`