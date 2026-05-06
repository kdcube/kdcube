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

## Get started

### Interactive wizard (first run)

```bash
kdcube
```

The wizard creates a runtime workdir, generates config files, and optionally
builds images and starts the stack.

### Descriptor-driven init (automated)

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

## Workdir scopes

Every runtime lives under a **namespaced workdir**:

```
~/.kdcube/kdcube-runtime/<tenant>__<project>/
```

The namespace comes from `assembly.yaml → context.tenant` and
`context.project`. With default values this becomes `default__default`.

```
~/.kdcube/kdcube-runtime/
├── default__default/       # default scope
├── acme__staging/          # acme tenant, staging project
└── acme__prod/             # acme tenant, prod project
```

Each scope is fully isolated — its own config, data, logs, and running stack.

### Persistent defaults

Save your most-used workdir so you can omit `--workdir` from every command:

```bash
kdcube defaults \
  --default-workdir ~/.kdcube/kdcube-runtime/<tenant>__<project> \
  --default-tenant <tenant> \
  --default-project <project>
```

### Single-deployment guard

The CLI writes `~/.kdcube/cli-lock.json` on `start` and clears it on `stop`.
Starting a **different** scope while another is live aborts with a message
showing what is running and how to stop it first.

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
| `kdcube bundle <bundle_id>` | Patch bundle source, identity, config, or secrets |
| `kdcube export` | Export live `bundles.yaml` / `bundles.secrets.yaml` |

### Configuration

| Command | What it does |
|---|---|
| `kdcube defaults` | Save persistent `--workdir`, `--tenant`, `--project` defaults |
| `kdcube --info` | Show global CLI state and resolved runtime info |
| `kdcube --reset` | Re-prompt for config values without deleting files |

---

## `kdcube bundle` — manage bundles at runtime

Patch a staged bundle — source, identity, config, or secrets — without touching
YAML files by hand. Changes are staged and take effect after `kdcube reload`.

**Source mode** — switch where proc loads the bundle from:

```bash
# Local path (container-visible path under /bundles/)
kdcube bundle <bundle_id> --local-path /bundles/my.bundle

# Git repo (proc clones to /managed-bundles/ on reload)
kdcube bundle <bundle_id> \
  --git-repo git@github.com:org/my-bundle.git \
  --git-ref 2026.4.30

# Git monorepo with a bundle subdirectory
kdcube bundle <bundle_id> \
  --git-repo git@github.com:org/monorepo.git \
  --git-ref main \
  --git-subdir src/my.bundle
```

**Identity and config/secrets patch:**

```bash
# Set display name, module, singleton flag
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
the command creates a new entry (upsert). All other flags require an existing entry.
All non-delete flags can be combined in one invocation (single atomic write).
`--git-ref` is required with `--git-repo`. `--git-subdir` requires `--git-repo`.

---

## Full documentation

See `additional-README.md` in this package or the platform docs:  
https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/cli-README.md
