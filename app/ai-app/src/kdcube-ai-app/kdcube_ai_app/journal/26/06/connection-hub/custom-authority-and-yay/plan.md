# Custom Authority And Yey Migration Battle Plan

## Current Finding

The current `oauth_mcp` feature is not "bundle MCP with OAuth". It is a
shortcut that combines three concerns:

1. OAuth authorization server and consent flow (`/.well-known/oauth-*`,
   `/oauth/*`).
2. Delegated credential storage and token issuance.
3. A root platform MCP JSON-RPC server at `/mcp`, mounted on chat-ingress and
   hardcoded around `conversations_export`.

KDCube's normal MCP model is different: MCP endpoints are bundle surfaces served
by chat-proc through:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{endpoint_alias}
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{endpoint_alias}
```

Therefore the current ingress `/mcp` must be treated as a temporary adapter, not
as the product architecture.

## Target Model

```text
External client / channel / browser
  -> auth material
  -> Connection Hub authority selector
  -> authenticator verifies proof
  -> linker projects to required authority if needed
  -> delegated credential or session is issued
  -> guarded surface checks required authority + grants
```

OAuth2, Telegram, Google, Slack, and custom Yey identity are different
authenticator/authority cases under Connection Hub. MCP is only one possible
guarded surface type and must stay where surfaces live: bundle/proc, unless we
intentionally introduce a separate platform-owned MCP product later.

## Work Plan

| # | Area | What Must Change | Platform Tasks | Yey Tasks | Validation | Status |
|---|------|------------------|----------------|-----------|------------|--------|
| 1 | Vocabulary | Stop treating `oauth_mcp` as the core concept. The durable concept is delegated external-client credentials under Connection Hub. | Keep OAuth protocol code under `sdk/solutions/connections/delegated_credentials/oauth_mcp`, but docs must frame it as one protocol adapter for delegated credentials. | Stop documenting the feature as a platform `/mcp` server. Document it as "Claude/external client obtains delegated credential, then calls a guarded bundle MCP surface." | Docs no longer imply KDCube has a generic platform MCP. | Started |
| 2 | Surface ownership | Remove the architectural dependency on ingress root `/mcp`. | Keep OAuth discovery/authorize/token routes as the public protocol facade. Move actual tool execution out of `ingress/oauth_mcp/mcp_server.py` into a bundle/proc MCP surface, or replace root `/mcp` with a temporary compatibility shim that proxies to the configured bundle MCP endpoint only during migration. | Route Claude's protected resource URL to the real proc bundle MCP URL, not `/mcp`. | `POST /mcp` is no longer the primary tool execution path. Bundle MCP route handles `initialize`, `tools/list`, `tools/call`. | Open |
| 3 | Delegated credential SDK | Credential issuing and grant checks must be reusable by any surface. | Generalize token/grant store and authority envelope in `connections/delegated_credentials`. Surface guards should call SDK APIs, not import ingress route helpers. | Use the platform SDK contract instead of local patch files. | Unit tests: consented tool allowed, unchecked tool denied, missing grant denied for non-admin. | Partially started |
| 4 | Custom authorities | Yey's `kdcube_ext` session/role provider is a custom authority, not a gateway monkeypatch. | Add SDK registry for custom authorities discovered from bundle manifests/load hooks, similar in spirit to named services. Runtime reachability rule: proc can execute bundle-local custom authority code; ingress can only resolve built-in/shared authorities or short-lived ingress-session credentials. | Register Yey identity authority/provider through the SDK instead of patching `AUTH_PROVIDER=session` into the platform gateway. | A Yey-issued credential resolves on proc bundle surfaces and does not pretend to be a platform Cognito session. | Open |
| 5 | Auth selector | Request auth should be a Connection Hub SDK concern, not scattered middleware logic. | Move `RequestAuthResolver`/selector surface under `connections.authentication` and make middleware use it as a single object. Keep cheap prefiltering inside that surface, not in ad hoc gateway code. | No direct work unless they expose custom proof material. | Anonymous public bundle routes still pass through; proof-bearing requests resolve authority when needed. | Open |
| 6 | Linker/authority projection | Incoming identity and required surface authority must be resolved only as needed. | Surface metadata should declare required authority/grants. Linker maps verified actor identity to the required authority identity, or returns null. | Define which Yey APIs require Yey authority, platform authority, or both. Avoid mapping everything to platform user by default. | A Telegram identity linked to platform can pass platform-role economics; a Yey identity can pass Yey-grant surfaces. | Open |
| 7 | Ingress session bridge | Data Bus/Event Bus need short-lived ingress-resolvable sessions, not bundle-local custom code. | Keep standardized federated Data Bus token under `connections/federated_tokens`. Token resolves to `UserSession` on ingress. It may carry projected platform roles if the actor is linked. | If Yey needs Data Bus from custom UI/channel, request a short-lived ingress session from Connection Hub first. | Telegram Mini App opens Data Bus before/after link; linked session projects platform authority; unlinked session stays registered-only. | Implemented for Data Bus, needs docs polish |
| 8 | Descriptors | Configuration must be descriptor-backed, not environment-patched. | Move remaining OAuth/delegated credential config under `auth.connection_hub.delegated_credentials.*` and document reference descriptors. Add bundle surface metadata for authority/grant requirements where applicable. | Remove `AUTH_PROVIDER=session` and direct env/token assumptions as platform mechanism. Keep deployment-specific secrets in descriptor/secrets service. | Reference descriptors are enough to reproduce the flow locally and in cloud. | Started |
| 9 | Reference bundle | We need a platform-native example before asking Yey to migrate. | Add or move a reference Connection Hub/Versatile bundle that exposes a proc MCP endpoint protected by delegated credentials. It should demonstrate Claude-style consent and tool allow-list enforcement without root `/mcp`. | Use the reference as the migration pattern for navigator/admin export. | We can run Claude/external client against the reference bundle MCP endpoint. | Open |
| 10 | Yey migration PR | Replace repo-local patches with platform-supported mechanisms. | Provide final docs, SDK APIs, and migration notes. | Open PR in `navigator-tg-bot`: route real bundle MCP, remove obsolete hotpatches, register Yey authority, adapt frontend/login to descriptor-backed authority. | Existing Boris screenshots flow still works, but without platform `/mcp` shortcut or monkeypatch auth provider. | Open |

## Yey Current Behavior To Replace

| Component | Current Behavior | Why It Is Not Final |
|-----------|------------------|---------------------|
| Proxy route | `/.well-known/oauth-*`, `/oauth/*`, `/mcp` route to `chat_api`/chat-ingress. | `/mcp` is a platform root resource, but KDCube MCP surfaces are bundle/proc surfaces. |
| Session auth | `AUTH_PROVIDER=session` loads `kdcube_ext.auth.session_manager.SessionTokenAuthManager`. | This is a custom authority implemented as a gateway auth monkeypatch. It should register through Connection Hub authority infrastructure. |
| OAuth access token | OAuth flow mints a bundle-session `kst1` access token with feedback-reader role and grant metadata in Redis. | Good concept, wrong placement/name: it should be a delegated credential under Connection Hub and usable by proc surface guards. |
| Tool execution | `ingress/oauth_mcp/mcp_server.py` runs `conversations_export` directly. | Tool execution belongs to a bundle MCP endpoint or a deliberately introduced platform surface, not accidental ingress code. |
| Patches/hotpatches | Yey carries patch notes and cloud hotpatches around `oauth_mcp`, session auth, and export adapter fixes. | These should disappear once the platform owns delegated credentials and custom authorities cleanly. |

## Immediate Next Steps

| Order | Action | Done When |
|-------|--------|-----------|
| 1 | Write/align docs that state the split: delegated credential protocol vs guarded surface. | Connection Hub docs and auth docs no longer present OAuth-MCP as the product architecture. |
| 2 | Refactor remaining ingress `oauth_mcp` modules so only protocol HTTP facade remains there. | Ingress has discovery/authorize/token/register; tool execution is not hardcoded there. |
| 3 | Design and implement proc/bundle delegated-credential guard. | Bundle MCP/API operation can declare required authority/grants and receive resolved principal. |
| 4 | Build reference bundle MCP endpoint for `conversations_export` or equivalent demo. | External client can call bundle MCP endpoint after OAuth consent. |
| 5 | Move Yey custom session auth into custom authority registration path. | `AUTH_PROVIDER=session` patch is no longer needed for Yey external-client access. |
| 6 | Open Yey migration PR. | Their deployment routes and docs use the platform-supported flow. |

