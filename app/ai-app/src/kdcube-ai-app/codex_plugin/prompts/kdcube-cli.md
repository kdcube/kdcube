# /kdcube-cli

Direct `kdcube` CLI operations — inject secrets, clean Docker, reset config, stop with
volume removal, export live bundles from AWS.

Take the intent from the text after `/kdcube-cli` and map it to the right command below.

Full CLI reference: https://pypi.org/project/kdcube-cli/

## Resolving the workdir

Before running any command that needs `--workdir`:

1. `KDCUBE_WORKDIR` env var — use it if set.
2. Look for `config/.env` upward from CWD and in `~/.kdcube/kdcube-runtime`.
3. Fall back to `~/.kdcube/kdcube-runtime`.

## Intent map

| User says | Action |
|---|---|
| inject secrets / set API key | **Secrets flow** |
| clean docker / clean images | `kdcube --clean` |
| reset config | `kdcube --reset` |
| stop / remove volumes | **Stop flow** |
| export bundles from AWS | **Export flow** |
| start with descriptors | **Start flow** |
| what CLI flags are there | read https://pypi.org/project/kdcube-cli/ |

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
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" reload <bundle-id>
```

## Stop flow

Stop stack only:

```bash
kdcube --workdir <workdir> --stop
```

Stop and remove volumes (full reset — all local Postgres/Redis data will be lost):

```bash
kdcube --workdir <workdir> --stop --remove-volumes
```

Always confirm with the user before running `--stop --remove-volumes`.

## Start flow

Latest release images:

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

- If `kdcube` is not found in PATH, tell the user to run `pip install --user kdcube-cli`
  and add `~/Library/Python/3.x/bin` to PATH.
- After `--clean`, warn the user that the next start will re-pull or rebuild images.
- Always confirm before running `--stop --remove-volumes`.