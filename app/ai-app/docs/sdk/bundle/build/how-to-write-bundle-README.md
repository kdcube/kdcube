---
id: ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
title: "How To Write A Bundle"
summary: "Authoring guide for bundle creators and integrators: bundle shape, lifecycle, decorators, runtime surfaces, configuration and storage decisions, and how to turn a product idea or existing app into a deployable bundle."
tags: ["sdk", "bundle", "authoring", "workflow", "widget", "api", "testing"]
keywords: ["bundle authoring guide", "bundle creator path", "bundle integrator path", "end to end bundle design", "decorator selection", "runtime surface selection", "widget api mcp cron on_job choices", "configuration and storage decisions", "bundle lifecycle design", "reference authoring patterns"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-release-bundle-content-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/bundle-storage-and-cache-README.md
  - ks:docs/sdk/storage/cache-README.md
  - ks:docs/sdk/storage/git-store-README.md
  - ks:docs/sdk/storage/sdk-store-README.md
---
# How To Write A KDCube Bundle

This document is written for a builder agent or engineer who must create or maintain bundles in this repo.

It is not a conceptual overview.
It is the working instruction set for doing the job correctly.

If you are not yet sure where this page fits in the full reading order, start
with [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md).

Tier 1 rule:

- this page is one part of the Tier 1 pack
- do not treat it as sufficient on its own
- read it together with the Tier 1 test, configuration, and configure/run pages

Primary references:

- bundle docs under `docs/sdk/bundle/`
- the reference bundle:
  `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

Use this document together with:

- [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md)
- [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
- [how-to-assemble-bundle-with-sdk-building-blocks-README.md](how-to-assemble-bundle-with-sdk-building-blocks-README.md)
- [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
- [how-to-release-bundle-content-README.md](how-to-release-bundle-content-README.md)
- [bundle-developer-guide-README.md](../bundle-developer-guide-README.md)
- [versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md)
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
- [bundle-widget-integration-README.md](../bundle-widget-integration-README.md)
- [bundle-runtime-README.md](../bundle-runtime-README.md)
- [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)

## 1. Working Method

When you build a bundle, do not invent the platform contract from memory.

Work in this order:

1. read the test guide first so you know the runtime contract you must satisfy
2. read the SDK building-block map so you reuse existing integrations,
   solutions, tools, storage, widgets, and job helpers
3. read the relevant bundle docs
4. inspect the `versatile` reference bundle for the nearest working pattern
5. inspect the platform implementation only when docs/reference are not enough
6. then write the bundle
7. then run the shared bundle suite and bundle-local tests
8. then verify the actual UI/API runtime behavior

Practical rule:

- test expectations are part of requirements, not only post-build validation
- docs define the intended contract
- `versatile` shows the reference bundle shape
- platform source is the last resort for unresolved edge cases
- when live runtime behavior disagrees with tests or expectations, follow
  [how-to-test-bundle-README.md#1d-runtime-log-and-timeline-checks](how-to-test-bundle-README.md#1d-runtime-log-and-timeline-checks)
  before reading platform internals or changing product logic

Configuration/runtime rule:

- use this page for how to structure the bundle code
- use [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md) for `assembly.yaml`, `bundles.yaml`, `bundles.secrets.yaml`, `kdcube --build --upstream`, and `kdcube --info`

Tier 1 role of this page:

- use it first when you are creating a new bundle
- use it first when you are wrapping existing user code into a bundle
- use it for creator and integrator work, not as the main configurator,
  deployer, or QA page
- do not use it as the main runtime setup guide or the main test guide

## 1A. What A Bundle Is

A KDCube bundle is a descriptor-addressed application unit that the platform
can discover, load, expose, and run through one or more surfaces.

In practical terms, a bundle is:

- code resolved from a bundle entry in `bundles.yaml`
- runtime metadata declared by decorators in `entrypoint.py`
- deployment-scoped non-secret config from bundle props
- deployment-scoped secret config from bundle secrets
- optional local mutable filesystem state under bundle storage
- optional remote state in platform or external storage systems

A bundle is not only a chat workflow.

It may expose one or more of these surfaces:

- on-message/chat handling
- authenticated operations APIs
- public APIs
- widgets
- bundle main UI apps
- MCP endpoints
- scheduled jobs

Operational rule:

- think of a bundle as a product module with a runtime contract
- descriptors decide how it is wired into the environment
- decorators decide what interfaces it exposes
- runtime context decides which execution path the code is in

Environment rule:

- one `tenant/project` runtime is one isolated environment
- use a different `tenant/project` when you need separate customer data or
  separate stages such as `dev`, `staging`, and `prod`
- keep multiple bundles inside one `tenant/project` when they belong to the
  same environment

So a bundle is the end-to-end application unit inside an environment.
`tenant/project` is the environment boundary, not the bundle boundary.

## 1D. If You Are Wrapping Existing Code

Treat the existing application code and the bundle adapter as different layers.

Preferred structure:

- keep business logic, schemas, and external API adapters reusable
- keep KDCube-specific decorators and runtime calls close to `entrypoint.py`
- move deployment-scoped config into bundle props or bundle secrets
- move user-owned runtime state into user props or user secrets

Do not port the whole legacy application into one giant bundle class.

The bundle should be the KDCube-facing integration boundary, not the place where
all product logic becomes entangled with platform wiring.

### If the existing backend is Node or TypeScript

Use this split:

- Python bundle = public KDCube application shell
- Node or TS backend = internal backend of that bundle

Keep in Python:

- `@api(...)`
- `@mcp(...)`
- `@ui_widget(...)`
- `@cron(...)`
- `@on_job`
- auth and roles
- props and secrets resolution

Keep in Node or TS:

- existing backend logic
- internal bridge routes
- optional live reconfigure handler

Use the public pattern, not a custom subprocess design:

- [bundle-node-backend-bridge-README.md](../bundle-node-backend-bridge-README.md)
- [node-backend-sidecar-README.md](../../node/node-backend-sidecar-README.md)

Builder rule:

- the Node backend is one implementation part of the bundle
- the bundle itself still belongs to Python from the platform point of view

## 1B. Bundle Lifecycle

When a bundle exists in a real environment, its lifecycle is:

1. A bundle entry in `bundles.yaml` identifies the code and supplies bundle props.
2. The platform resolves the bundle root/module and imports `entrypoint.py`.
3. Decorators are discovered and the bundle interface manifest is built.
4. The bundle becomes discoverable through integrations listing, subject to:
   - roles / user-types
   - bundle-level `enabled_config`
   - resource-level `enabled_config`
5. The bundle is then entered through one of the runtime paths:
   - chat/on-message
   - operations/public API
   - widget-driven operation calls
   - MCP endpoint dispatch
   - cron/scheduled job
   - background job stream / `@on_job`
6. During execution, the bundle reads:
   - effective bundle props via `bundle_prop(...)`
   - secrets via async helpers such as `get_secret_async(...)` and
     `get_user_secret_async(...)`
   - typed platform settings via `get_settings()`
7. Mutable state goes to the right tier:
   - bundle local storage for instance-local filesystem state
   - `AIBundleStorage` for bundle artifacts
   - DB/Redis/external systems for runtime/business state
8. Config changes are applied by reload/reconcile:
   - `bundles.yaml` / `bundles.secrets.yaml` changes
   - bundle reload
   - scheduler reconciliation for cron jobs
9. If effective bundle props changed:
   - `on_props_changed(...)` may reconcile long-lived side effects
   - internal sidecars may restart or reconfigure lazily on next use
10. After a turn finishes, errors, or is cancelled:
   - `on_turn_completed(...)` may release per-turn resources
   - the hook is best-effort, timeout-bounded, and must be fast/idempotent

Builder rule:

- design the bundle around this lifecycle explicitly
- do not treat the code as if it only ever runs from one widget click path

Practical hook rule:

- `on_bundle_load(...)` = one-time per process per tenant/project setup
- `on_props_changed(...)` = reconcile long-lived state after effective prop change
- `pre_run_hook(...)` = request-time validation or lazy reconcile before execution
- `on_turn_completed(...)` = fast per-turn cleanup after success, error, or
  cancellation; do not perform expensive reporting or user-facing delivery there

Async rule:

- lifecycle hooks should be `async def`
- prefer `async def` for `@api`, `@mcp`, `@ui_widget`, and `@cron` methods
- `@on_job` must be `async def`
- in async bundle code, use async secret helpers:
  `get_secret_async(...)`, `get_user_secret_async(...)`,
  `set_user_secret_async(...)`, and `delete_user_secret_async(...)`
- do not run blocking setup in a request path
- if expensive work is only needed once for shared bundle storage, make it idempotent and guard it with a storage signature plus a cross-process lock

Shared-storage rule:

- `singleton` does not mean machine-global or EFS-global setup
- multiworker proc and ECS tasks can load the same bundle in multiple Python processes
- `on_bundle_load` may run once per process unless the work is explicitly guarded
- generated UI builds, indexes, and shared workspace preparation must tolerate concurrent loaders

## 1B.1 New Bundle Skeleton Checklist

When creating a new bundle from scratch, create the smallest useful skeleton
before implementing product behavior.

Recommended first-pass shape:

```text
<bundle-id>/
  README.md
  release.yaml
  entrypoint.py
  config/
    bundles.template.yaml
    bundles.secrets.template.yaml
  docs/
    design/
      <bundle-design>.md
    journal/
      journal.md
  tests/
```

Add only the implementation folders the first milestone needs, for example:

```text
  services/
  tools/
  ui/
  ui-src/
  skills/
```

Skeleton file rules:

- `README.md` should have front matter with at least bundle id, title, summary,
  status, tags, module, singleton expectation, primary surfaces, and links to
  config/design/journal docs
- `release.yaml` may be empty until the first real release is cut
- when the user agrees to cut a release, fill `release.yaml` using
  [how-to-release-bundle-content-README.md](how-to-release-bundle-content-README.md)
- `entrypoint.py` should be loadable and thin, even if it only exposes a safe
  placeholder workflow/status API at first
- `config/bundles.template.yaml` documents non-secret deployment props
- `config/bundles.secrets.template.yaml` documents deployment-scoped bundle
  secrets only
- user-owned credentials and user state do not belong in descriptor templates
- `docs/design/` should contain the structured design that implementation will
  follow, not only raw notes
- `docs/journal/journal.md` should track important build decisions and
  bundle-builder-doc proposals while the bundle is being built
- update `docs/journal/journal.md` in the same change that alters runtime
  behavior, tool/skill contracts, storage semantics, user-scope mapping,
  release shape, or Tier 1 builder guidance

If the bundle needs external human setup before an integration can work, add an
operator-facing integration homework doc such as:

```text
docs/integrations/admin-integrational-homework.md
```

Use that doc for actions outside code, for example creating Telegram bots,
collecting webhook secrets, or recording which descriptor/secrets keys must be
filled later. Do not use it for user-owned settings such as a user's personal
email credentials; those belong in the bundle UI/user settings flow.

Local path descriptor rule:

```yaml
bundles:
  version: "1"
  default_bundle_id: "my.bundle@1-0"
  items:
    - id: "my.bundle@1-0"
      name: "My Bundle"
      path: "/Users/you/src/my-repo/src/my.bundle@1-0"
      module: "entrypoint"
      singleton: false
      config: {}
```

For seed/source descriptors used by local CLI setup or host-side processor runs,
`path` is the host-visible bundle root. The CLI may rewrite the staged runtime
copy under `workdir/config/` to the container-visible mount path when the
processor runs inside Docker.

For the full local path contract, use
[how-to-configure-and-run-bundle-README.md#local-path-bundles](how-to-configure-and-run-bundle-README.md#local-path-bundles).

### 1B.2 Bundle-Local Import Rule

Bundle code must load under both supported descriptor shapes:

```yaml
# bundle-root shape
subdir: "src/my_bundle"
module: "entrypoint"
```

```yaml
# parent-subdir shape
subdir: "src"
module: "my_bundle.entrypoint"
```

The same rule applies to local `path:` descriptors:

- `path: /Users/you/src/my-repo/src/my_bundle` with `module: entrypoint`
- `path: /Users/you/src/my-repo/src` with `module: my_bundle.entrypoint`

`module` is a Python import path. Dots in `module` are package separators, not
literal characters in a directory name. If the bundle directory name contains a
dot, prefer the bundle-root descriptor shape unless the filesystem layout
intentionally mirrors the dotted package path.

So bundle-local imports must not assume that only the bundle root is on
`sys.path`.

In `entrypoint.py`, use package-relative imports for bundle-local modules and
import reusable SDK components from their SDK package:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.tasks import AsyncTaskStorage

try:
    from .subsystems.common import storage_root_or_error
except ImportError:
    from subsystems.common import storage_root_or_error
```

In a nested bundle module, use the matching relative form for bundle-local code
and SDK imports for shared pieces:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.tasks import TaskStorage

try:
    from ..services.storage import UserMemoryStorage
except ImportError:
    from services.storage import UserMemoryStorage
```

Do not write imports that only work from the processor cwd or only work when the
bundle root itself is on `sys.path`, such as unconditional
`from services.storage import UserMemoryStorage`.

## 1C. Bundle Design Decision Matrix

Before writing code, classify the product surface and state model.

| Product need | Primary surface | Typical runtime path | Typical state/storage | Notes |
| --- | --- | --- | --- | --- |
| Copilot/chat experience | `@agentic_workflow` / `@on_message` | request-bound chat path | conversation stores, retrieval systems, bundle props | start here for assistant-style products |
| Admin console | `@ui_widget` + `@api(route="operations")` | widget -> operations | descriptor-backed config, bundle local storage, DB/Redis | keep admin separate from public/user surface |
| External webhook/integration | `@api(route="public")` | public HTTP path | bundle props + secrets, external systems | auth boundary must be explicit |
| Tool-serving integration | `@mcp(...)` | MCP dispatch path | bundle props + secrets, external systems | bundle owns MCP auth |
| Background automation | `@cron(...)` plus `@on_job` for ready work | cron scan -> Redis job stream -> proc `@on_job` | bundle local storage, DB/Redis, external APIs | cron detects due work; `@on_job` executes it fairly |
| Mixed product app | combine widget/API/chat/cron intentionally | multiple runtime paths | split state by storage tier | this is common; design boundaries explicitly |

State-placement rule:

- bundle props/secrets:
  deployment-scoped configuration
- bundle local storage:
  instance-local mutable files/workspaces/caches
- `AIBundleStorage`:
  persisted bundle artifacts
- DB/Redis/external APIs:
  runtime or business state

## 1D.1 Reuse SDK Building Blocks First

Before creating a new `services/`, `subsystems/`, `tools/`, or provider adapter
module, check:

- [how-to-assemble-bundle-with-sdk-building-blocks-README.md](how-to-assemble-bundle-with-sdk-building-blocks-README.md)

Current reusable blocks include:

- Tasks Solution for saved tasks, schedules, executions, execution artifacts,
  due scans, task tools, and task/job skills;
- Email Integration for Gmail/iCloud accounts, OAuth/settings, attachment
  materialization, Email MCP, and Claude Code email processing;
- Telegram Integration for webhooks, Bot API rendering, progress streaming,
  Mini App auth, widget operations, registry storage, and signed downloads;
- Delivery Integration for email/Telegram report delivery and delivered-file
  metadata;
- built-in web, browser, rendering, exec, io, and context tools;
- storage/cache/git helpers, widgets, MCP, `@cron`, `@on_job`, `@venv`, and
  Node sidecar support.

Bundle code should normally supply:

- route aliases and decorators;
- product prompts and skills;
- user-scope and role policy;
- UI composition;
- deployment prop/secret paths;
- domain-specific storage that is not already part of a reusable SDK block.

## 1E. SDK Configuration And Secrets Cheat Sheet

Keep this page compact, but do not hide the actual SDK helpers.

Use this quick map while writing code:

| What you need | Read | Write |
| --- | --- | --- |
| platform/global props | `get_settings()` | none |
| platform/global secrets | `await get_secret_async("canonical.key")` | none |
| deployment-scoped bundle props | `self.bundle_prop("path", default=...)`, `self.bundle_props` | `await set_bundle_prop(...)` |
| deployment-scoped bundle secrets | `await get_secret_async("b:...")` | `await set_bundle_secret(...)` |
| user-scoped bundle props | `get_user_prop(...)`, `get_user_props()` | `set_user_prop(...)`, `delete_user_prop(...)` |
| user-scoped bundle secrets | `await get_user_secret_async(...)` | `await set_user_secret_async(...)`, `await delete_user_secret_async(...)` |

Bundle user-scope rule:

- `user_id` in bundle storage, user props, and user secrets means the current
  bundle user scope. It is not guaranteed to be a KDCube control-plane account id.
- In KDCube-authenticated chat/widgets it may be the logged-in KDCube user.
- In public integrations such as Telegram it may be a bundle-approved external
  identity or a stable synthetic scope such as `telegram_<telegram_user_id>`.
- Roles/auth and user scope are related but separate: a Telegram user can be
  registered/admin for this bundle without owning a KDCube login.
- Never require every external user to map to a KDCube account unless the
  product design explicitly says so.

Hard rule:

- bundle code reads all scopes
- bundle code writes bundle-scoped and user-scoped values only
- bundle code does not write platform/global props or secrets
- sync secret helpers still exist for compatibility, but new async request
  paths should use the async helpers

If long-lived helpers depend on bundle props:

- recompute or invalidate them in `on_props_changed(...)`
- do not assume a singleton bundle instance keeps prop-derived state valid forever

Use the full contract page only when you need the deeper ownership, storage, or
export rules:

- [bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)

## 1F. SDK Storage, Cache, And Git Cheat Sheet

Use this compact map while writing bundle code:

| Need | Use | Typical purpose |
| --- | --- | --- |
| local mutable files on this instance | `self.bundle_storage_root()` | workspaces, cloned repos, local indexes, generated files |
| same local root outside entrypoint code | `bundle_storage_dir(...)` | helper code that has no `self` |
| persisted bundle artifacts | `AIBundleStorage` | artifact read/write/list/delete through the storage backend |
| lightweight Redis cache | `create_kv_cache()` or namespaced cache helpers | small runtime cache, flags, lightweight transient state |
| git subprocess auth/transport | `build_git_env(...)`, `normalize_git_remote_url(...)` | PAT/SSH-safe git commands without mutating process-global env |

Hard rule:

- local mutable filesystem state -> bundle storage helper
- persisted bundle artifacts -> `AIBundleStorage`
- lightweight transient cache -> KV cache
- git auth/transport -> shared git helper, not custom `os.environ` mutation

Use the deeper docs only when needed:

- [bundle-storage-and-cache-README.md](../bundle-storage-and-cache-README.md)
- [cache-README.md](../../storage/cache-README.md)
- [git-store-README.md](../../storage/git-store-README.md)
- [sdk-store-README.md](../../storage/sdk-store-README.md)

## 2. Decide What Kind Of Bundle You Are Building

Before writing code, classify the bundle.

Typical bundle surfaces:

- chat-first workflow bundle
- operations/API bundle
- widget bundle
- main UI bundle
- MCP-serving bundle
- scheduled-job bundle
- mixed bundle with several surfaces

You should explicitly decide:

- what the primary user-facing surface is
- which methods are read-only
- which methods mutate state
- whether there is a separate admin surface
- what state must persist locally on the instance
- what state must be descriptor-backed

Do not collapse all authority into one public widget.

Preferred split:

- one end-user-facing React widget/web app when the product is naturally one app
- separate admin APIs for privileged operations
- separate admin widget only when the product needs a distinct admin app
- scheduled jobs for background automation

## 2.1 Process Environment Boundary

Multiple applications may run inside the same processor process.

That means:

- inherited processor environment variables are shared by design
- bundle code must not treat `os.environ` as private mutable state

For git-backed helpers in particular:

- read git configuration through `get_settings()` / `get_secret_async()`
- build a subprocess env dict for git commands
- pass that env only to the git subprocess
- do not write `GIT_HTTP_TOKEN`, `GIT_SSH_COMMAND`, or similar values back into the processor process env

Correct pattern:

```python
import asyncio
import subprocess

from kdcube_ai_app.apps.chat.sdk.config import get_secret_async

async def fetch_repo():
    env = build_git_env(
        git_http_token=await get_secret_async("services.git.http_token"),
        git_http_user=await get_secret_async("services.git.http_user"),
    )
    await asyncio.to_thread(
        subprocess.run,
        ["git", "fetch", "--prune", "origin"],
        env=env,
        check=True,
    )
```

Interpretation:

- inherited process env remains shared
- descriptor-backed settings/secrets remain the normal source of truth
- explicit overrides remain local to the subprocess call only

Transport rule:

- git-backed workspace or storage repos may be configured with either HTTPS or SSH remotes
- if HTTPS token auth is configured, the shared helper prefers that path and may normalize an
  SSH-style remote such as `git@github.com:org/repo.git` to `https://github.com/org/repo.git`
- if SSH transport is intended, configure the SSH settings explicitly:
  - `GIT_SSH_KEY_PATH`
  - `GIT_SSH_KNOWN_HOSTS`
  - `GIT_SSH_STRICT_HOST_KEY_CHECKING`
- do not half-configure both modes and assume git will choose the intended one silently

Operationally:

- HTTPS + PAT is usually the simpler deployment choice
- SSH is supported, but it requires key and host-verification material to be mounted and configured

## 3. Start From The Minimal Bundle Shape

Recommended layout:

```text
my_bundle/
  entrypoint.py
  orchestrator/
    workflow.py
  tools_descriptor.py
  skills_descriptor.py
  requirements.txt    # optional, but required when bundle-local venv code needs Python deps
  tools/
  skills/
  ui-src/             # optional main-view React/Vite source folder
  widgets/            # optional widget React/Vite source folders
  tests/
```

Required in practice:

- `entrypoint.py`
- bundle registration decorators
- compiled graph or equivalent execution path

Usually present:

- `orchestrator/workflow.py`
- `tools_descriptor.py`
- `skills_descriptor.py`
- `requirements.txt` when bundle-local Python deps are installed through `@venv(...)`

If the bundle ships a full main UI app:

- put source in `ui-src`
- declare `ui.main_view` with the source folder and build command in the bundle configuration
- let the bundle UI loader build into bundle storage
- use the loader-provided build destination such as `<VI_BUILD_DEST_ABSOLUTE_PATH>` when the build system needs the output path
- treat `VITE_BUNDLE_ID` or equivalent build-time values as fallbacks; the parent config bridge still supplies the runtime bundle id
- do not treat the built runtime storage directory as source

If the bundle ships a React widget/web app:

- put the widget app source under a stable widget folder such as `widgets/<widget-alias>`
- declare `ui.web_app_widgets.<alias>.src_folder` and `build_command`
- use the standard build command shape:
  `npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build`
- make Vite write to `process.env.OUTDIR`; do not pass the output directory as
  `vite build <path>`
- keep the decorated `@ui_widget(alias="<alias>")` method as the manifest/entrypoint surface only
- expose structured data/mutation APIs separately through `@api(route="operations")`
- let the loader build the widget into bundle storage; do not render a TSX source file from Python for new widgets
- for the full source-folder widget contract, use
  [bundle-widget-integration-README.md](../bundle-widget-integration-README.md)

## 4. Copy The Right Reference Pattern

Use `versatile` as the default reference bundle.

Reference doc:

- [versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md)

Reference bundle root:

- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

Do not guess from older example bundles first.
Start from this reference unless your task is specifically about a capability it
does not cover.

Study in this order:

1. `entrypoint.py`
2. `orchestrator/workflow.py`
3. `tools_descriptor.py`
4. `skills_descriptor.py`
5. `ui/PreferencesBrowser.tsx`
6. `ui-src/src/App.tsx`
7. `tests/`

What `versatile` is good for:

- entrypoint and graph bootstrap
- workflow orchestration
- bundle-local tools and skills
- widget and operations integration
- public endpoint example
- bundle main UI example
- bundle storage usage

What `versatile` is not the reference for:

- `@cron`
- `@venv`

Use dedicated docs for those:

- [bundle-scheduled-jobs-README.md](../bundle-scheduled-jobs-README.md)
- [bundle-venv-README.md](../bundle-venv-README.md)

### React V2/V3 With Bundle Tools And Skills

Use React when the bundle's behavior should be driven by tools and skills.
Do not put business behavior directly in a public webhook or REST method if the
same behavior belongs to the agent.

For the full integration map across React descriptors, bundle-served MCP, MCP
client config, and Claude Code subagents, read
[bundle-agent-integration-README.md](../bundle-agent-integration-README.md).

Canonical examples:

- `versatile@2026-03-31-13-36` for the general descriptor/workflow pattern
- `kdcube.copilot@2026-04-03-19-05` for a production-style React workflow

Minimal shape:

```text
my.bundle@1-0/
  entrypoint.py
  orchestrator/
    workflow.py
  tools_descriptor.py
  skills_descriptor.py
  tools/
    task_tools.py
    user_memory_tools.py
  skills/
    product/
      tasks/
        SKILL.md
        tools.yaml
      user_memory/
        SKILL.md
        tools.yaml
```

Entrypoint responsibilities:

- register the bundle
- build the one-node graph that initializes SDK services
- instantiate the bundle workflow
- pass the turn state to `workflow.process(...)`
- keep public/operations APIs thin

Workflow responsibilities:

- construct the turn scratchpad
- call `start_turn(...)`
- persist the user message
- call `build_react(...)` with `tools_descriptor` and `skills_descriptor`
- run `react.run(...)`
- call `react.persist_workspace()`
- call `finish_turn(...)`

Descriptor rules:

- expose bundle-local tools through `tools_descriptor.py`
- expose skill prompts through `skills_descriptor.py`
- make skill `when_to_use` rules operational, not vague
- for stateful skills, distinguish read/retrieval use from write/reconcile use
- use separate tool aliases for separate domains
- do not collapse different product concepts into one alias just because they
  are used by the same agent
- do not add generic SDK tools unless this bundle actually needs them
- skill visibility filters should use the real React decision agent ids:
  `solver.react.v2.decision.v2.strong` and
  `solver.react.v2.decision.v2.regular`
- avoid stale/legacy consumer ids in bundle descriptors; the ids should match
  runtime logs, accounting metadata, and model routing

Example split:

```python
TOOLS_SPECS = [
    {"module": "kdcube_ai_app.apps.chat.sdk.solutions.tasks.tools", "alias": "tasks", "use_sk": True},
    {"ref": "tools/user_memory_tools.py", "alias": "user_memory", "use_sk": True},
]
```

React version:

- React V2/V3 is selected by descriptor-backed platform config
  `ai.react.react_agent_version`
- do not hardcode the version in bundle code
- write the bundle against `BaseWorkflow.build_react(...)`

Channel rule:

- React writes to the communicator and timeline
- the timeline is the durable source of truth for what happened in a turn
- transport adapters such as Telegram should trigger or route agent work, then
  derive transport-specific output from the turn result/timeline
- do not duplicate task or memory business logic in the transport webhook

Stateful asset rule:

- durable user/product assets should have one source-of-truth file per asset
  under bundle storage when local filesystem storage is appropriate
- use Markdown with YAML frontmatter for human-editable assets such as tasks or
  user memories
- frontmatter carries id, status, ownership, access policy, search labels,
  relations, schedule, and execution metadata
- the Markdown body carries the durable human-facing content, such as the task
  description or memory statement
- generated SQLite indexes are rebuildable retrieval surfaces, not source of
  truth
- expose search tools/APIs for assets that the agent must modify or delete, so
  the agent can find the right existing asset before changing it
- tool descriptions should state the intended scenario and sequence, for
  example search existing memory first, comment it when it matches, and create
  only when no existing memory captures the durable signal
- user-visible memory must have explicit policy metadata, for example
  `access_policy.visible_to_user: true`
- memory widget data may mark returned user-visible entries and comments as
  seen when the product explicitly treats widget open as a read receipt
- scheduled tasks should record the React execution conversation id that will be
  continued when the task fires

Gate rule:

- a separate gate LLM call is optional
- use it only when the bundle needs classification/routing/title generation
  that cannot be handled deterministically
- for simple task/memory bundles, start with a deterministic prepare step plus
  the React solver

Use `kdcube.copilot@2026-04-03-19-05/orchestrator/workflow.py` as the workflow
shape when in doubt.

## 4.1 Copyable Feature Snippets

Use these as the smallest correct starting points.

### Authenticated API

```python
@api(alias="task_list", route="operations", method="GET", user_types=("registered",))
async def task_list(self, **kwargs):
    return {"items": []}
```

Reference:
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)

### Public API with explicit platform auth

```python
@api(
    alias="incoming_webhook",
    route="public",
    method="POST",
    public_auth={"mode": "header_secret", "header": "X-Webhook-Secret", "secret_key": "incoming.secret"},
)
async def incoming_webhook(self, **kwargs):
    return {"ok": True}
```

Reference:
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)

### Public API with bundle-owned auth

```python
from fastapi import HTTPException, Request
from kdcube_ai_app.apps.chat.sdk.config import get_secret_async

@api(alias="telegram_webhook", route="public", method="POST", public_auth="bundle")
async def telegram_webhook(self, request: Request, **kwargs):
    header_name = self.bundle_prop("telegram.webhook.auth.header_name", "X-Telegram-Bot-Api-Secret-Token")
    expected_token = await get_secret_async("b:telegram.webhook.auth.shared_token")
    if request.headers.get(header_name) != expected_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"ok": True}
```

Reference:
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
- [bundle-transports-README.md](../bundle-transports-README.md)
- [bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
- [bundle-developer-guide-README.md](../bundle-developer-guide-README.md)

### Widget plus structured API

```python
@api(alias="task-board", route="operations", method="POST", user_types=("registered",))
@ui_widget(alias="task-board", icon={"tailwind": "heroicons-outline:check-badge"}, user_types=("registered",))
def task_board(self, **kwargs):
    return [self._render_dashboard_html(content=rendered_tsx, title="Task Board")]

@api(alias="task-board-api", route="operations", method="POST", user_types=("registered",))
async def task_board_api(self, **kwargs):
    return {"items": []}
```

Reference:
- [bundle-widget-integration-README.md](../bundle-widget-integration-README.md)

### Public MCP

```python
@mcp(alias="docs_public", route="public", transport="streamable-http")
def docs_public_mcp(self, **kwargs):
    return build_docs_mcp_app()
```

Reference:
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
- [bundle-transports-README.md](../bundle-transports-README.md)

### Bundle-authenticated MCP

```python
from fastapi import HTTPException, Request
from kdcube_ai_app.apps.chat.sdk.config import get_secret_async

@mcp(alias="docs", route="operations", transport="streamable-http")
async def docs_mcp(self, request: Request, **kwargs):
    header_name = self.bundle_prop("mcp.docs.auth.header_name", "X-Docs-MCP-Token")
    expected_token = await get_secret_async("b:mcp.docs.auth.shared_token")
    if request.headers.get(header_name) != expected_token:
        raise HTTPException(status_code=401, detail=f"Missing or invalid {header_name}")
    return build_docs_mcp_app()
```

Reference:
- [bundle-transports-README.md](../bundle-transports-README.md)
- [bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)

### Scheduled job

```python
@cron(alias="sync", expr_config="task_tracker.sync", span="system")
async def sync(self, **kwargs):
    await self._sync_tasks()
```

Reference:
- [bundle-scheduled-jobs-README.md](../bundle-scheduled-jobs-README.md)

### Platform-gated surface with `enabled_config`

```python
@ui_widget(
    alias="task-board",
    icon={"tailwind": "heroicons-outline:check-badge"},
    user_types=("registered",),
    enabled_config="features.task_board.widget_enabled",
)
def task_board(self, **kwargs):
    return ["<div id='root'></div>"]
```

```yaml
bundles:
  items:
    - id: "task.board@1-0"
      config:
        features:
          task_board:
            widget_enabled: true
```

Use this when the platform should hide or suppress the surface directly instead
of the bundle method deciding at runtime.

### Bundle props and secrets

```python
async def sync_external(self):
    enabled = self.bundle_prop("features.auto_sync", False)
    api_key = await get_secret_async("b:external.api_key")
    ...
```

Reference:
- [bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)

### Bundle local storage

```python
root = self.bundle_storage_root()
workspace = root / "_task_tracker"
workspace.mkdir(parents=True, exist_ok=True)
```

Reference:
- [bundle-storage-and-cache-README.md](../bundle-storage-and-cache-README.md)

### Per-bundle virtualenv helper

```python
@venv(requirements="requirements.txt")
def render_report(payload: dict) -> dict:
    return {"ok": True, "payload": payload}
```

Reference:
- [bundle-venv-README.md](../bundle-venv-README.md)

## 4.2 Feature Gating With `enabled_config`

This feature is important enough to treat as a first-class authoring tool.

`enabled_config` is the platform-native feature flag for bundle surfaces.

You can attach it to:

- `@agentic_workflow(...)`
- `@api(...)`
- `@mcp(...)`
- `@ui_widget(...)`
- `@cron(...)`

What it does:

- resolves a dot-path against effective bundle props
- if the resolved value is disabled, the platform suppresses that surface
- missing path means enabled
- bundle-level disable overrides resource-level settings

Current disabled values:

- boolean `False`
- integer `0`
- strings `false`, `disable`, `disabled`, `off`, `0`

Use it for:

- staged rollout
- environment-specific exposure
- disabling one job/widget/API without deleting code
- temporarily hiding unfinished surfaces

Do not use it for:

- secrets
- per-user authorization
- complex business predicates that depend on request payload or database state

Authoring rule:

- put the flag value in bundle props under `bundles.yaml -> config:`
- keep the decorator path stable
- let the platform do the 404/scheduler suppression instead of duplicating the check in method bodies

## 5. Entrypoint Rules

Every bundle should make the entrypoint simple and explicit.

Core requirements:

- register the bundle with `@agentic_workflow(...)`
- declare bundle identity with `@bundle_id(...)` when code-level identity matters
- compile the graph once in `__init__`
- keep route methods thin
- move real business logic into helper/service/orchestrator modules

Minimal pattern:

```python
from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, bundle_id
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.infra.service_hub.inventory import BundleState


@agentic_workflow(name="my.bundle", version="1.0.0")
@bundle_id("my.bundle@1.0.0")
class MyEntrypoint(BaseEntrypoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.graph = self._build_graph()

    def _build_graph(self):
        g = StateGraph(BundleState)
        g.add_node("orchestrate", self._orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()
```

Entrypoint responsibilities:

- runtime wiring
- surface declaration through decorators
- lightweight access control
- creation of helper/service objects
- passing runtime context into those helpers

Entrypoint should not contain:

- large HTML blobs unless unavoidable
- business logic mixed with route handling
- direct deployment/env assumptions
- ad hoc local path construction next to the source tree

## 6. Runtime Context Rules

This must be explicit in the builder’s mental model.

Different bundle execution paths expose different runtime surfaces.
Do not write code as if every path looked like a request-bound widget call.

### Chat turn / SSE / socket-driven request

This is the normal processor-driven request path.

In this path, entrypoint code has request-bound runtime context:

- `self.comm`
- `self.comm_context`
- actor/session/routing details
- `self.bundle_props`
- `self.pg_pool`
- `self.redis`
- storage helpers
- `get_secret_async(...)` / `get_user_secret_async(...)`

This is the path where communicator behavior is request-bound and peer/session-aware.

### REST bundle operation path

Bundle operations called through `/api/integrations/bundles/.../operations/...` also run with request-bound runtime context.

In this path, entrypoint code also has:

- `self.comm`
- `self.comm_context`
- `self.bundle_props`
- DB/Redis handles when available

So the practical rule is:

- chat/SSE path: request-bound comm context exists
- REST operations path: request-bound comm context also exists

If a widget or iframe calls a bundle operation, do not treat it as a detached background job.

### Cron / scheduled-job path

Cron is different.

When code runs from `@cron(...)`, there may be no meaningful end-user actor/session/socket context.

Do not assume in cron code that you have:

- a real user actor
- a request routing session
- a socket target
- request-bound streaming semantics

Cron-safe assumptions:

- bundle props are available
- storage helpers are available
- DB/Redis handles are available when configured
- request/actor/communicator details may be absent or not user-scoped

Practical rule:

- do not build cron logic around `self.comm_context.actor`
- do not depend on request headers or peer state
- pass explicit tenant/project/bundle scope into subsystem helpers when needed
- if cron discovers user/task work that may take time, enqueue a background job
  and handle it in `@on_job` instead of doing all work inside the cron tick

### Background job / `@on_job` path

`@on_job` is for ready work claimed by proc from the background job stream. It
is not a browser route and is not called through `/operations`.

Use it when:

- a scheduler scan finds due work
- a widget/API "run now" request should queue work instead of blocking the UI
- a bundle subsystem needs fair processor claiming and retry behavior

Rules:

- define at most one `@on_job` method in the bundle entrypoint
- make it `async def`
- validate `job["work_kind"]`
- read durable domain ids from `job["payload"]`
- use `job["metadata"]` only for transport/runtime hints such as
  `conversation_id`, `turn_id`, or display text
- update the bundle-owned execution/status/result record from inside the handler
- treat retry as possible until proc acknowledges the stream message

Minimal pattern:

```python
from kdcube_ai_app.infra.plugin.agentic_loader import cron, on_job

class MyBundle(BaseEntrypoint):
    @cron(alias="due-scan", cron_expression="*/5 * * * *", span="system")
    async def scan_due_work(self):
        due_items = await self.tasks.find_due_items()
        for item in due_items:
            await self.tasks.enqueue_job(item)

    @on_job
    async def on_job(self, job: dict, **kwargs) -> dict:
        del kwargs
        if job.get("work_kind") == "task.execution.due":
            return await self.tasks.run_execution(job["payload"]["execution_id"])
        return {"ok": False, "error": {"code": "unsupported_job"}}
```

### Tool execution in normal in-process runtime

Tool modules do not get the same surface as the bundle entrypoint.

They should use the documented tool bindings such as:

- `_SERVICE` / `SERVICE`
- `_INTEGRATIONS` / `INTEGRATIONS`
- `_TOOL_SUBSYSTEM`
- `_COMMUNICATOR`
- `_KV_CACHE`
- `_CTX_CLIENT`

For common tool context, prefer:

- `kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context.scope()`
- `ok(...)` / `error(...)`
- `log_tool_start(...)`, `log_tool_success(...)`, `log_tool_error(...)`
- `host_files(...)` when a trusted bundle/catalog tool has materialized files
  that should become current-turn hosted artifacts

That helper resolves tenant/project/bundle id, bundle user scope, user type,
conversation/turn ids, bundle props, bundle storage root, output/work dirs, and
`bundle_call_context`.

Do not assume a tool module has:

- `self.comm`
- `self.comm_context`
- arbitrary entrypoint internals

Tool signature rule:

- only expose parameters the model can reasonably know or derive from the chat
- do not expose runtime ids such as `user_id`, `task_id`, `execution_id`,
  `conversation_id`, `turn_id`, internal `account_id`, or storage paths unless
  a previous tool returned an opaque reference specifically for the model to pass
- inject runtime identity through the tool subsystem, `bundle_call_context`, or
  job payload/context, then read it inside the tool implementation
- return opaque references for follow-up actions when a later tool needs exact
  storage/execution identity
- every model-facing tool description and return annotation must show the
  standard envelope and the concrete `ret` shape that will appear on the
  timeline. Do not stop at "Envelope: {ok, error, ret}" or "returns metadata".
  The solver chooses tools from this text.
- keep the shape compact but useful:
  `Envelope: {ok,error,ret}. ret={items:[{id,title,status}],count,next_cursor?}`
  is better than a long prose paragraph with no fields.
- if a tool returns provider/user data for later tool calls, include the exact
  identifiers and fields the model should reuse in the `ret` shape.
- if a tool returns user-visible files, return the standard envelope
  `{"ok": true, "ret": {"artifact_type": "files", "files": [...]}}` or use
  `host_files(...)` to host the files inside the tool before returning
- `host_files(...)` works only after the runtime has prepared the trusted tool
  context: active `ToolSubsystem`, hosting service, tenant/project/user/
  conversation/turn scope, conversation storage, and output directory. Normal
  React workflows prepare this through `BaseWorkflow.build_react(...)`;
  isolated execution prepares it through `bootstrap_bind_all(...)`.
- generated executor code should call a catalog tool through
  `agent_io_tools.tool_call(...)` when it needs file materialization or hosting;
  `host_files(...)` is for trusted bundle/catalog tools

### Tool execution in isolated runtime

Isolated runtime is narrower again.

It does not inherit arbitrary live Python objects from the host process.
It receives a reconstructed portable runtime contract.

That means:

- do not rely on random globals from the host process
- do not rely on live in-memory objects created in the parent process
- use only the documented portable surfaces
- trusted catalog tools still receive the reconstructed tool subsystem and can
  host files with `host_files(...)` when conversation storage is available

If code may run in isolated execution, write it as if only the documented bindings are available.

### Writing Rule

Before writing a method or helper, explicitly decide which runtime path it belongs to:

- request-bound entrypoint logic
- REST operation logic
- cron/system logic
- in-proc tool logic
- isolated tool logic

If code crosses those boundaries, make the dependency explicit instead of assuming one path behaves like another.

## 7. Singleton And Exclusivity Rules

These are related, but they are not the same thing.

### Bundle singleton

A bundle can be configured as `singleton`.

Meaning:

- the workflow instance is cached and reused inside the proc process
- subsequent requests reuse that same entrypoint instance instead of creating a fresh one each time

What singleton is good for:

- expensive bundle initialization you want to keep warm inside the process
- long-lived in-memory helpers that are safe to reuse
- reducing repeated setup cost

What singleton does **not** mean:

- it does not make bundle operations exclusive
- it does not serialize concurrent requests
- it does not give cross-process or cross-instance exclusivity
- it does not replace locks

Important runtime consequence:

- request-bound context is rebound on reuse
- singleton bundles must not treat request state as permanently stored on `self`
- singleton does not prevent another process, worker, or ECS task from loading the same bundle against the same storage

Practical rule:

- if the bundle is singleton, assume `self` is process-lifetime state
- request-specific data must come from the current request context, method arguments, or task-local/context-local surfaces
- shared filesystem or EFS work still needs an explicit shared-storage guard

### Exclusive operations

If you need “only one run at a time”, do not rely on `singleton`.

Use an explicit exclusivity mechanism.

#### For cron

Use `@cron(span=...)`.

This is the supported exclusivity control:

- `span="process"`
  - one run per proc process
- `span="instance"`
  - one run per host instance
- `span="system"`
  - one run across the whole deployed system for that tenant/project/bundle/job

For recurring background jobs, `span` is the first control to choose.

Default recommendation:

- use `span="system"` unless you explicitly want per-process or per-instance behavior

#### For non-cron operations

Use an explicit lock in the operation or subsystem logic.

Typical choices:

- Redis lock keyed by tenant/project/bundle/operation
- DB advisory lock or equivalent DB-scoped lock when appropriate
- local fallback lock only for standalone/local debugging

Practical rule:

- singleton controls instance reuse
- lock controls exclusivity

Do not confuse them.

## 8. Identity Rules

This is one of the easiest places to break bundles.

Runtime identity is descriptor-driven.
The source folder name is not authoritative when descriptors already define the bundle.

Authoritative identity sources, in order of trust:

1. loaded descriptor / `ai_bundle_spec.id`
2. explicit runtime bundle id passed into context
3. code fallback such as `@bundle_id(...)`
4. source folder name only as a last-resort local fallback

Do not build these from the source folder name when runtime already has descriptor context:

- storage roots
- workspace branches
- conversation IDs
- widget operation URLs
- admin operation URLs

If you ignore this, you will get split state:

- one local root for the source folder name
- another root for the runtime bundle id
- diverging branches, sessions, or archive trees

## 9. Configuration Rules

### Use the correct surface

For non-secret deployment config:

- `self.bundle_prop(...)`

For bundle-scoped secrets:

- `await get_secret_async("b:...")`

For platform/global secrets:

- `await get_secret_async("...")` or `await get_secret_async("a:...")`

For descriptor-file reads only when absolutely necessary:

- `get_plain(...)`

For platform settings:

- `get_settings()`

### Do not read deployment-owned config with raw `os.getenv(...)`

Bundle logic should not depend on raw env variable names for operational config when the platform already provides:

- `bundle_prop(...)`
- `get_settings()`
- `get_secret(...)`
- `get_plain(...)`

Treat this as prohibited in normal bundle code:

- do not call `os.getenv(...)` or read `os.environ[...]` for deployment-owned
  config or secrets
- do not invent bundle-local env variable names as a second config contract

Exception:

- direct env access is acceptable only in code that explicitly lives at the
  iso-runtime or sandbox boundary and is intentionally driven by process env

If you add a standalone helper script for local debugging:

- load `.env` into the platform settings path
- then read through `get_settings()` / `get_secret_async()`
- do not let the runtime bundle depend on bundle-local `.env` files

### Do not call the secrets provider directly

Bundle or feature code must not call secrets-provider internals such as
`get_secrets_manager(...).get_secret(...)` directly.

Use:

- `get_secret_async(...)` in async code
- `get_secret(...)` only in legacy sync-only code
- `get_settings()` for promoted secret-backed settings

Reason:

- direct provider calls bypass canonical key handling, env-first behavior, and
  mode-specific resolution
- they couple bundle code to one provider implementation instead of the
  supported helper contract

### Do not open descriptor YAML files through hardcoded paths

Bundle code must not open `assembly.yaml`, `bundles.yaml`, or other descriptor
YAML files through hardcoded filesystem paths.

Use:

- `get_plain(...)` for raw descriptor inspection
- `bundle_prop(...)` for effective bundle config
- `get_settings()` for effective typed platform/runtime settings

Reason:

- direct file opens hardcode one runtime path layout
- they bypass descriptor path indirection and alternate runtime wiring
- they are easier to break in direct local runs, tests, and non-default mounts

### Descriptor-backed values are the durable source of truth

If a setting must survive reload and deployment refresh, it belongs in descriptors and bundle props.

Typical examples:

- cron expression
- default window sizes
- feature toggles
- workspace repo/branch overrides
- validation toggles

## 10. Local Storage Rules

If the bundle needs mutable filesystem state on the proc instance, use the platform helper.

Do not:

- write mutable runtime data into the bundle source tree
- create a repo-relative `.runtime/` folder for operational data
- assume current working directory is stable or durable

Use:

- `self.bundle_storage_root()`
- or `bundle_storage_dir(...) / "_subsystem"` only when you are outside entrypoint code and do not have `self.bundle_storage_root()`

Use local bundle storage for:

- cloned repos
- local archive mirrors
- prepared indexes
- cron workspaces
- temporary generated files that belong to this instance

This is separate from `AIBundleStorage`.

Mental model:

- local bundle storage = instance-visible filesystem
- `AIBundleStorage` = backend storage API for bundle artifacts
- hosted conversation files = current-turn user-visible artifacts; use
  `ret.artifact_type == "files"` with `ret.files[]` or `host_files(...)`
  instead of treating bundle storage paths as deliverable links

## 11. Widget Design Rules

Widget bundles fail most often because authors treat them like isolated frontends.
They are not.

KDCube widgets are React/TSX web apps rendered inside a platform iframe shell.
Do not create ad hoc HTML fragments unless you are maintaining a legacy widget.
For a product with several panels, prefer one React widget with internal tabs or
routes over several disconnected widgets.

New widgets should use the same source-folder/build/storage model as main UI.
The usual bundle config shape is:

```yaml
ui:
  web_app_widgets:
    task_memo_webapp:
      enabled: true
      src_folder: widgets/task_memo_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

The loader builds that source folder into shared bundle storage under
`ui/widgets/<alias>`. Browser requests to widget subpaths fall back to the built
`index.html`, so a single widget can support tabs/routes and later be reused as
a Telegram WebApp surface.

Use `npm ci` when the widget source folder commits a lockfile. For early
prototype widgets without a lockfile, `npm install --no-package-lock` avoids
mutating the source folder during loader builds.

Source-folder behavior is per widget alias. If a subclass inherits other
`@ui_widget` methods from `BaseEntrypoint`, those widgets continue to use the
legacy method-rendered HTML path unless their own alias also has
`ui.web_app_widgets.<alias>.src_folder/build_command`.

### Required contract

The widget must:

- request runtime config from the parent frame
- accept both `CONN_RESPONSE` and `CONFIG_RESPONSE`
- use host-provided auth tokens
- build operation URLs from runtime config

Required config fields:

- `baseUrl`
- `accessToken`
- `idToken`
- `idTokenHeader`
- `defaultTenant`
- `defaultProject`
- `defaultAppBundleId`

Do not hardcode:

- tenant
- project
- bundle id
- localhost URLs
- source-folder names in operation URLs

For custom main-view UI apps, use the same config bridge. The value sent as
`defaultAppBundleId` is the runtime bundle id selected by the host. Use it for
`/sse/chat`, `/api`, `/mcp`, and widget calls. A compiled bundle id is only a
standalone fallback.

For `/sse/chat`, new conversations must omit `conversation_id`. The UI should
bind the server-generated conversation id from the HTTP ack or the first SSE
envelope.

### Widget routes and subpaths

The side panel fetches widgets through:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{widget_alias}
```

That API returns a JSON envelope containing the rendered widget HTML for the
platform iframe. The same route can serve direct HTML when requested by a
browser, and subpaths are supported for single-web-app routing:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{widget_alias}/{widget_path}
```

If the widget method accepts `widget_path` or `path`, the platform passes the
subpath so the React app can select its initial route or tab. This is the
preferred shape when the same widget will become a Telegram WebApp.

If a source-folder widget must load before platform auth exists, for example as
a Telegram Mini App, use the public static route for the app shell:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{widget_alias}/{widget_path}
```

Only static widget assets are public through that route. The widget's data and
action APIs still need their own bundle-level request auth, such as Telegram
WebApp `initData` verification on every request.

### Separate display and structured API

Recommended pattern:

- widget method:
  - `@ui_widget(alias="task-board", ...)`
- compatibility operation on the same method if needed:
  - `@api(alias="task-board", route="operations", ...)`
- separate structured backend API:
  - `@api(alias="task-tracker-api", route="operations", method="POST", ...)`

The widget should call the structured API alias, not the widget alias.

### Public and admin capabilities should be separated

Good pattern:

- one end-user React web app when the product is naturally one app
- admin controls are separate panels or routes only when the product requires
  them in that app
- mutating/admin operations are always separate `@api` methods with roles

Do not expose destructive or administrative operations without role checks.
Use widget composition for UX, and API roles for authority.

### Read-only load by default

Initial widget load should not mutate external state.

Exception: a widget may perform a small explicit read-receipt mutation when the
product defines opening the widget as acknowledgement, such as marking returned
memory entries/comments as seen. This should be documented in the widget API and
must not trigger expensive sync, execution, commit, push, or rebuild work.

Prefer:

- initial load: read-only bootstrap
- explicit button such as `Refresh`, `Sync`, `Run now`, `Save settings` for mutations

This avoids accidental pushes or expensive jobs on every widget open.

### Operation body shape

For widgets, preferred POST body shape is:

```json
{ "data": { "operation": "bootstrap", "payload": { ... } } }
```

The integrations layer also accepts raw JSON objects, but widget code should use the platform wrapper consistently.

Also remember:

- integrations responses are enveloped
- widgets should unwrap the `[alias]` field in the response body

## 12. Access Control Rules

Use `user_types` and `roles` correctly.

Current `user_types` order:

- `anonymous < registered < paid < privileged`

This is threshold-based, not exact-match.

Examples:

- `user_types=("registered",)` means registered-or-higher
- `user_types=("paid",)` means paid-or-higher
- `user_types=("privileged",)` means privileged only

Use `roles=(...)` for raw external roles such as:

- `kdcube:role:super-admin`

If both `user_types` and `roles` are present:

- both must pass

For admin widgets and APIs, use the platform’s privileged pattern.

In entrypoints derived from `BaseEntrypoint`, prefer:

- `_ensure_privileged(...)`

This keeps the access check consistent with the rest of the platform.

## 13. Scheduled Jobs And Background Pipelines

If the bundle runs background work through `@cron(...)` and `@on_job`, treat it as an operational subsystem.

Rules:

- lock the job so concurrent instances do not corrupt shared work
- use Redis lock with TTL when runtime Redis is available
- keep local fallback lock only for standalone/local use
- use bundle local storage for the working root
- keep schedule and first-run/default-window settings in bundle props
- use `@cron(span=...)` as the primary exclusivity control for scheduled jobs
- use `@on_job` for the actual ready-work execution when the work is per-user,
  long-running, retryable, or should be claimed fairly across processors
- assume schedules are reconciled on startup, bundle registry updates, and effective bundle-props changes
- scheduled logic should read current props through the normal runtime path, not cached startup-only values

For automation that may still need operator control:

- keep cron for regular background runs
- make "run now" enqueue the same `@on_job` work shape as a due cron item
- expose a privileged admin API/widget for:
  - changing schedule
  - changing default window
  - running now
  - deleting bad outputs
  - rebuilding indexes or archives

## 14. Standalone Scripts Inside Bundles

Sometimes a bundle subsystem benefits from a local standalone runner for debugging.
That is acceptable, but only under these rules:

- standalone mode is for local development/debugging
- operational runtime must still work entirely through KDCube wiring
- standalone env must be loaded into `get_settings()` / `get_secret_async()`
- operational config must still come from descriptors/bundle props in real runtime

Do not let a successful standalone path hide a broken runtime path.

When a subsystem has both:

- standalone mode
- in-bundle runtime mode

you must test both.

## 15. Pitfalls That Recur In Real Bundle Work

### Pitfall: using the source folder name as runtime bundle id

Symptom:

- storage root, workspace branch, or session path differs between runtime and standalone

Fix:

- resolve bundle id from descriptor/runtime context first

### Pitfall: repo-relative mutable runtime folders

Symptom:

- state ends up under the bundle source tree
- reloads and operational data get mixed together

Fix:

- use bundle local storage helper

### Pitfall: widget only listens for `CONFIG_RESPONSE`

Symptom:

- widget gets stuck waiting for config in some host paths

Fix:

- accept both `CONN_RESPONSE` and `CONFIG_RESPONSE`

### Pitfall: widget builds `////operations/...` URLs

Symptom:

- missing tenant/project/bundleId in generated request path

Fix:

- treat config handshake as mandatory
- refuse to call operation endpoints when config is incomplete

### Pitfall: widget initial load mutates remote state

Symptom:

- opening the widget triggers syncs, commits, or background work

Fix:

- initial load read-only
- explicit buttons for mutating actions

### Pitfall: Python f-string HTML/JS/CSS builders with unescaped braces

Symptom:

- runtime `NameError` from CSS like `@page{...}` or JS template placeholders `${...}`

Fix:

- inside Python f-strings, escape literal braces as `{{` and `}}`
- test HTML-builder functions directly, not only by syntax compile

### Pitfall: runtime config read via `os.getenv`

Symptom:

- bundle works only under one local shell shape
- runtime descriptors and props are ignored

Fix:

- use `bundle_prop(...)`, `get_settings()`, `get_secret_async(...)`, `get_plain(...)`

### Pitfall: direct descriptor file reads through hardcoded paths

Symptom:

- bundle works only when descriptors happen to be mounted at one expected path
- direct local runs or alternative runtime layouts break

Fix:

- use `get_plain(...)` for raw descriptor inspection
- use `bundle_prop(...)` or `get_settings()` for effective runtime values

### Pitfall: direct secrets-provider calls from bundle code

Symptom:

- bundle is coupled to one secrets backend
- alias handling, env-first behavior, or provider substitution is bypassed

Fix:

- use `get_secret_async(...)` in async code
- use `get_settings()` for promoted secret-backed settings

### Pitfall: writing cron logic as if it were a request-bound widget/API call

Symptom:

- code expects actor/session/socket details during scheduled execution
- cron path breaks or behaves inconsistently

Fix:

- treat cron as system/background execution
- pass explicit scope into helpers
- do not assume request-bound `comm_context` details exist

### Pitfall: writing isolated-exec code against host-process globals

Symptom:

- helper works in one local path but fails in isolated execution

Fix:

- use only documented tool/runtime bindings
- assume isolated runtime reconstructs a narrow portable surface

### Pitfall: assuming `singleton` makes an operation exclusive

Symptom:

- concurrent requests or jobs still overlap
- state corruption happens despite singleton bundle configuration

Fix:

- use `singleton` only for instance reuse
- use `@cron(span=...)` or an explicit lock for exclusivity

### Pitfall: public widget and admin authority mixed together

Symptom:

- access model becomes unclear
- widget load surface becomes dangerous

Fix:

- keep privileged operations in separate role-protected APIs
- use a separate admin widget only when the UX should be a distinct admin app

## 16. Writing Checklist

Before considering the bundle “implemented”, verify:

- entrypoint decorators are correct
- runtime identity does not depend on folder name
- all mutable local state uses bundle storage helper
- all deployment config uses bundle props/settings/secrets instead of raw env
- widgets follow the iframe config handshake
- widget URLs are built from runtime config
- public load paths are read-only by default
- admin surfaces are separated and privileged
- singleton is used only when process-level instance reuse is actually wanted
- scheduled/background work is locked
- exclusivity is implemented with `span` or explicit locks, not by singleton
- cron/background logic does not assume request-bound comm context
- isolated-exec code does not assume host-process globals
- destructive operations are explicit
- bundle-local tests exist for bundle-specific logic
- shared bundle suite passes

## 17. Minimum Deliverable Standard

A bundle implementation is not complete when it only “works once”.

It is complete when:

- it follows the documented platform contract
- it survives reloads
- runtime identity is stable
- widget/API surfaces are discoverable
- state is stored in the correct tier
- local and runtime execution both work
- the shared test suite and bundle-local tests pass

If you are unsure, default to the simpler, more explicit design:

- thin entrypoint
- service/helper module
- separate admin surface
- descriptor-backed settings
- bundle local storage for mutable filesystem state
