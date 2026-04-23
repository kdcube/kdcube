---
id: ks:docs/sdk/bundle/bundle-index-README.md
title: "Bundle Index"
summary: "Navigation index for bundle documentation: pick the right authoring, runtime, integration, configuration, reference, delivery, and storage docs without reading the whole SDK tree."
tags: ["sdk", "bundle", "docs", "index", "reference"]
keywords: ["bundle docs navigation", "authoring doc index", "runtime doc index", "integration doc index", "configuration doc index", "reference bundle entrypoint", "delivery and storage docs"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/bundle-delivery-and-update-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-transports-README.md
---
# Bundle Docs Index

This is the bundle-developer start page.

The bundle docs now use one reference bundle only:

- `versatile@2026-03-31-13-36`

Older `docs/sdk/example-bundle` sample docs are no longer part of the bundle path.

## Read in this order

1. [build/how-to-navigate-kdcube-docs-README.md](build/how-to-navigate-kdcube-docs-README.md)
   - shortest path for bundle creators, wrappers, integrators, and readers
2. [bundle-developer-guide-README.md](bundle-developer-guide-README.md)
   - minimal authoring contract
   - local bundle layout
   - config/secrets/update loop
3. [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md)
   - exact local runtime contract
   - `assembly.yaml`, `bundles.yaml`, `bundles.secrets.yaml`
   - `kdcube --build --upstream` and `kdcube --info`
   - sharp `path` / `module` rules
4. [build/how-to-write-bundle-README.md](build/how-to-write-bundle-README.md)
   - builder playbook for designing and structuring a bundle
5. [build/how-to-test-bundle-README.md](build/how-to-test-bundle-README.md)
   - builder playbook for validation and runtime checks
6. [versatile-reference-bundle-README.md](versatile-reference-bundle-README.md)
   - the concrete reference bundle to study
7. [bundle-runtime-README.md](bundle-runtime-README.md)
   - runtime surfaces available to bundle code
8. [bundle-platform-integration-README.md](bundle-platform-integration-README.md)
   - exact decorator and route contract
9. [bundle-transports-README.md](bundle-transports-README.md)
   - canonical inbound/outbound protocol and transport map
10. [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)
   - platform/global, bundle-scoped, and user-scoped configuration and secrets
11. [bundle-delivery-and-update-README.md](bundle-delivery-and-update-README.md)
   - local reload, registry updates, delivery modes, and deployment-side changes

## Core Doc Map

| Concern | Primary doc |
| --- | --- |
| Fastest Tier 1 reading order | [build/how-to-navigate-kdcube-docs-README.md](build/how-to-navigate-kdcube-docs-README.md) |
| Build a new bundle | [bundle-developer-guide-README.md](bundle-developer-guide-README.md) |
| Configure local runtime, descriptors, and CLI loop | [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md) |
| Builder playbook | [build/how-to-write-bundle-README.md](build/how-to-write-bundle-README.md) |
| Testing playbook | [build/how-to-test-bundle-README.md](build/how-to-test-bundle-README.md) |
| Study the reference bundle | [versatile-reference-bundle-README.md](versatile-reference-bundle-README.md) |
| Runtime surfaces | [bundle-runtime-README.md](bundle-runtime-README.md) |
| Decorators, widget/API/public integration | [bundle-platform-integration-README.md](bundle-platform-integration-README.md) |
| Inbound/outbound transports and protocols | [bundle-transports-README.md](bundle-transports-README.md) |
| Props, secrets, raw descriptor reads | [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md) |
| Reserved platform-owned prop paths | [bundle-reserved-platform-properties-README.md](bundle-reserved-platform-properties-README.md) |
| Bundle lifecycle and instance model | [bundle-lifecycle-README.md](bundle-lifecycle-README.md) |
| Widgets, streaming, operations surface | [bundle-interfaces-README.md](bundle-interfaces-README.md) |
| Bundle-facing browser/UI entry | [bundle-client-ui-README.md](bundle-client-ui-README.md) |
| Browser/UI transport contract | [bundle-client-communication-README.md](bundle-client-communication-README.md) |
| SSE event catalog for bundle-facing clients | [bundle-sse-events-README.md](bundle-sse-events-README.md) |
| Frontend behavior under drains/rate limits | [bundle-frontend-awareness-README.md](bundle-frontend-awareness-README.md) |
| Local reload and deployed registry updates | [bundle-delivery-and-update-README.md](bundle-delivery-and-update-README.md) |
| Knowledge space (`ks:`) | [bundle-knowledge-space-README.md](bundle-knowledge-space-README.md) |
| Bundle storage and cache | [bundle-storage-and-cache-README.md](bundle-storage-and-cache-README.md) |
| Scheduled jobs (`@cron`) | [bundle-scheduled-jobs-README.md](bundle-scheduled-jobs-README.md) |
| Cached subprocess virtualenv helpers (`@venv`) | [bundle-venv-README.md](bundle-venv-README.md) |
| Outbound event filtering | [bundle-firewall-README.md](bundle-firewall-README.md) |
| Python-to-Node backend bridge | [bundle-node-backend-bridge-README.md](bundle-node-backend-bridge-README.md) |

## Reference Bundle

Reference bundle root:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

Start with:

- the reference doc:
  [versatile-reference-bundle-README.md](versatile-reference-bundle-README.md)
- then the actual bundle README:
  `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`

## Validation

Shared SDK bundle tests:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`

Default combined runner:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle
```
