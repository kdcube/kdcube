---
id: ks:docs/sdk/bundle/bundle-index-README.md
title: "Bundle Index"
summary: "Start page for bundle developers: authoring path, reference bundle, configuration, and local reload workflow."
tags: ["sdk", "bundle", "docs", "index", "reference"]
keywords: ["bundle docs index", "bundle developer", "versatile bundle", "bundle reload", "bundles.yaml"]
see_also:
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/bundle-reference-versatile-README.md
  - ks:docs/sdk/bundle/bundle-props-secrets-README.md
  - ks:docs/sdk/bundle/bundle-ops-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-transports-README.md
---
# Bundle Docs Index

This is the bundle-developer start page.

The bundle docs now use one reference bundle only:

- `versatile@2026-03-31-13-36`

Older `docs/sdk/example-bundle` sample docs are no longer part of the bundle path.

## Read in this order

1. [bundle-dev-README.md](bundle-dev-README.md)
   - minimal authoring contract
   - local bundle layout
   - config/secrets/update loop
2. [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md)
   - exact local runtime contract
   - `assembly.yaml`, `bundles.yaml`, `bundles.secrets.yaml`
   - `kdcube --build --upstream` and `kdcube --info`
   - sharp `path` / `module` rules
3. [build/how-to-write-bundle-README.md](build/how-to-write-bundle-README.md)
   - builder playbook for designing and structuring a bundle
4. [build/how-to-test-bundle-README.md](build/how-to-test-bundle-README.md)
   - builder playbook for validation and runtime checks
5. [bundle-reference-versatile-README.md](bundle-reference-versatile-README.md)
   - the concrete reference bundle to study
6. [bundle-runtime-README.md](bundle-runtime-README.md)
   - runtime surfaces available to bundle code
7. [bundle-platform-integration-README.md](bundle-platform-integration-README.md)
   - exact decorator and route contract
8. [bundle-transports-README.md](bundle-transports-README.md)
   - canonical inbound/outbound protocol and transport map
9. [bundle-props-secrets-README.md](bundle-props-secrets-README.md)
   - effective props, raw descriptor reads, bundle secrets, user props, user secrets
10. [bundle-ops-README.md](bundle-ops-README.md)
   - local reload, registry updates, delivery modes, and deployment-side changes

## Core Doc Map

| Concern | Primary doc |
| --- | --- |
| Build a new bundle | [bundle-dev-README.md](bundle-dev-README.md) |
| Configure local runtime, descriptors, and CLI loop | [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md) |
| Builder playbook | [build/how-to-write-bundle-README.md](build/how-to-write-bundle-README.md) |
| Testing playbook | [build/how-to-test-bundle-README.md](build/how-to-test-bundle-README.md) |
| Study the reference bundle | [bundle-reference-versatile-README.md](bundle-reference-versatile-README.md) |
| Runtime surfaces | [bundle-runtime-README.md](bundle-runtime-README.md) |
| Decorators, widget/API/public integration | [bundle-platform-integration-README.md](bundle-platform-integration-README.md) |
| Inbound/outbound transports and protocols | [bundle-transports-README.md](bundle-transports-README.md) |
| Props, secrets, raw descriptor reads | [bundle-props-secrets-README.md](bundle-props-secrets-README.md) |
| Reserved platform-owned prop paths | [bundle-platform-properties-README.md](bundle-platform-properties-README.md) |
| Bundle lifecycle and instance model | [bundle-lifecycle-README.md](bundle-lifecycle-README.md) |
| Widgets, streaming, operations surface | [bundle-interfaces-README.md](bundle-interfaces-README.md) |
| Bundle-facing browser/UI entry | [bundle-client-ui-README.md](bundle-client-ui-README.md) |
| Browser/UI transport contract | [bundle-client-communication-README.md](bundle-client-communication-README.md) |
| SSE event catalog for bundle-facing clients | [bundle-sse-events-README.md](bundle-sse-events-README.md) |
| Frontend behavior under drains/rate limits | [bundle-frontend-awareness-README.md](bundle-frontend-awareness-README.md) |
| Local reload and deployed registry updates | [bundle-ops-README.md](bundle-ops-README.md) |
| Knowledge space (`ks:`) | [bundle-knowledge-space-README.md](bundle-knowledge-space-README.md) |
| Bundle storage and cache | [bundle-storage-cache-README.md](bundle-storage-cache-README.md) |
| Scheduled jobs (`@cron`) | [bundle-scheduled-jobs-README.md](bundle-scheduled-jobs-README.md) |
| Cached subprocess virtualenv helpers (`@venv`) | [bundle-venv-README.md](bundle-venv-README.md) |
| Outbound event filtering | [bundle-firewall-README.md](bundle-firewall-README.md) |
| Python-to-Node backend bridge | [bundle-node-backend-bridge-README.md](bundle-node-backend-bridge-README.md) |

## Reference Bundle

Reference bundle root:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

Start with:

- the reference doc:
  [bundle-reference-versatile-README.md](bundle-reference-versatile-README.md)
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
