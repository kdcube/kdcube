---
description: Build or repair KDCube bundles. Use the KDCube bundle docs, the versatile reference bundle, and the shared bundle suite before writing code.
---

# KDCube Bundle Builder

Use this skill when the task is bundle authoring: writing a bundle from scratch, wrapping an
existing application into a bundle, or adding features to an existing bundle.

## Authoring rule #1 — lean on the docs (HARD GATE — NO EXCEPTIONS)

**Never write bundle code, edit a descriptor, or touch runtime config from memory.**
Decorators, import paths, descriptor fields, runtime paths, and mount semantics change
between releases — guessing them produces bundles that "load" but silently misbehave, or
worse, `bundles.yaml` entries that look right but never actually resolve inside the
container. You will not catch these by reading the code — the runtime is permissive and
the symptoms are delayed.

**This rule is absolute.** It applies every single time, including:

- "small" edits to an existing bundle
- renames, path changes, adding one decorator
- "I already read it last session" — no, re-read it; state changes between sessions
- the user says "just do it quickly" — still read the docs first, then do it quickly
- the bundle lives outside the runtime workdir / outside `host_bundles_path` — **especially then**

Do NOT skip the read step because the task "looks simple." The most common failure mode
of this plugin is exactly that: the agent skips the docs, writes a plausible-looking
`bundles.yaml` entry with the host path instead of the container path, the reload
appears to succeed, and nothing works. Reading the docs is cheaper than debugging that.

### Mandatory pre-flight (do these in order, every bundle task)

1. **Read the how-to playbooks first — operational canon:**
   - `how-to-write-bundle-README.md` — authoring
   - `how-to-configure-and-run-bundle-README.md` — **REQUIRED reading any time the bundle
     lives outside the current `host_bundles_path`, or any time you touch `bundles.yaml`
     or `assembly.yaml`.** This doc is the only source of truth for the host-path /
     container-path / mount-root split. Do not attempt to configure a bundle that sits
     outside the runtime tree without reading it — you will get the path wrong.
   - `how-to-test-bundle-README.md` — testing
2. Read the versatile reference bundle (and another example if the task is a specialized case).
3. When editing `assembly.yaml` / `bundles.yaml` / `bundles.secrets.yaml` / `gateway.yaml` /
   `secrets.yaml`, also read the matching descriptor doc under `docs/service/configuration/`
   before making the edit. Not after. Before.
4. Only then start writing or editing code.

If a doc contradicts this skill, the doc wins — surface the conflict to the user.

### Bundle lives outside the runtime mount — read this section of the how-to twice

When the user's bundle directory is NOT under the current `host_bundles_path` from
`assembly.yaml`, the runtime cannot see it. The fix documented in
`how-to-configure-and-run-bundle-README.md` (section "If you want to change the host
bundles root") is: edit `assembly.yaml -> paths.host_bundles_path` to the parent that
contains the bundle, then rebuild with `kdcube --workdir $WORKDIR --build --upstream`
so the new mount takes effect. After that, in `bundles.yaml` use the **container path**
= `/bundles/<relative-path-from-host_bundles_path>`.

The plugin's `bootstrap <bundle-id> <bundle-dir> --host-bundles-path <parent>` helper
does the same thing (it writes `host_bundles_path` into `assembly.yaml`), so you can
use it as a shortcut when you also want a fresh descriptor set — but it is the same
underlying action, not an alternative fix.

Do not put the host path directly into `bundles.yaml` — the runtime path and host path
are different namespaces. Read the "Host path and runtime path are not the same thing"
and "If you want to change the host bundles root" sections of the how-to before
editing anything.

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

**Default source is GitHub.** The plugin has no reliable way to locate the repo on disk, so
fetch the docs from GitHub with `WebFetch`. Only use local paths if
`CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT` is explicitly set — that is an opt-in fast path, not
the default.

All paths below are relative to `https://github.com/kdcube/kdcube-ai-app/blob/main/`.

### 1. How-to playbooks (read these first — operational canon)

- `app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md` — authoring
- `app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md` — configuration + runtime (`assembly.yaml`, `bundles.yaml`, `bundles.secrets.yaml`, props/secrets, reload)
- `app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md` — testing

### 2. SDK reference docs

- `app/ai-app/docs/sdk/bundle/bundle-index-README.md`
- `app/ai-app/docs/sdk/bundle/bundle-reference-versatile-README.md`
- `app/ai-app/docs/sdk/bundle/bundle-dev-README.md`
- `app/ai-app/docs/sdk/bundle/bundle-runtime-README.md`
- `app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md`
- `app/ai-app/docs/sdk/bundle/bundle-props-secrets-README.md`
- `app/ai-app/docs/sdk/bundle/bundle-node-backend-bridge-README.md`

### 3. Descriptor / service configuration

Read the matching descriptor doc when editing any of `assembly.yaml`, `bundles.yaml`,
`bundles.secrets.yaml`, `gateway.yaml`, `secrets.yaml`:

- `app/ai-app/docs/service/configuration/service-config-README.md`
- `app/ai-app/docs/service/configuration/assembly-descriptor-README.md`
- `app/ai-app/docs/service/configuration/bundles-descriptor-README.md`
- `app/ai-app/docs/service/configuration/bundles-secrets-descriptor-README.md`
- `app/ai-app/docs/service/configuration/gateway-descriptor-README.md`
- `app/ai-app/docs/service/configuration/secrets-descriptor-README.md`

### 4. Reference bundle and tests

- Bundle: `app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/`
- Suite tests (understand what validates a bundle):
  - `app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_bundle_state.py`
  - `app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_run_bundle_suite.py`

### Local fast path (opt-in)

If `CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT` is set, read the same files from
`$CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT/<path-above>` with the `Read` tool instead of
`WebFetch`. This is purely an optimization — do not ask the user for a local path if the env
var is not already set, just use GitHub.

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