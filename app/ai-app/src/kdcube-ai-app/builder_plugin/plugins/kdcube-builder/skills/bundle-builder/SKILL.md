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

Read **Tier 1 only** by default. Pull Tier 2 on demand when Tier 1 does not answer the
specific thing you are about to do.

1. **Tier 1 — always read, every bundle task:**
   - `how-to-write-bundle-README.md` — authoring
   - `how-to-configure-and-run-bundle-README.md` — **REQUIRED any time the bundle
     lives outside the current `host_bundles_path`, or any time you touch `bundles.yaml`
     or `assembly.yaml`.** Only source of truth for the host-path / container-path /
     mount-root split.
   - `how-to-test-bundle-README.md` — testing
   - versatile reference bundle — read end-to-end (structure + `entrypoint.py`)
2. **Tier 2 — only when Tier 1 is not enough.** See the Tier 2 section below for the
   trigger list. Do not preload Tier 2 "just in case" — it is large and mostly irrelevant
   to any single task.
3. Only then start writing or editing code.

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

### Tier 1 — always read (operational canon)

- `app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md` — authoring
- `app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md` — configuration + runtime (`assembly.yaml`, `bundles.yaml`, `bundles.secrets.yaml`, props/secrets, reload)
- `app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md` — testing
- Reference bundle (read end-to-end): `app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/`

### Tier 2 — read only on demand (when Tier 1 is not enough)

Pull these when the task specifically hits the topic. Do not preload.

**SDK reference deep-dives** (`app/ai-app/docs/sdk/bundle/`) — read the matching file when
you need more than Tier 1 gave you on a specific feature:

- `bundle-index-README.md` — SDK map
- `bundle-reference-versatile-README.md` — annotated walkthrough of versatile
- `bundle-dev-README.md` — dev loop / layout
- `bundle-runtime-README.md` — runtime internals
- `bundle-platform-integration-README.md` — platform hooks
- `bundle-props-secrets-README.md` — props / secrets model (read when editing either)
- `bundle-knowledge-space-README.md` — **read for KS / `ks:` namespace resolvers**
- `bundle-node-backend-bridge-README.md` — **read for Node/TS backend**
- `bundle-widget-integration-README.md` — widget deep-dive
- `bundle-client-ui-README.md` / `bundle-client-communication-README.md` — client UI + transport
- `bundle-venv-README.md` — `@venv` internals
- `bundle-scheduled-jobs-README.md` — `@cron` internals
- `bundle-storage-cache-README.md` — storage + cache
- `bundle-sse-events-README.md`, `bundle-transports-README.md`, `bundle-frontend-awareness-README.md`,
  `bundle-interfaces-README.md`, `bundle-lifecycle-README.md`, `bundle-ops-README.md`,
  `bundle-firewall-README.md`, `bundle-platform-properties-README.md` — specialized; read by name when the topic matches.

**Descriptor / service configuration** (`app/ai-app/docs/service/configuration/`) — read the
matching file **only when editing that specific descriptor**:

- `service-config-README.md` — overview
- `assembly-descriptor-README.md` — when editing `assembly.yaml`
- `bundles-descriptor-README.md` — when editing `bundles.yaml`
- `bundles-secrets-descriptor-README.md` — when editing `bundles.secrets.yaml`
- `gateway-descriptor-README.md` — when editing `gateway.yaml`
- `secrets-descriptor-README.md` — when editing `secrets.yaml`

**Specialized example bundles** (`app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/`) — read the one that matches the specialized case:

- `kdcube.copilot@2026-04-03-19-05` — knowledge-space / extended resolver
- `with-isoruntime@2026-02-16-14-00` — isolated exec
- `resources/node-backend-bridge` — Node/TS bridge

**Suite tests** (read when writing or debugging bundle tests):

- `app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_bundle_state.py`
- `app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_run_bundle_suite.py`

### Local fast path (opt-in)

If `CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT` is set, read the same files from
`$CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT/<path-above>` with the `Read` tool instead of
`WebFetch`. This is purely an optimization — do not ask the user for a local path if the env
var is not already set, just use GitHub.

## Primary example

Default to `versatile` (Tier 1). Pull specialized examples from Tier 2 only when the task
is specifically about `ks:` / custom namespace resolvers, isolated exec, or the Node/TS bridge.

**Versatile is NOT a reference for `@cron` or `@venv`** — it does not use them. If the task
needs either decorator, read `bundle-scheduled-jobs-README.md` (for `@cron`) or
`bundle-venv-README.md` (for `@venv`) from Tier 2 before writing code. The copyable
snippets in `how-to-write-bundle-README.md` §4.1 are the minimum correct starting point.

## Register the bundle in `bundles.yaml`

Recommended form — `path` = bundle root, `module: entrypoint`:

```yaml
bundles:
  items:
    - id: "<bundle-id>"
      name: "<Human Name>"
      path: "/bundles/<relative-path-from-host_bundles_path>"
      module: "entrypoint"
```

`path` is the **container path** — `/bundles/` + the bundle's path relative to
`assembly.yaml -> paths.host_bundles_path`. It is **not** `/bundles/<bundle-id>` unless
the bundle directory happens to sit directly under `host_bundles_path` with that name.
Host path in `bundles.yaml` is the #1 source of silent reload failures — see
"Host path and runtime path are not the same thing" in the how-to.

Alternative form (less readable, use only when needed): `path` points at the parent,
`module` carries the bundle subdir — `module: "<bundle_dir>.entrypoint"`.

## Workflows

### Write a bundle from scratch

1. Resolve `$WORKDIR` and `$BUNDLES_YAML` (ask the user if the workdir is not found).
2. Read Tier 1 (3 how-to docs + versatile reference bundle end-to-end).
3. If the task hits a specialized feature (`@cron`, `@venv`, KS, Node bridge, isolated
   exec, specific descriptor edit), pull the matching Tier 2 doc.
4. Pick a host directory for the bundle (default `~/.kdcube/bundles/<bundle-id>/`,
   or wherever the user asked). Create it and write `entrypoint.py` + `__init__.py`.
5. Register the bundle in `$BUNDLES_YAML` using the correct **container path**
   (`/bundles/<relative-path-from-host_bundles_path>`, not the host path, not
   `/bundles/<bundle-id>` unless that matches the actual layout).
6. Run bundle tests (`bundle-tests <host-path>`), then reload + verify-reload.

### Wrap an existing application into a bundle

1. Resolve `$WORKDIR` and `$BUNDLES_YAML`.
2. Read the existing app's code to understand entry points, APIs, and data.
3. Read Tier 1 (3 how-to docs + versatile). Pull Tier 2 on demand.
4. Map the app's functionality to bundle primitives (`@api`, `@ui_main`, `@cron`, etc.).
5. Pick a host directory for the bundle (default `~/.kdcube/bundles/<bundle-id>/`,
   or wherever the user asked). Copy the app source into it (or under a subdir)
   and call it from `entrypoint.py`. Do not modify the original app tree.
6. Register in `$BUNDLES_YAML` with the correct container path, run bundle tests,
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