---
id: kdcube-services@1-0/docs/journal/2026-07-12-interface-and-storage-contract
title: "Interface And Storage Contract"
summary: "Added the machine-readable app interface, complete storage ownership map, signed-file contract, descriptor secret shape, and interface parity test."
status: active
tags: ["kdcube-services", "journal", "interface", "openapi", "storage", "tests"]
---

# Interface And Storage Contract

## Change

`kdcube-services@1-0` already had a human MCP interface note but lacked the
machine-readable app declaration and an ownership-oriented storage document.
The package contract now includes:

- `interface/kdcube-services.openapi.yaml` for widgets, operations, managed MCP,
  signed file routes, Data Bus relay, and named-service provider declarations;
- `docs/storage/README.md` for owned, read-through, provider-owned, temporary,
  secret, generated, and Redis-backed state;
- the actual `conversations.file_download_secret` placeholder in the secret
  descriptor template;
- cross-links from root, interface, design, and builder-agent docs;
- an AST/OpenAPI parity test that compares decorators with declared paths and
  non-HTTP surface metadata.

## Semantics Preserved

- The app reads conversation data but does not own conversation persistence or
  retention.
- Mail and Slack bytes remain provider-owned and are fetched on demand.
- Signed public file routes are session-less, not unauthenticated: a prior
  authorized call mints a token bound to object and requester.
- Integration upload staging is temporary, single-use filesystem content with
  a one-hour sweep backstop.
- Redis is coordination/discovery state, not the authority for domain records,
  provider credentials, or signing secrets.

## Follow-Up Exposed By The Storage Map

Filesystem staging requires the upload route and consuming action to share the
same staging root. Distributed deployments using a non-local `STORAGE_PATH`
need a shared staging backend before cross-host execution is safe.
