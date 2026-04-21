---
description: >
  Direct kdcube CLI operations — start/stop the stack, inject secrets, clean Docker, reset config,
  export live bundles. TRIGGER when: user wants to inject API keys/secrets, clean Docker, reset
  kdcube config, stop the stack with volume removal, export bundles from AWS, or asks about kdcube
  CLI flags.
  SKIP: bundle authoring, bundle reload (use kdcube-dev for those).
allowed-tools: Bash, Read
---

# KDCube CLI

Direct wrapper around the `kdcube` CLI. Full reference: https://pypi.org/project/kdcube-cli/

All commands run `kdcube [flags]`.

## Resolving the workdir

Before running any command, resolve the active workdir:

1. Check `CLAUDE_PLUGIN_OPTION_KDCUBE_WORKDIR` — use it if set.
2. Otherwise check `KDCUBE_WORKDIR` env var.
3. Otherwise look for a workdir from the project root: search for `config/.env` upward from the
   current directory and in common locations (`~/.kdcube/kdcube-runtime`).
4. Fall back to `~/.kdcube/kdcube-runtime`.

## Intent map

| User says | Command |
|---|---|
| inject secrets / set API key | see **Secrets flow** |
| clean docker / clean images | `kdcube --clean` |
| reset config | `kdcube --reset` |
| stop / remove volumes | see **Stop flow** |
| export bundles from AWS | see **Export flow** |
| start with descriptors | see **Start flow** |
| what CLI flags are there | read https://pypi.org/project/kdcube-cli/ |

## Secrets flow

Claude Code has no interactive terminal. Always use `--secrets-set` (non-interactive):

```bash
kdcube --secrets-set ANTHROPIC_API_KEY=<key> --workdir <workdir>
kdcube --secrets-set OPENAI_API_KEY=<key> --workdir <workdir>
kdcube --secrets-set GIT_HTTP_TOKEN=<token> --workdir <workdir>
```

Multiple keys in one call:
```bash
kdcube --secrets-set ANTHROPIC_API_KEY=<key> --secrets-set OPENAI_API_KEY=<key> --workdir <workdir>
```

`--secrets-prompt` requires an interactive terminal — only suggest it when the user will run
the command themselves in their own shell, never run it from Claude Code.

> After `--secrets-set` the CLI restarts `chat-proc` and `chat-ingress`. Always reload active
> bundles immediately after: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" reload <bundle-id>`

## Stop flow

Stop stack only:
```bash
kdcube --workdir <workdir> --stop
```

Stop and remove volumes (full reset — all local Postgres/Redis data will be lost):
```bash
kdcube --workdir <workdir> --stop --remove-volumes
```

## Start flow

Latest release images with descriptors:
```bash
kdcube --descriptors-location <dir> --latest
```

Specific release:
```bash
kdcube --descriptors-location <dir> --release <ref>
```

From local repo (builds locally):
```bash
kdcube --path /path/to/kdcube-ai-app
```

## Export flow

Export live `bundles.yaml` + `bundles.secrets.yaml` from AWS Secrets Manager:
```bash
kdcube --export-live-bundles \
  --tenant <tenant> --project <project> \
  --aws-region <region> \
  --out-dir /tmp/kdcube-export
```

Optional: `--aws-profile <profile>`, `--aws-sm-prefix <prefix>`.

## General rules

- If `kdcube` is not found in PATH, tell the user to install it via `pip install --user kdcube-cli` and add `~/Library/Python/3.x/bin` to PATH.
- Prefer `--secrets-prompt` over `--secrets-set` when the key is sensitive to avoid leaking it into shell history.
- After `--clean`, warn the user that the next start will re-pull or rebuild images.
- Always confirm before running `--stop --remove-volumes`.