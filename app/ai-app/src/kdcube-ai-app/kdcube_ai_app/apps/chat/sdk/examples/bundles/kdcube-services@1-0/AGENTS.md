---
id: kdcube-services@1-0/agents
title: "KDCube Services — Builder-Agent Onboarding"
summary: "How to work on the built-in KDCube service bundle: thin surface wiring, modular service packages, managed delegated-credential MCP auth."
status: active
tags: ["agents", "builder", "onboarding", "mcp", "delegated-credentials", "connection-hub"]
see_also:
  - "README.md"
  - "interface/README.md"
  - "docs/README.md"
---

# KDCube Services — Builder-Agent Onboarding

## Read First

- `README.md` — product role and current service list.
- `interface/README.md` — public MCP URL, auth policy, tools, dataflow.
- `entrypoint.py` — thin bundle/surface adapter.
- `services/` — product service modules.
- `surfaces/mcp/` — service-family FastMCP adapters only. Keep `mcp` nested
  under `surfaces/`; a top-level `mcp/` package shadows the installed MCP SDK.
- Connection Hub delegated credentials docs:
  `docs/sdk/solutions/connections/delegated-credentials/`.

## Rules

- Keep `entrypoint.py` thin. It declares bundle identity, defaults, and surface
  decorators only.
- Put product logic in `services/<service>/`.
- Put MCP tool schema/registration in `surfaces/mcp/<service>.py`; call service
  modules from there.
- Do not implement auth in tools. Managed MCP auth is enforced by the proc MCP
  bridge from `surfaces.as_provider.mcp.<alias>.auth`.
- `auth_config` on `@mcp` is only a pointer to descriptor-owned config. Real
  grants/tools/authority values belong in `bundles.yaml` or
  `configuration_defaults()`.
- Add one MCP alias per service family. Do not overload a generic `kdcube`
  endpoint with unrelated tools.
- Keep docs and journal updated with every new service surface.

## Validate

```bash
python -m py_compile entrypoint.py surfaces/mcp/conversations.py services/conversations/__init__.py
```

Export domain logic lives in the conversation SDK
(`sdk.solutions.conversation.export` and `sdk.solutions.conversation.mcp_export`);
this bundle only publishes the MCP tool schema and supplies pooled resources
from the runtime. To compile-check the SDK owner from the source root:

```bash
python -m py_compile kdcube_ai_app/apps/chat/sdk/solutions/conversation/export.py kdcube_ai_app/apps/chat/sdk/solutions/conversation/mcp_export.py
```

Then reload the bundle and test the real public MCP URL through Connection Hub
delegated OAuth consent.
