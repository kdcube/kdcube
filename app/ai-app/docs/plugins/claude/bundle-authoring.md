# Bundle Authoring

The main value of the plugin: the user can ask Claude to **create a new bundle**,
**wrap an existing application** into one, or **extend an existing bundle** with
new features — all from natural language, without hand-reading the SDK.

`kdcube-dev` routes these requests to `/kdcube-builder:bundle-builder`, which
owns the authoring rules. This page summarizes what that skill enforces.

## Three workflows

### 1. Create a bundle from scratch

Triggered by: *"create a bundle"*, *"build a bundle for X"*, *"создай бандл"*.

Steps the skill walks Claude through:

1. Resolve `HOST_BUNDLES_PATH` from `$WORKDIR/config/.env` and `BUNDLES_YAML`
   from `$WORKDIR/config/bundles.yaml`.
2. Read the bundle docs **in the prescribed order** (see below).
3. Read the `versatile` reference bundle end-to-end.
4. Create `HOST_BUNDLES_PATH/<bundle-id>/` with `entrypoint.py` + `__init__.py`.
5. Register the bundle in `BUNDLES_YAML` using the **container path**
   (`/bundles/<relative-path-from-host_bundles_path>`, not the host path).
6. Run `bundle-tests`, then `reload` + `verify-reload`.

### 2. Wrap an existing app into a bundle

Triggered by: *"wrap my FastAPI app"*, *"turn this project into a bundle"*,
*"заверни приложение в бандл"*.

Same steps as above, but between (3) and (4) the skill reads the user's
application code to map its entry points, APIs, and data onto bundle primitives
(`@api`, `@ui_main`, `@cron`, `@ui_widget`, `@venv`, etc.). The app source is
copied into `HOST_BUNDLES_PATH/<bundle-id>/` (or a subdirectory) and called
from `entrypoint.py` — the original tree is not modified.

### 3. Add a feature to an existing bundle

Triggered by: *"add a cron to this bundle"*, *"expose a new API"*, *"добавь
фичу в бандл"*.

1. Read the existing `entrypoint.py` and the relevant docs section.
2. Make the minimal change that adds the feature.
3. Run `bundle-tests`, then `reload` + `verify-reload`.

## What a bundle can contain

A single KDCube bundle can combine:

- a Python backend entry point
- authenticated APIs (`@api(route="operations")`)
- public APIs (`@api(route="public", public_auth=...)`)
- widgets (`@ui_widget(...)`) and/or a full iframe UI (`@ui_main`)
- storage, deploy-scoped and user-scoped props and secrets
- scheduled jobs (`@cron(...)`)
- dependency-isolated helpers (`@venv(...)`)
- React v2, Claude Code, or custom agents
- optional Node/TypeScript behind a Python bridge

## Read-order (enforced)

Authoring rule #1 in the skill: **never write bundle code from memory**.
Decorators, import paths, and descriptor fields change between releases.

**The plugin ships without docs on disk.** Always fetch via `WebFetch` from raw
GitHub URLs. If `CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT` is set, read local files
instead (strip the `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/`
prefix and use `Read`).

### Tier 1 — always read, every bundle task (in this order)

1. `app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md` — routing entry point; read first
2. `app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md` — testing / QA
3. `app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md` — authoring / implementation
4. `app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md` — props, secrets, runtime config ownership model
5. `app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md` — required any time the bundle lives outside `host_bundles_path` or `bundles.yaml`/`assembly.yaml` is touched

Plus the reference bundle end-to-end (README + entrypoint.py + any descriptor files):
`app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/`

### Tier 2 — read only on demand

Pull when Tier 1 does not answer the specific thing you are implementing.
Apply **header-first gate**: fetch, read the title and first section (≈30 lines),
confirm it covers the topic, then read the rest.

SDK deep-dives under `app/ai-app/docs/sdk/bundle/`:
`bundle-index-README.md`, `bundle-reference-versatile-README.md`,
`bundle-dev-README.md`, `bundle-runtime-README.md`,
`bundle-platform-integration-README.md`, `bundle-props-secrets-README.md`,
`bundle-node-backend-bridge-README.md`, `bundle-knowledge-space-README.md`,
`bundle-widget-integration-README.md`, `bundle-scheduled-jobs-README.md`,
`bundle-venv-README.md`, and others by topic.

Descriptor docs under `app/ai-app/docs/configuration/` — read only when editing
that specific descriptor: `assembly-descriptor-README.md`,
`bundles-descriptor-README.md`, `bundles-secrets-descriptor-README.md`,
`gateway-descriptor-README.md`, `secrets-descriptor-README.md`.

Specialized examples: `kdcube.copilot@...` (knowledge-space / custom resolvers),
`with-isoruntime@...` (isolated exec), `resources/node-backend-bridge` (Node/TS bridge).

**Note:** versatile is NOT a reference for `@cron` or `@venv` — it does not use
them. Read `bundle-scheduled-jobs-README.md` / `bundle-venv-README.md` from Tier 2
for those decorators.

## Placement and registration rules

- **The bundle directory can live anywhere on the host.** Default is
  `~/.kdcube/bundles/<bundle-id>/`; Desktop, a project dir, or any other real
  directory works equally well.
- **Use real directories, not symlinks.** Symlinks do not work across Docker
  volume mounts. Copy source in; don't link it.
- **`bundles.yaml` `path` must be the container path**, not the host path.
  A host-path entry makes `reload` succeed-looking but a no-op — this is the #1
  silent failure.
- **`<bundle-id>` must be filesystem-safe** and match the `id` registered in
  `bundles.yaml`.

### Container path formula

The runtime mounts `HOST_BUNDLES_PATH` (from `$WORKDIR/config/assembly.yaml →
paths.host_bundles_path`) into `/bundles`. The container path for a bundle is:

```
/bundles/<path-of-bundle-dir-relative-to-host_bundles_path>
```

This is **not** necessarily `/bundles/<bundle-id>` — it depends on where the
bundle directory sits relative to `host_bundles_path`. Recommended `bundles.yaml`
registration:

```yaml
bundles:
  items:
    - id: "<bundle-id>"
      name: "<Human Name>"
      path: "/bundles/<relative-path-from-host_bundles_path>"
      module: "entrypoint"
```

### Mount coverage

If the chosen host dir is inside `HOST_BUNDLES_PATH`, it is already visible.
If it is **outside** (e.g. Desktop or a project dir), edit
`assembly.yaml → paths.host_bundles_path` to the parent that contains the bundle,
then rebuild with `kdcube --workdir $WORKDIR --build --upstream` so the new mount
takes effect. The `bootstrap <bundle-id> <bundle-dir> --host-bundles-path <parent>`
helper does the same in one call. Prefer putting multiple bundles under one parent
to avoid re-bootstrapping each time.

## Workdir resolution

Every authoring step starts by resolving the workdir:

1. `CLAUDE_PLUGIN_OPTION_KDCUBE_WORKDIR` / `KDCUBE_WORKDIR`.
2. Probe the standard dotfile location `~/.kdcube/kdcube-runtime`.
3. Fall back to `kdcube_local.py status`.

If nothing resolves, the skill **asks the user** in one short message rather
than guessing or silently bootstrapping.

## Validation

After any authoring step:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" bundle-tests /abs/path/to/bundle
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" reload <bundle-id>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" verify-reload <bundle-id>
```

`bundle-tests` requires `kdcube_repo_root` to be configured — it runs the
shared suite from the repo's `kdcube_ai_app.apps.chat.sdk.tests.bundle` module
with `PYTHONPATH` pointed at the repo's Python source root.

See [runtime-flows.md](./runtime-flows.md) for why `verify-reload` is mandatory.