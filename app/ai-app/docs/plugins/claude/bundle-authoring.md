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
   (`/bundles/<bundle-id>`).
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

If `CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT` is set, the skill reads local files
(fast, no network); otherwise it falls back to GitHub URLs. Read order:

1. `bundle-index-README.md`
2. `bundle-reference-versatile-README.md`
3. `bundle-dev-README.md`
4. `bundle-runtime-README.md`
5. `bundle-platform-integration-README.md`
6. `bundle-props-secrets-README.md`
7. `bundle-node-backend-bridge-README.md`

All under `app/ai-app/docs/sdk/bundle/` in the repo.

Plus the reference bundle itself:
`app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@<date>`.

Specialized examples used when the task calls for them: `kdcube.copilot@...`
(knowledge-space / custom resolvers), `with-isoruntime@...` (isolated exec),
`resources/node-backend-bridge` (Node/TS bridge).

## Placement and registration rules

- **The bundle directory can live anywhere on the host.** Default is
  `~/.kdcube/bundles/<bundle-id>/`; Desktop, a project dir, or any other real
  directory works equally well. The plugin mounts it into the container at
  `/bundles/<bundle-id>`.
- **Use real directories, not symlinks.** Symlinks do not work across Docker
  volume mounts. Copy source in; don't link it.
- **`bundles.yaml` `path` must be the container path** (`/bundles/<bundle-id>`),
  not the host path. A host-path entry makes `reload` succeed-looking but a
  no-op — this is the #1 silent failure.
- **`<bundle-id>` must be filesystem-safe** and match the `id` registered in
  `bundles.yaml`.

### Mount coverage

The runtime mounts everything under `HOST_BUNDLES_PATH` (from
`$WORKDIR/config/.env`) into `/bundles`. If the chosen host dir is inside
`HOST_BUNDLES_PATH`, it is already visible. If it is **outside** (e.g. Desktop),
re-run `bootstrap <bundle-id> <bundle-dir>` — `cmd_bootstrap` sets
`host_bundles_path` to the bundle's parent — then restart the runtime. Prefer
putting multiple bundles under one parent to avoid re-bootstrapping each time.

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