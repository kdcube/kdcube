# /kdcube-bundle-builder

Build or repair a KDCube bundle based on the task the user typed after
`/kdcube-bundle-builder`. Use this for writing a bundle from scratch, wrapping an existing
application into a bundle, or adding features to an existing bundle.

## Authoring rule #1 — lean on the docs (HARD GATE — NO EXCEPTIONS)

**Never write bundle code, edit a descriptor, or touch runtime config from memory.**
Decorators, import paths, descriptor fields, runtime paths, and mount semantics change
between releases — guessing them produces bundles that "load" but silently misbehave.

**This rule is absolute.** It applies every single time, including:

- "small" edits to an existing bundle
- renames, path changes, adding one decorator
- "I already read it last session" — no, re-read it; state changes between sessions
- the user says "just do it quickly" — still read the docs first, then do it quickly
- the bundle lives outside the runtime workdir / outside `host_bundles_path` — **especially then**

### Mandatory pre-flight (do these in order, every bundle task)

Read **Tier 1 only** by default. Pull Tier 2 on demand.

1. **Tier 1 — always read, every bundle task:**
   - `how-to-write-bundle-README.md` — authoring
   - `how-to-configure-and-run-bundle-README.md` — **REQUIRED any time the bundle lives
     outside the current `host_bundles_path`, or any time you touch `bundles.yaml` or
     `assembly.yaml`.**
   - `how-to-test-bundle-README.md` — testing
   - versatile reference bundle — read end-to-end (structure + `entrypoint.py`)
2. **Tier 2 — only when Tier 1 is not enough.** See the list below.
3. Only then start writing or editing code.

If a doc contradicts this prompt, the doc wins — surface the conflict to the user.

### Bundle lives outside the runtime mount — read this section twice

When the user's bundle directory is NOT under the current `host_bundles_path` from
`assembly.yaml`, the runtime cannot see it. The fix: edit `assembly.yaml ->
paths.host_bundles_path` to the parent that contains the bundle, then rebuild with
`kdcube --workdir $WORKDIR --build --upstream` so the new mount takes effect. After that,
in `bundles.yaml` use the **container path** =
`/bundles/<relative-path-from-host_bundles_path>`.

The `bootstrap <bundle-id> <bundle-dir> --host-bundles-path <parent>` helper does the
same thing (it writes `host_bundles_path` into `assembly.yaml`).

Do not put the host path directly into `bundles.yaml` — the runtime path and host path
are different namespaces.

## What one bundle can contain

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

**The plugin ships without docs — they are NOT on disk.** Always fetch from the web. Do
not try to `Read` these paths locally, do not try to `ls` a docs directory, do not ask
the user to point you at one. The only exception is the opt-in local fast path at the
bottom of this section, which requires `KDCUBE_REPO_ROOT` to already be set.

Base URL (for reference): `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/`.

### Tier 1 — always read (operational canon)

- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md`
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md`
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md`

Reference bundle `versatile@2026-03-31-13-36` — directories aren't web-fetchable; fetch
these individually:

- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py`
- To discover the rest of the tree, fetch
  `https://api.github.com/repos/kdcube/kdcube-ai-app/contents/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`
  then fetch individual files by name (e.g. `skills_descriptor.py`, `tools_descriptor.py`,
  anything under `agents/`, `skills/`, `tools/`).

### Tier 2 — read only on demand

**Header-first gate:** Before reading any Tier 2 doc in full, fetch it and read only the
title and first section (≈first 30 lines, up to the first `##` heading). Then ask yourself:
does this doc specifically address what I am implementing right now? If yes — read the rest.
If no — stop; you have confirmed it is not needed for this task.

All under `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/<filename>`:

- `bundle-index-README.md`, `bundle-reference-versatile-README.md`, `bundle-dev-README.md`,
  `bundle-runtime-README.md`, `bundle-platform-integration-README.md`,
  `bundle-props-secrets-README.md`, `bundle-knowledge-space-README.md` (KS / `ks:`
  resolvers), `bundle-node-backend-bridge-README.md` (Node/TS), `bundle-widget-integration-README.md`,
  `bundle-client-ui-README.md`, `bundle-client-communication-README.md`,
  `bundle-venv-README.md` (`@venv`), `bundle-scheduled-jobs-README.md` (`@cron`),
  `bundle-storage-cache-README.md`, `bundle-sse-events-README.md`,
  `bundle-transports-README.md`, `bundle-frontend-awareness-README.md`,
  `bundle-interfaces-README.md`, `bundle-lifecycle-README.md`, `bundle-ops-README.md`,
  `bundle-firewall-README.md`, `bundle-platform-properties-README.md`.

**Descriptor / service configuration** — read the matching file **only when editing that
specific descriptor**. Apply the same header-first gate: fetch, read the title and first
section, confirm it covers your specific field, then read in full. Base:
`https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/service/configuration/<filename>`:

- `service-config-README.md` (overview), `assembly-descriptor-README.md`,
  `bundles-descriptor-README.md`, `bundles-secrets-descriptor-README.md`,
  `gateway-descriptor-README.md`, `secrets-descriptor-README.md`.

**Specialized example bundles** — use the GitHub contents API at
`https://api.github.com/repos/kdcube/kdcube-ai-app/contents/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/<dir>`:

- `kdcube.copilot@2026-04-03-19-05` — knowledge-space / extended resolver
- `with-isoruntime@2026-02-16-14-00` — isolated exec
- `resources/node-backend-bridge` — Node/TS bridge

**Suite tests** (read when writing or debugging bundle tests):

- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_bundle_state.py`
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_run_bundle_suite.py`

### Local fast path (opt-in — do not ask for it)

If — **and only if** — `KDCUBE_REPO_ROOT` is already set, read the same paths from
`$KDCUBE_REPO_ROOT/<repo-relative-path>` locally. Derive the repo-relative path by
stripping the `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/` prefix. If
the env var is not set, do not suggest setting it — just fetch.

## Primary example

Default to `versatile` (Tier 1). Pull specialized examples from Tier 2 only when the task
is specifically about `ks:` / custom namespace resolvers, isolated exec, or the Node/TS
bridge.

**Versatile is NOT a reference for `@cron` or `@venv`** — it does not use them. If the
task needs either decorator, read `bundle-scheduled-jobs-README.md` (for `@cron`) or
`bundle-venv-README.md` (for `@venv`) from Tier 2 before writing code.

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

## Workflows

### Write a bundle from scratch

1. Resolve `$WORKDIR` and `$BUNDLES_YAML` (ask if the workdir is not found).
2. Read Tier 1 (3 how-to docs + versatile reference bundle end-to-end).
3. If the task hits a specialized feature (`@cron`, `@venv`, KS, Node bridge, isolated
   exec, specific descriptor edit), pull the matching Tier 2 doc.
4. Pick a host directory (default `~/.kdcube/bundles/<bundle-id>/`). Create it and write
   `entrypoint.py` + `__init__.py`.
5. Register in `$BUNDLES_YAML` using the correct container path.
6. Run bundle tests, then reload + verify-reload.

### Wrap an existing application into a bundle

1. Resolve `$WORKDIR` and `$BUNDLES_YAML`.
2. Read the existing app's code to understand entry points, APIs, and data.
3. Read Tier 1.
4. Map the app's functionality to bundle primitives (`@api`, `@ui_main`, `@cron`, etc.).
5. Pick a host directory. Copy the app source in (or under a subdir) and call it from
   `entrypoint.py`. Do not modify the original app tree.
6. Register, run tests, reload + verify-reload.

### Add a feature to an existing bundle

1. Read the existing `entrypoint.py` and the relevant docs section.
2. Make the minimal change.
3. Run bundle tests, then reload + verify-reload.

## Validation + reload

```bash
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" bundle-tests /abs/path/to/bundle
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" reload <bundle-id>
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" verify-reload <bundle-id>
```

`bundle-tests` needs `KDCUBE_REPO_ROOT` to point at a local `kdcube-ai-app` clone — if
it's unset, ask the user whether they have one.

### Reload rules (read before touching a running runtime)

- Editing files in `HOST_BUNDLES_PATH/<bundle-id>/` does **not** hot-reload. The runtime
  serves the cached bundle until an explicit `reload <bundle-id>`.
- `reload` only works if `<bundle-id>` is registered in `bundles.yaml` with the correct
  container path.
- **Always run `verify-reload` after `reload`.** The reload call returns before the proc
  cache actually rotates.
- `verify-reload` reporting `eviction: None` for a bundle that was supposed to be active
  is a red flag — the bundle was never in the proc cache.
- Any container restart drops the proc cache. Reload every active bundle immediately
  after such events.