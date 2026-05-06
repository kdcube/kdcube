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

### Plain init

The fastest way to get a local KDCube stack running:

```bash
kdcube init
kdcube start
```

`init` generates runtime config and env files under a namespaced workdir.
`start` launches the Docker Compose stack.

### Descriptor-driven init (reproducible / automated)

When you have a descriptor set (`assembly.yaml`, `bundles.yaml`, etc.):

```bash
kdcube init --descriptors-location /path/to/descriptors
kdcube start
```

With a local platform source tree and image build:

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --path /path/to/kdcube-ai-app \
  --build
kdcube start
```

### Typical day-to-day flow

```bash
# Start the stack
kdcube start --workdir ~/.kdcube/kdcube-runtime/<tenant>__<project>

# After editing a bundle's config or code — reload without a full restart
kdcube reload <bundle_id> --workdir ~/.kdcube/kdcube-runtime/<tenant>__<project>

# Stop the stack
kdcube stop --workdir ~/.kdcube/kdcube-runtime/<tenant>__<project>
```

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
| `kdcube init` | Stage descriptors, generate env files, optionally build images |
| `kdcube start` | Start the platform stack for an initialized workdir |
| `kdcube stop` | Stop the stack; `--remove-volumes` also wipes local volumes |

### Runtime operations

| Command | What it does |
|---|---|
| `kdcube reload <bundle_id>` | Reapply bundle config and clear proc caches — no full restart needed |
| `kdcube bundle <bundle_id>` | Create, update, or delete a staged bundle entry |
| `kdcube export` | Export live `bundles.yaml` / `bundles.secrets.yaml` |

### Configuration

| Command | What it does |
|---|---|
| `kdcube defaults` | Save persistent `--workdir`, `--tenant`, `--project` defaults |
| `kdcube --info` | Show global CLI state and resolved runtime info |
| `kdcube --reset` | Re-prompt for config values without deleting files |

---

## `kdcube bundle` — manage bundles at runtime

Create, update, or delete a staged bundle entry without touching YAML files by
hand. Changes are staged and take effect after `kdcube reload`.

**Source mode** — point the bundle at a local path or a git repo:

```bash
# Local path (container-visible path under /bundles/)
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
kdcube reload <bundle_id>
```

```bash
# Delete a bundle entry (also removes its secrets entry)
kdcube bundle <bundle_id> --delete
```

When `--local-path` or `--git-repo` is given and the bundle doesn't exist yet,
the command creates a new entry (upsert). All other flags require an existing
entry. All non-delete flags can be combined in one invocation (single atomic
write). `--git-ref` is required with `--git-repo`. `--git-subdir` requires
`--git-repo`.

---

## Full documentation

See `additional-README.md` in this package or the platform docs:  
https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/cli-README.md
