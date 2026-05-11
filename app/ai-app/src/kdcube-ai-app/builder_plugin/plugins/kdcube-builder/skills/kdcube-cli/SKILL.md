---
description: >
  Direct kdcube CLI operations — init workdir, start/stop the stack, reload bundles,
  inject secrets, save operator defaults, export live bundles. TRIGGER when: user wants
  to init/start/stop KDCube, inject API keys/secrets, reload a bundle via CLI, clean Docker,
  export bundles from AWS, set operator defaults, or asks about kdcube CLI flags.
  SKIP: bundle authoring or the full edit→reload→verify development loop (use kdcube-dev).
allowed-tools: Bash, Read, Write, WebFetch
---

# KDCube CLI

Direct wrapper around the `kdcube` CLI.

## Reference docs (fetch before answering CLI questions)

| Doc | URL |
|---|---|
| CLI quickstart & command table | repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_cli/README.md |
| `kdcube bundle` full reference (source, identity, config/secrets patch, delete) | repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_cli/additional_README.md |
| Current CLI contract (all commands, flags, env overrides) | repo:kdcube-ai-app/app/ai-app/docs/service/cicd/cli-README.md |
| Bundle configure & run workflow | repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md |
| CLI as control plane design (reload, init, defaults) | repo:kdcube-ai-app/app/ai-app/docs/service/cicd/design/cli--as-control-plane-README.md |
| PyPI package reference | https://pypi.org/project/kdcube-cli/ |

## Doc cache

CLI reference docs are cached locally to avoid repeated fetches. Cache TTL: 24 hours.

**Before fetching any Reference doc:**

```bash
CACHE="${CLAUDE_PLUGIN_ROOT}/cache/cli-docs.md"
python3 -c "import os,time; exit(0 if os.path.isfile('$CACHE') and time.time()-os.path.getmtime('$CACHE')<86400 else 1)"
```

- Exit 0 → cache is fresh. Use `Read` on `${CLAUDE_PLUGIN_ROOT}/cache/cli-docs.md` instead of WebFetch.
- Exit 1 → cache is stale or missing. Fetch the relevant docs via WebFetch, then write the full
  result to the cache file using the `Write` tool:
  `${CLAUDE_PLUGIN_ROOT}/cache/cli-docs.md`
  Include `<!-- cached: <ISO timestamp> -->` as the first line.

Fetch the relevant doc(s) via WebFetch when the user asks about a specific command or flag
not covered by the quick-reference below.

`kdcube bundle <bundle_id>` manages a bundle entry in `bundles.yaml` / `bundles.secrets.yaml`
without editing YAML by hand. It can create a new entry or update an existing one — switching
the source between a local host-mounted path and a git repo (with optional ref and subdir),
setting identity fields (display name, module, singleton flag), patching config values or
secrets by dotted key path (e.g. `llm.model`, `routines.heartbeat.cron`), and deleting
individual keys or the whole entry. All flag groups can be combined in one atomic call.
Changes are staged to disk; apply them to the running proc with `kdcube reload <bundle_id>`.

## Command surface

Current CLI uses subcommands:

| Subcommand | Purpose |
|---|---|
| `kdcube init` | Stage descriptors, prepare workdir (does not start containers) |
| `kdcube start` | Launch Docker Compose stack |
| `kdcube stop` | Stop running stack |
| `kdcube reload <bundle_id>` | Reapply bundle descriptors and clear proc cache |
| `kdcube bundle <bundle_id>` | Create / update / delete a bundle entry in bundles.yaml |
| `kdcube export` | Export live bundle descriptors from AWS Secrets Manager |
| `kdcube defaults` | Save persistent operator preferences |
| `kdcube --info` | Show configured defaults and active deployment lock state |

## Resolving the workdir

Before running any command, resolve the active workdir:

1. Check `CLAUDE_PLUGIN_OPTION_KDCUBE_WORKDIR` — use it if set.
2. Otherwise check `KDCUBE_WORKDIR` env var.
3. Otherwise look for `config/.env` upward from CWD and in `~/.kdcube/kdcube-runtime`.
4. Fall back to `~/.kdcube/kdcube-runtime`.

Workdir resolution precedence when `--workdir` is omitted from a subcommand:
1. `--workdir` flag — explicit, takes precedence.
2. `default_workdir` in `~/.kdcube/cli-defaults.json` — set via `kdcube defaults`.
3. Neither → error with guidance to pass `--workdir` or run `kdcube defaults`.

## Intent map

| User says | Command                                                                   |
|---|---------------------------------------------------------------------------|
| init workdir / setup descriptors | see **Init flow**                                                         |
| start stack | `kdcube start --workdir <workdir>`                                        |
| stop stack | see **Stop flow**                                                         |
| reload bundle via CLI | see **Reload flow**                                                       |
| register / create / update / delete bundle entry | fetch `kdcube bundle` reference from **Reference docs**                   |
| switch bundle source / change git repo | fetch `kdcube bundle` reference from **Reference docs**                   |
| patch bundle config or secrets by dotted key | fetch `kdcube bundle` reference from **Reference docs**                   |
| inject secrets / set API key | see **Secrets flow**                                                      |
| clean docker / clean images | `kdcube clean`                                                            |
| reset config | `kdcube init --reset-config`                                              |
| save operator defaults | see **Defaults flow**                                                     |
| export bundles from AWS | see **Export flow**                                                       |
| show active deployment / lock state | `kdcube --info`                                                           |
| what CLI flags are there | read repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_cli/README.md |

## Init flow

Descriptor fast-path (non-interactive when `assembly.yaml`, `secrets.yaml`, and `gateway.yaml`
are complete):

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --workdir ~/.kdcube/kdcube-runtime
```

Source selector — choose exactly one:

```bash
--latest           # latest released platform ref
--upstream         # latest origin/main state
--release <ref>    # pin specific release, e.g. 2026.4.11.012
# (omit all → reads platform.ref from assembly.yaml)
```

`--build` builds images after staging but does not start containers:

```bash
kdcube init --descriptors-location <dir> --upstream --build
```

If the descriptor set is incomplete, the CLI falls back to the guided interactive setup.

## Start flow

```bash
# Start an already-initialized workdir
kdcube start --workdir ~/.kdcube/kdcube-runtime/tenant__project

# Rebuild images before starting (convenience rebuild on an existing workdir)
kdcube start --workdir <workdir> --build
```

## Stop flow

Stop stack only:

```bash
kdcube stop --workdir <workdir>
```

Stop and remove volumes (full reset — all local Postgres/Redis data will be lost):

```bash
kdcube stop --workdir <workdir> --remove-volumes
```

Always confirm with the user before running `--remove-volumes`.

## Reload flow

Reload a bundle after descriptor changes — reapplies bundle descriptors and clears proc cache:

```bash
kdcube reload <bundle_id> --workdir <workdir>
```

After CLI reload, confirm cache rotation took effect:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" verify-reload <bundle_id>
```

For the full edit→reload→verify development loop, use the `kdcube-dev` skill instead.

## Defaults flow

Persist operator preferences so `--workdir` can be omitted from subsequent commands:

```bash
kdcube defaults \
  --default-tenant acme \
  --default-project prod
```

Inspect configured defaults and verify the lock state of the active deployment:

```bash
kdcube --info
kdcube --info --tenant acme --project prod
```

## Secrets flow

# TODO

## Single-deployment guard

The CLI maintains `~/.kdcube/cli-lock.json` to prevent concurrent deployments. Starting a
different `tenant/project` while another stack is live triggers an abort with guidance to
stop the active deployment first. `kdcube --info` verifies the lock against live
`docker compose ps`; stale locks (deployment stopped externally) are cleared automatically.

`tenant/project` is the environment boundary — use separate values for customer isolation or
lifecycle stages (`dev`, `staging`, `prod`). Keep multiple bundles inside one `tenant/project`
when they belong to the same environment.

## General rules

- If `kdcube` is not found in PATH, tell the user to install via `pip install --user kdcube-cli`.
- After `clean`, warn that the next start will re-pull or rebuild images.
- Always confirm before running `stop --remove-volumes`.