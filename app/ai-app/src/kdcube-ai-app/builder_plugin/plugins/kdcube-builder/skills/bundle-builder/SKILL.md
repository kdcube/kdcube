---
description: Build or repair KDCube bundles. Use the KDCube bundle docs, the versatile reference bundle, and the shared bundle suite before writing code.
---

# KDCube Bundle Builder

Use this skill when the task is bundle authoring: writing a bundle from scratch, wrapping an
existing application into a bundle, or adding features to an existing bundle.

## Authoring rule #1 — lean on the docs

**Never write bundle code from memory.** Always read the docs and a real reference bundle
first. Decorators, import paths, descriptor fields, and runtime bindings change between
releases — guessing them produces bundles that load but silently misbehave.

For every bundle task, the first actions are:

1. Read the docs in the order below.
2. Read the versatile reference bundle (and another example if the task is a specialized case).
3. Only then start writing or editing code.

If a doc contradicts this skill, the doc wins — surface the conflict to the user.

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

Check whether `CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT` is set. If it is, use **local paths**
(faster, no network). Otherwise fall back to the GitHub URLs below.

### Local paths (when repo root is available)

Let `R = $CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT`. Read in this order with the `Read` tool:

1. `R/app/ai-app/docs/sdk/bundle/bundle-index-README.md`
2. `R/app/ai-app/docs/sdk/bundle/bundle-reference-versatile-README.md`
3. `R/app/ai-app/docs/sdk/bundle/bundle-dev-README.md`
4. `R/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md`
5. `R/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md`
6. `R/app/ai-app/docs/sdk/bundle/bundle-props-secrets-README.md`
7. `R/app/ai-app/docs/sdk/bundle/bundle-node-backend-bridge-README.md`

Reference bundle (read all files with `Read` / `Glob`):

- `R/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

Bundle suite tests (read to understand what the suite validates):

- `R/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_bundle_state.py`
- `R/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_run_bundle_suite.py`

### GitHub fallback (when no local repo)

1. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-index-README.md`
2. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-reference-versatile-README.md`
3. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-dev-README.md`
4. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md`
5. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md`
6. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-props-secrets-README.md`
7. `https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-node-backend-bridge-README.md`

## Primary examples

Default to `versatile` unless the task is specifically about `ks:` / custom namespace resolvers,
isolated exec, or the Node/TS bridge.

- versatile reference bundle — `versatile@2026-03-31-13-36` (see local path above, or GitHub tree)
- knowledge-space and extended resolver — `kdcube.copilot@2026-04-03-19-05`
- isolated exec — `with-isoruntime@2026-02-16-14-00`
- Node/TS bridge — `resources/node-backend-bridge`

## Register the bundle in `bundles.yaml`

```yaml
bundles:
  version: "1"
  default_bundle_id: "<bundle-id>"
  items:
    - id: "<bundle-id>"
      name: "<Human Name>"
      path: "/bundles/<bundle-id>"
      module: "entrypoint"
      config:
        role_models:
          gate.simple:
            provider: "anthropic"
            model: "claude-haiku-4-5-20251001"
```

The `path` MUST be the **container path** (`/bundles/<bundle-id>`), not the host
path. Mismatch here is the #1 source of silent reload failures.

**macOS gotcha:** Docker Desktop on macOS does not refresh a file-level bind mount
when the host file's inode changes — and the Edit/Write tools replace inodes.
After editing `$WORKDIR/config/bundles.yaml`, restart `chat-proc` before reloading
so the container sees the new file:

```bash
docker restart all_in_one_kdcube-chat-proc-1
```

Changes to files inside the bundle directory itself do **not** need this — the
bundle is mounted as a directory bind, not a file bind.

## Workflows

### Write a bundle from scratch

1. Resolve `$WORKDIR` and `$BUNDLES_YAML` (ask the user if the workdir is not found).
2. Read the docs (all seven, in order).
3. Read the versatile reference bundle end-to-end.
4. Pick a host directory for the bundle (default `~/.kdcube/bundles/<bundle-id>/`,
   or wherever the user asked). Create it and write `entrypoint.py` + `__init__.py`.
5. Register the bundle in `$BUNDLES_YAML` using the **container path**
   (`/bundles/<bundle-id>`).
6. Run bundle tests (`bundle-tests <host-path>`), then reload + verify-reload.

### Wrap an existing application into a bundle

1. Resolve `$WORKDIR` and `$BUNDLES_YAML`.
2. Read the existing app's code to understand entry points, APIs, and data.
3. Read the docs and versatile reference bundle.
4. Map the app's functionality to bundle primitives (`@api`, `@ui_main`, `@cron`, etc.).
5. Pick a host directory for the bundle (default `~/.kdcube/bundles/<bundle-id>/`,
   or wherever the user asked). Copy the app source into it (or under a subdir)
   and call it from `entrypoint.py`. Do not modify the original app tree.
6. Register in `$BUNDLES_YAML` with the container path, run bundle tests,
   then reload + verify-reload.

### Add a feature to an existing bundle

1. Read the existing `entrypoint.py` and the relevant docs section.
2. Make the minimal change that adds the feature.
3. Run bundle tests, then reload + verify-reload.

## Authoring rules

- Read the docs and examples before writing code — every time, even for small changes.
- Do not invent decorators, import paths, or bundle tree layout.
- For third-party Python packages, first check whether the runtime already has them.
- Use `@venv(...)` for dependency-heavy leaf helpers, not for request-bound orchestration.
- Keep communicator, request context, Redis, DB clients, and other live proc/runtime
  bindings outside `@venv(...)`.
- If a Node backend is needed, keep Python as the bundle boundary and put Node/TS behind a
  narrow bridge.
- If local runtime setup is needed, use `/kdcube-builder:bootstrap-local` first.

## Validation + reload

Run the shared bundle suite before considering bundle work done:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" bundle-tests /abs/path/to/bundle
```

Then reload if the runtime is running — **always pair `reload` with `verify-reload`**:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" reload <bundle-id>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" verify-reload <bundle-id>
```

### Reload rules (read before touching a running runtime)

- Editing files in `HOST_BUNDLES_PATH/<bundle-id>/` does **not** hot-reload. The runtime
  serves the cached bundle until an explicit `reload <bundle-id>`. Old code keeps running
  until you reload — that is the usual cause of "my change didn't take effect".
- `reload` only works if `<bundle-id>` is registered in `bundles.yaml` with the correct
  container path (`/bundles/<bundle-id>`). A typo or host-path in `bundles.yaml` makes the
  reload succeed-looking but no-op.
- **Always run `verify-reload` after `reload`.** The reload call returns before the proc
  cache actually rotates; without verify you do not know whether the new code is live.
- `verify-reload` reporting `eviction: None` for a bundle that was supposed to be active is
  a red flag — the bundle was never in the proc cache, which usually means the id/path in
  `bundles.yaml` is wrong, or the bundle was never loaded in the first place.
- Any container restart (secrets injection, `kdcube --stop`/`start`, Docker restart) drops
  the proc cache. Reload every active bundle immediately after such events.