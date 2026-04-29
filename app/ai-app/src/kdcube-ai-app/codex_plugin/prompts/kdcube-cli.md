# /kdcube-cli

Direct `kdcube` CLI operations — init workdir, start/stop the stack, reload bundles,
inject secrets, save operator defaults, export live bundles from AWS.

Take the intent from the text after `/kdcube-cli` and map it to the right command below.

## Reference docs (fetch before answering CLI questions)

| Doc | URL |
|---|---|
| CLI quickstart & command table | https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_cli/README.md |
| Current CLI contract (all commands, flags, env overrides) | https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/service/cicd/cli-README.md |
| Bundle configure & run workflow | https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md |
| CLI as control plane design (reload, init, defaults) | https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/service/cicd/design/cli--as-control-plane-README.md |
| PyPI package reference | https://pypi.org/project/kdcube-cli/ |

Fetch the relevant doc(s) via WebFetch when the user asks about a specific command or flag
not covered by the quick-reference below.

## Command surface

Current CLI uses subcommands:

| Subcommand | Purpose |
|---|---|
| `kdcube init` | Stage descriptors, prepare workdir (does not start containers) |
| `kdcube start` | Launch Docker Compose stack |
| `kdcube stop` | Stop running stack |
| `kdcube reload <bundle_id>` | Reapply bundle descriptors and clear proc cache |
| `kdcube export` | Export live bundle descriptors from AWS Secrets Manager |
| `kdcube defaults` | Save persistent operator preferences |
| `kdcube --info` | Show configured defaults and active deployment lock state |

## Resolving the workdir

Before running any command that needs `--workdir`:

1. `KDCUBE_WORKDIR` env var — use it if set.
2. Look for `config/.env` upward from CWD and in `~/.kdcube/kdcube-runtime`.
3. Fall back to `~/.kdcube/kdcube-runtime`.

Workdir resolution precedence when `--workdir` is omitted from a subcommand:
1. `--workdir` flag — explicit, takes precedence.
2. `default_workdir` in `~/.kdcube/cli-defaults.json` — set via `kdcube defaults`.
3. Neither → error with guidance to pass `--workdir` or run `kdcube defaults`.

## Intent map

| User says | Action |
|---|---|
| init workdir / setup descriptors | **Init flow** |
| start stack | `kdcube start --workdir <workdir>` |
| stop stack | **Stop flow** |
| reload bundle via CLI | **Reload flow** |
| inject secrets / set API key | **Secrets flow** |
| clean docker / clean images | `kdcube --clean` |
| reset config | `kdcube --reset` |
| save operator defaults | **Defaults flow** |
| export bundles from AWS | **Export flow** |
| show active deployment / lock state | `kdcube --info` |
| what CLI flags are there | read https://pypi.org/project/kdcube-cli/ |

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

If the descriptor set is incomplete the CLI falls back to the guided interactive setup.

## Start flow

```bash
# Start an already-initialized workdir
kdcube start --workdir ~/.kdcube/kdcube-runtime/tenant__project

# Rebuild images before starting
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
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" verify-reload <bundle-id>
```

For the full edit→reload→verify development loop, use `/kdcube-runtime` instead.

## Defaults flow

Persist operator preferences so `--workdir` can be omitted from subsequent commands:

```bash
kdcube defaults \
  --default-workdir ~/.kdcube/kdcube-runtime \
  --default-tenant acme \
  --default-project prod
```

Inspect configured defaults and verify the lock state of the active deployment:

```bash
kdcube --info
kdcube --info --workdir ~/.kdcube/kdcube-runtime/acme__prod
```

## Secrets flow

Always use `--secrets-set` (non-interactive — Codex has no interactive terminal):

```bash
kdcube --secrets-set ANTHROPIC_API_KEY=<key> --workdir <workdir>
kdcube --secrets-set OPENAI_API_KEY=<key> --workdir <workdir>
kdcube --secrets-set GIT_HTTP_TOKEN=<token> --workdir <workdir>
```

Multiple keys in one call:

```bash
kdcube --secrets-set ANTHROPIC_API_KEY=<key> --secrets-set OPENAI_API_KEY=<key> --workdir <workdir>
```

`--secrets-prompt` requires an interactive terminal — only suggest it when the user will
run the command themselves in their own shell, never run it from Codex.

After `--secrets-set` the CLI restarts `chat-proc` and `chat-ingress`. Reload active
bundles immediately after:

```bash
kdcube reload <bundle-id> --workdir <workdir>
```

## Export flow

Export live `bundles.yaml` + `bundles.secrets.yaml` from AWS Secrets Manager:

```bash
kdcube export \
  --tenant <tenant> --project <project> \
  --aws-region <region> \
  --out-dir /tmp/kdcube-export
```

Optional: `--aws-profile <profile>`, `--aws-sm-prefix <prefix>`.

Operational rule for `aws-sm` deployments: export the current live bundle state **before**
the next provision, reconcile into private descriptor source-of-truth files, then copy into
GitHub Environment secrets. Skipping this step can cause a later provision to replay stale
`BUNDLES_YAML` and overwrite runtime bundle changes.

## Single-deployment guard

The CLI maintains `~/.kdcube/cli-lock.json` to prevent concurrent deployments. Starting a
different `tenant/project` while another stack is live triggers an abort with guidance to
stop the active deployment first. `kdcube --info` verifies the lock against live
`docker compose ps`; stale locks are cleared automatically.

`tenant/project` is the environment boundary — use separate values for customer isolation or
lifecycle stages (`dev`, `staging`, `prod`). Keep multiple bundles inside one `tenant/project`
when they belong to the same environment.

## General rules

- If `kdcube` is not found in PATH, tell the user to run `pip install --user kdcube-cli`
  and add `~/Library/Python/3.x/bin` to PATH.
- After `--clean`, warn the user that the next start will re-pull or rebuild images.
- Always confirm before running `stop --remove-volumes`.