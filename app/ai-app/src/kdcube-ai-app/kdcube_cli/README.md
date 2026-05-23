# KDCube CLI

![KDCube CLI](https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_cli/pixel-cubes.png)

Bootstrap and operate a KDCube platform stack from the command line.

---

## Install

```bash
pip install kdcube-cli
```

Or with pipx (recommended):

```bash
pipx install kdcube-cli
```
---

## What You Build

With KDCube, you author **portable AI application bundles**.

A bundle can be a full AI application, an internal tool, a workflow backend,
a UI-backed product surface, an MCP server, a scheduled automation, or a mix of
these. It can use KDCube's built-in agent harnesses when agentic behavior is
needed, or it can be ordinary application code.

You focus on product behavior:

- what the app or workflow should do
- which APIs, screens, jobs, tools, MCP servers, or webhooks it exposes
- what state it reads and writes
- how users interact through chat, UI, messages, or external events
- which agent/runtime blocks it wants to use, if any
- how many conversations, tasks, or long-running threads it maintains

KDCube provides the hosting runtime around it: tenant/project isolation, auth,
routing, streaming, storage, conversation/message handling, service discovery,
hot reload, deployment wiring, and reusable AI runtime blocks.

Those AI runtime blocks can include ReAct-style agents, tool and skill
execution, isolated code execution, Claude Code integration, MCP access,
streaming progress, artifacts, and memory/search facilities.

In other words: you author the application module; KDCube hosts it and gives it
access to the platform and agent harnesses it needs.

---

## What is a bundle?

A **bundle** is the deployable application unit of the KDCube platform.

Concretely, it is a folder or git-backed source reference that contains bundle
code plus metadata describing the surfaces KDCube should expose. The platform
discovers the bundle, loads its entrypoint, wires its declared surfaces into the
runtime, and manages reload/lifecycle for it.

A bundle can expose any combination of:

- **HTTP APIs** — authenticated operations APIs or public webhook endpoints
- **Frontend assets** — bundle-owned UI/static assets served by the platform
- **MCP servers** — Model Context Protocol endpoints for agent/tool use
- **Scheduled jobs** — cron-driven background automation
- **Message handlers** — conversation/message workflows with attachments,
external events, `steer`, and `followup`
- **Agent workflows** — ReAct, tool/skill execution, code execution, or other
runtime blocks provided by KDCube

---

## Tenant, project, and workdir

Every KDCube runtime lives in a **namespaced workdir**:

```
~/.kdcube/kdcube-runtime/<tenant>__<project>/
```

`tenant` and `project` together define one **isolated environment** — its own
config, data, credentials, Postgres/Redis stores, and running stack. Use
separate namespaces for separate customers, products, or lifecycle stages
(dev, staging, prod).

```
~/.kdcube/kdcube-runtime/
├── default__default/       # default scope
├── acme__staging/          # acme tenant, staging project
└── acme__prod/             # acme tenant, prod project
```

Each scope is fully isolated — its own config, data, logs, and running stack.

For local seed descriptors, storage and host path fields may be left `null` or
omitted to use the tenant/project workdir defaults. For example,
`storage.kdcube: null` and `storage.bundles: null` resolve to:

```
~/.kdcube/kdcube-runtime/<tenant>__<project>/data/kdcube-storage
~/.kdcube/kdcube-runtime/<tenant>__<project>/data/bundle-storage
```

Explicit `file:///...` values select a custom local host path. Explicit
`s3://...` values are preserved as remote storage URIs.

### One machine, one running stack

A machine can hold **many workdirs** on disk, but only **one stack can run at
a time**. Starting a second workdir while another is live aborts with a message
showing what is running and how to stop it first.

### One workdir, many bundles

Inside one `tenant/project` environment you can register and run **any number
of bundles**. They share the same platform infrastructure — storage, auth,
Postgres, Redis, and the same deployment boundary.

This is the normal model: one environment, multiple application modules running
side by side.

### Bundles are portable across workdirs

A bundle is just code (a local path or a git repo) plus a descriptor entry in
`bundles.yaml`. The same bundle can be registered in multiple workdirs
independently — each workdir maintains its own config, secrets, and runtime
state for that bundle. This makes it straightforward to promote a bundle from
a `dev` environment to `staging` or `prod` by registering it in the target
workdir and supplying the appropriate descriptor values.

---



## Get started

### `init` is for first-time setup only

`kdcube init` creates a brand-new namespaced runtime workdir. It refuses if
the target workdir is already initialized (i.e. has `install-meta.json`).
To pick up platform code changes or rebuild images on an existing workdir,
use [`kdcube refresh`](#kdcube-refresh) instead.

### Plain init

The fastest way to get a local KDCube stack running — pick a tenant and a
project; the CLI creates the runtime under the platform default base
`~/.kdcube/kdcube-runtime/<tenant>__<project>/`:

```bash
kdcube init --tenant acme --project staging
kdcube start --tenant acme --project staging
```

To fill common service secrets during init:

```bash
kdcube init --tenant acme --project staging --prompt-secrets
```

To stage known secret values without prompts, use dotted descriptor keys:

```bash
kdcube init --tenant acme --project staging \
  --set-secret services.openai.api_key "sk-..." \
  --set-secret services.anthropic.api_key "sk-ant-..."
```

### Descriptor-driven init (reproducible / automated)

When you have a descriptor set (`assembly.yaml`, `bundles.yaml`, etc.):

```bash
kdcube init --tenant acme --project staging \
  --descriptors-location /path/to/descriptors
```

With a local platform source tree and image build:

```bash
kdcube init --tenant acme --project staging \
  --descriptors-location /path/to/descriptors \
  --path /path/to/kdcube-ai-app \
  --build
```

### Typical day-to-day flow

Pass `--tenant` / `--project` (or set them with `kdcube defaults`) to point
each command at the runtime you want. `--quiet` suppresses the banner; the
CLI auto-suppresses when stdout is not a TTY and when `--json` is requested.

```bash
# Start the stack
kdcube start --tenant acme --project staging

# Pick up platform code changes (rebuild images + restart)
kdcube refresh --tenant acme --project staging --build
kdcube refresh --tenant acme --project staging --release 2026.5.22.001 --build

# After editing a bundle's config or code — reload without a full restart
kdcube bundle reload <bundle_id> --tenant acme --project staging

# Stop the stack
kdcube stop --tenant acme --project staging
```

If you've set `kdcube defaults --default-tenant acme --default-project staging`,
you can drop `--tenant`/`--project` from these commands entirely.

### Runtime flow map

Use these three flows as the mental model for local KDCube operation.

Init is first-time setup for a runtime workdir:

```text
seed descriptors                         platform source
assembly.yaml / bundles.yaml / ...       --path / --upstream / --latest / --release
              |                                |
              +---------------+----------------+
                              v
              kdcube init --descriptors-location <dir> --build
                              |
                              v
       ~/.kdcube/kdcube-runtime/<tenant>__<project>/
       config/*.yaml + repo/ + compose/env files + data/
                              |
                              v
                         kdcube start
                              |
                              v
                  http://localhost:<port>/platform/chat
```

Refresh is for an already initialized runtime. It preserves staged descriptors:

```text
existing runtime workdir
config/*.yaml  ----------------------------- preserved
      |
      | optional platform source selector
      | none / --path / --upstream / --latest / --release
      v
kdcube refresh [selector] --build
      |
      +-- with --path: copy that local checkout into workdir/repo first
      +-- with --upstream/--latest/--release: update workdir/repo to that ref
      +-- with no selector: rebuild the already staged/recorded source
      +-- with --build: rebuild images
      +-- unless --no-restart: restart the stack
```

Bundle descriptor apply and reload are bundle-only operations. They do not
rebuild platform images or restart Docker:

```text
seed content descriptors
bundles.yaml + bundles.secrets.yaml
              |
              v
kdcube bundle config apply --descriptors-location <dir> [--dry-run]
              |
              v
active runtime bundle descriptors
workdir/config/bundles.yaml + bundles.secrets.yaml
              |
              v
kdcube bundle reload <bundle_id>
              |
              v
proc clears bundle cache and reloads code/config on the next request
```

Use `kdcube export` before replacing live runtime bundle descriptors with an
older seed copy. Export writes `bundles.yaml` and `bundles.secrets.yaml`;
local non-git bundle paths are normalized back to host paths, while git-backed
entries keep repo/ref/subdir and drop materialized runtime paths.

### Advanced workdir placement

When the runtime must live outside the default base (`~/.kdcube/kdcube-runtime`),
two advanced flags are available on `init` and `refresh`:

- `--workdir <full-path>` — explicit fully-qualified namespaced runtime
  (trailing path segment must contain `__`, e.g.
  `/opt/kdcube/acme__staging`).
- `--workdir-base <base> --tenant T --project P` — the CLI composes
  `<base>/<tenant>__<project>/` for you.

```bash
# Explicit full path:
kdcube init    --workdir /opt/kdcube/acme__staging
kdcube refresh --workdir /opt/kdcube/acme__staging --build

# Non-default base:
kdcube init --workdir-base /opt/kdcube --tenant acme --project staging
```

`--workdir` and `--workdir-base` are mutually exclusive. Other subcommands
(`start`, `stop`, `reload`, `bundle`, `info`, `export`) accept the same
`--tenant`/`--project` shape and a `--workdir` for the explicit form.

### `kdcube refresh`

Use `kdcube refresh` to apply platform-side changes (new images, updated
SDK code) on an existing initialized workdir. It stops the stack, rebuilds
images when `--build` is given, and restarts — **without** touching staged
descriptors (`bundles.yaml`, `bundles.secrets.yaml`, `assembly.yaml`,
`secrets.yaml`, `gateway.yaml`):

```bash
kdcube refresh --workdir ~/.kdcube/kdcube-runtime/<tenant>__<project> --build
```

Refresh also accepts the same platform source selectors as `init`:
`--latest`, `--upstream`, and `--release <ref>`. When you pass
`--path /path/to/kdcube-ai-app` without one of those selectors, refresh
restages that local platform source into `<workdir>/repo` before rebuilding.
When you do pass a selector, refresh checks out that selected ref and then
uses the staged `<workdir>/repo` copy. This keeps all compose build contexts
aligned with the same source tree while preserving staged descriptors.

`refresh` refuses if the workdir is not initialized; pair with
`kdcube init` for the first run, then use `refresh` for every subsequent
re-init.

---

## Persistent defaults

Save your most-used workdir so you can omit `--workdir` from every command:

```bash
kdcube defaults \
  --default-workdir ~/.kdcube/kdcube-runtime/<tenant>__<project> \
  --default-tenant <tenant> \
  --default-project <project>
```

---

## Command groups

### Lifecycle

| Command | What it does |
|---|---|
| `kdcube init` | First-time setup of a fresh runtime workdir: stage descriptors, generate env files, optionally stage local secrets, optionally build images. Refuses if the target workdir is already initialized. |
| `kdcube refresh` | Re-init an existing workdir: optionally select platform source with `--latest`, `--upstream`, or `--release <ref>`, stop the stack, rebuild images with `--build`, restart. Never touches staged descriptors. |
| `kdcube start` | Start the platform stack for an initialized workdir |
| `kdcube stop` | Stop the stack; `--remove-volumes` also wipes local volumes |

### Runtime operations

| Command | What it does |
|---|---|
| `kdcube bundle reload <bundle_id> [--json] [--quiet]` | Reapply bundle config and clear proc caches — no full restart needed |
| `kdcube bundle <bundle_id>` | Create, update, or delete a staged bundle entry |
| `kdcube bundle config apply --descriptors-location <dir> [--dry-run] [--reload]` | User/operator flow to reapply seed `bundles.yaml` / `bundles.secrets.yaml` to an existing runtime — no platform refresh |
| `kdcube export` | Export live `bundles.yaml` / `bundles.secrets.yaml`; local paths are normalized back to host descriptor paths |

### Configuration

| Command | What it does |
|---|---|
| `kdcube defaults` | Save persistent `--workdir`, `--tenant`, `--project` defaults |
| `kdcube info` | Show global CLI state (defaults + running deployment) |
| `kdcube info --show-defaults` | Show only the stored CLI defaults |
| `kdcube info --show-current-running-runtime` | Show only the currently running deployment |
| `kdcube info --workdir <path>` | Show resolved runtime info for a specific workdir |
| `kdcube info --tenant <t> --project <p>` | Show runtime info for tenant/project under the default runtime base |
| `kdcube init --reset-config` | (Legacy) Re-prompt for config values on a fresh init. Not applicable to already-initialized workdirs; use `kdcube refresh` for re-init. |
| `kdcube clean` | Clean local Docker cache and unused KDCube images |

---

## `kdcube bundle` — manage bundles at runtime

Create, update, or delete a staged bundle entry without touching YAML files by
hand. Changes are staged and take effect after `kdcube bundle reload`.

**Source mode** — point the bundle at a local path or a git repo:

```bash
# Local host path under paths.host_bundles_path; CLI stores the /bundles/... path
kdcube bundle <bundle_id> --local-path /Users/you/src/my.bundle

# Already runtime-visible path is also accepted
kdcube bundle <bundle_id> --local-path /bundles/my.bundle

# Git repo (platform clones to /managed-bundles/ on reload)
kdcube bundle <bundle_id> \
  --git-repo git@github.com:org/my-bundle.git \
  --git-ref 2026.4.30

# Git monorepo — bundle lives in a subdirectory
kdcube bundle <bundle_id> \
  --git-repo git@github.com:org/monorepo.git \
  --git-ref main \
  --git-subdir src/my.bundle
```

**Identity and config/secrets patch:**

```bash
# Set display name, entry module, singleton flag
kdcube bundle <bundle_id> \
  --name "My Bundle" --module entrypoint --singleton

# Patch config and secrets by dotted key path
kdcube bundle <bundle_id> \
  --set-config routines.heartbeat.cron "*/5 * * * *" \
  --set-secret api.token "sk-..." \
  --del-config features.legacy_mode

# Apply all staged changes
kdcube bundle reload <bundle_id>
```

Normal reload output is concise and operator-facing. Use `--verbose` only when
you need the raw Docker Compose command and full proc response. Use `--json`
for scriptable output.

```bash
# Delete a bundle entry (also removes its secrets entry)
kdcube bundle <bundle_id> --delete
```

**Descriptor apply** — when a user intentionally edits seed `bundles.yaml` /
`bundles.secrets.yaml` and wants to reapply that descriptor source of truth to
an existing runtime:

```bash
kdcube bundle config apply \
  --tenant acme \
  --project staging \
  --descriptors-location /path/to/descriptors \
  --dry-run

kdcube bundle config apply \
  --tenant acme \
  --project staging \
  --descriptors-location /path/to/descriptors \
  --reload
```

This is not a platform refresh: it touches only `bundles.yaml` and optional
`bundles.secrets.yaml` in the active runtime config directory. Host local
bundle paths from seed descriptors are translated to runtime `/bundles/...`
paths before staging.

**Status** — inspect one explicit bundle entry:

```bash
kdcube bundle status <bundle_id> --workdir ~/.kdcube/kdcube-runtime/<tenant>__<project>
```

By default this reports staged descriptor/path/runtime-service diagnostics only
and does not list other bundles. For local operator diagnostics, add `--live`
to ask localhost `chat-proc` to validate that same explicit bundle id:

```bash
kdcube bundle status <bundle_id> --live --json --workdir ~/.kdcube/kdcube-runtime/<tenant>__<project>
```

`--live` is an operator-level check for someone with local workdir and Docker
access. It does not emulate an end-user session or frontend visibility rules.

For scriptable runtime inspection, `kdcube info --json` emits defaults, the
running deployment lock, and runtime mount details when a workdir is selected.

When `--local-path` or `--git-repo` is given and the bundle doesn't exist yet,
the command creates a new entry (upsert). All other flags require an existing
entry. All non-delete flags can be combined in one invocation (single atomic
write). `--git-ref` is required with `--git-repo`. `--git-subdir` requires
`--git-repo`.

---

## Full documentation

See `additional-README.md` in this package or the platform docs:  
https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/cli-README.md
