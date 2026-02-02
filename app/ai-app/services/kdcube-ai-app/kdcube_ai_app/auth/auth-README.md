# Auth Overview

This module supports multiple authentication providers and several token transport
options across REST, SSE, and Socket.IO.

## Supported auth providers

1) Cognito (production)
- Implementation: [Cognito auth](../auth/implementations/cognito.py)
- Uses access token (Bearer) and optional ID token for profile/roles.

2) SimpleIDP (dev / local)
- Implementation: [SimpleIDP](../apps/middleware/simple_idp.py)
- Token-to-user mapping stored in `idp_users.json` (or `IDP_DB_PATH`).

3) Delegated auth (proxy login service)
- Proxy service build: [ProxyLogin Dockerfile](../../deployment/docker/all_in_one/Dockerfile_ProxyLogin)
- Proxy service wiring: [all-in-one compose](../../deployment/docker/all_in_one/docker-compose.yaml)
- The proxy service exchanges credentials and returns tokens; the UI stores
  access + refresh + id tokens and forwards them to the API.

## Token transport (server-side)

The gateway/auth adapters accept tokens from multiple sources. The intent is to
support:
- REST (headers, cookies)
- SSE (query params, headers, cookies)
- Socket.IO (auth payload, cookies)

### Access token (auth)
- Header: `Authorization: Bearer <access_token>`
- Cookie: `__Secure-LATC` (configurable via `AUTH_TOKEN_COOKIE_NAME`)
- SSE query param: `bearer_token`
- Socket.IO auth payload: `bearer_token`

### ID token
- Header: `X-ID-Token` (configurable via `ID_TOKEN_HEADER_NAME`)
- Cookie: `__Secure-LITC` (configurable via `ID_TOKEN_COOKIE_NAME`)
- SSE query param: `id_token`
- Socket.IO auth payload: `id_token`

### Precedence
1) Explicit transport payload (headers or auth payload for Socket.IO)
2) Query params (SSE only)
3) Cookies (fallback)

Notes:
- For SSE, query param tokens are injected into headers in
  [chat web app](../apps/chat/api/web_app.py) before gateway processing.
- For Socket.IO, the gateway session upgrade uses auth payload first, then cookies
  in [ingress chat core](../apps/chat/api/ingress/chat_core.py).

## Configuration

Common environment variables:
- `ID_TOKEN_HEADER_NAME` (default `X-ID-Token`)
- `AUTH_TOKEN_COOKIE_NAME` (default `__Secure-LATC`)
- `ID_TOKEN_COOKIE_NAME` (default `__Secure-LITC`)
- `IDP_DB_PATH` (SimpleIDP user token map)

## References
- Gateway/auth adapters:
  [gateway adapter](../apps/middleware/gateway.py),
  [auth adapter](../apps/middleware/auth.py)
- SSE + Socket.IO ingress:
  [SSE chat](../apps/chat/api/sse/chat.py),
  [Socket.IO chat](../apps/chat/api/socketio/chat.py),
  [ingress chat core](../apps/chat/api/ingress/chat_core.py)
