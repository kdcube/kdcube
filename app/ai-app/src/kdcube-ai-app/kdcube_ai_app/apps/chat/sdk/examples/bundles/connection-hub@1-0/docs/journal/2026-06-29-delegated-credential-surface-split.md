---
id: kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/docs/journal/2026-06-29-delegated-credential-surface-split.md
title: "2026-06-29 - Delegated Credential Surface Split"
summary: "Connection Hub work split the OAuth delegated credential shortcut into delegated credential authority concerns and actual bundle/proc surface ownership."
status: active
tags: ["connection-hub", "delegated-credentials", "custom-authority", "mcp", "custom-authority"]
---

# 2026-06-29 - Delegated Credential Surface Split

## Problem

The current OAuth delegated credential integration started as a working external-client path for
Claude-style feedback export. It proved the product need, but it mixed three
separate responsibilities:

- OAuth authorization, consent, token, refresh, and dynamic client registration;
- delegated credential/grant storage;
- root `/mcp` tool execution mounted on chat-ingress.

That last point is the mismatch. KDCube's application MCP surfaces are bundle
surfaces served by chat-proc. Connection Hub should not normalize an accidental
"platform MCP" concept unless we explicitly design one.

## Decision

Connection Hub owns the identity/authority/delegation concepts:

```text
auth material
  -> selector
  -> authenticator
  -> linker / authority projection
  -> delegated credential or session
  -> guarded surface checks required authority + grants
```

MCP is only a guarded surface type. If a bundle exposes MCP, the endpoint lives
in the bundle/proc integration layer. OAuth is only one protocol adapter that can
issue delegated credentials for an external client to call that surface.

## Consequence

The implementation must continue moving in this direction:

- SDK-owned Connection Hub modules contain the reusable authority, identity,
  resolver, federated token, and delegated credential logic.
- The Connection Hub bundle stays thin: UI, public operations, and bootstrap
  wiring on top of SDK logic.
- Ingress may host public OAuth protocol endpoints, but tool execution should
  not live in ingress as the default architecture.
- Custom authorities such as custom identity should be registered through the
  Connection Hub authority model, not through repo-local gateway monkeypatches.

## Reference Plan

The detailed migration plan is tracked in the platform journal:

```text
repo:kdcube-ai-app:app/ai-app/src/kdcube-ai-app/kdcube_ai_app/journal/26/06/connection-hub/custom-authority/plan.md
```

Use that plan before changing the custom integration or removing the temporary
root `/mcp` shortcut.
