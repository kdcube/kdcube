---
description: Build or repair KDCube bundles. Use the KDCube bundle docs, the versatile reference bundle, and the shared bundle suite before writing code.
---

# KDCube Bundle Builder

Use this skill when the task is bundle authoring.

## What one bundle can contain

One KDCube bundle can combine:

- Python backend entrypoint
- authenticated APIs via `@api(route="operations")`
- public APIs via `@api(route="public", public_auth=...)`
- widgets via `@ui_widget(...)`
- a full custom iframe UI via `@ui_main`
- storage
- deploy-scoped props and secrets
- user-scoped props and secrets
- scheduled jobs via `@cron(...)`
- dependency-isolated helpers via `@venv(...)`
- React v2 and/or Claude Code and/or custom agents
- optional Node or TypeScript backend logic behind a Python bridge

## Read order

Prefer a local `kdcube-ai-app` checkout if you have one. Otherwise use these public docs first:

1. `https://github.com/kdcube/kdcube-ai-app/blob/main/README.md`
2. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-index-README.md`
3. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-reference-versatile-README.md`
4. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-dev-README.md`
5. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md`
6. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md`
7. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-props-secrets-README.md`
8. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-node-backend-bridge-README.md`

## Primary examples

Use these examples first:

- versatile reference bundle:
  `https://github.com/kdcube/kdcube-ai-app/tree/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`
- knowledge-space and extended resolver example:
  `https://github.com/kdcube/kdcube-ai-app/tree/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05`
- isolated exec example:
  `https://github.com/kdcube/kdcube-ai-app/tree/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00`
- public Node/TS bridge example:
  `https://github.com/kdcube/kdcube-ai-app/tree/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/resources/node-backend-bridge`

Default to `versatile` unless the task is specifically about:

- `ks:` / custom namespace resolvers
- isolated exec
- the Node/TS bridge

## Authoring rules

- Read the docs and examples before writing code.
- Do not invent decorators, import paths, or bundle tree layout.
- For third-party Python packages, first check whether the runtime already has them.
- Use `@venv(...)` for dependency-heavy leaf helpers, not for request-bound orchestration.
- Keep communicator, request context, Redis, DB clients, and other live proc/runtime bindings outside `@venv(...)`.
- If a Node backend is needed, keep Python as the bundle boundary and put Node/TS behind a narrow bridge.
- If local runtime setup is needed, use `/kdcube-builder:bootstrap-local` first.

## Validation

Run the shared bundle suite before considering bundle work done:

```text
/kdcube-builder:local-runtime bundle-tests /abs/path/to/bundle
```
