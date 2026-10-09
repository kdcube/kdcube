# W670 post-window verification (read-only)

`verify_window.sh` checks the dev-main host after the W670 window. It changes nothing. Every check prints `PASS`, `FAIL`, `WARN`, `SKIP` or `INFO` with evidence. It never prints a secret value, its length or a digest: only key names, counts, file modes and booleans. The exit code is non-zero if any check fails.

## Run

```bash
CONFIG_DIR=<workdir>/config \
OAUTH_METADATA_URL='https://<host>/api/integrations/bundles/<tenant>/<project>/connection-hub@1-0/public/oauth/.well-known/oauth-authorization-server?resource=<...problem_board>' \
EXPECTED_ISSUER='https://<host>/api/integrations/bundles/<tenant>/<project>/connection-hub@1-0/public/oauth' \
WINDOW_START='2026-10-09T18:15:00Z' \
scratch/ops/w670/verify_window.sh
```

Optional settings:
- `CHAT_PROC`: default `custom-ui-managed-infra-chat-proc-1`.
- `COMPOSE_PREFIX`: default `custom-ui-managed-infra`.
- `EXPECTED_USER_SECRETS`: default `connection-hub@1-0=5,task-and-memo-app@1-0=2`.
- `CONTAINER_PYTHON`: default `python`.

## Checks

| | What | How |
|---|---|---|
| a | kdcube is up; OAuth metadata 200 with the configured issuer | `docker ps` health for `COMPOSE_PREFIX`; `curl OAUTH_METADATA_URL`, comparing `issuer` with `EXPECTED_ISSUER` |
| b | Card transactions are on | live `bundles.yaml`: connection-hub `config.connections.card_transactions.enabled == true` |
| c | Agents stay connected | `pb status` shows `session_attending`; chat-proc logs since `WINDOW_START` contain no `token withheld` and no `refresh issuance failed`; at least one `oauth/token` 200 in the access log (WARN if there is none: the route logs no success line) |
| d | Folder layout | `config/secrets/<bundle>/` with app files and `users/<user>/` files, counted per bundle against `EXPECTED_USER_SECRETS`; every dir 0700 and file 0600; `bundles.secrets.yaml` has no `secrets` blocks; `secrets.yaml` keeps only `platform` |
| e | Read-back | inside chat-proc, the SDK `get_secret` (with settings) resolves every folder key: "resolved N/M" plus the names of unresolved keys |
| f | PB secret read | chat-proc logs contain `[problem-board.secrets] ... provider=secrets-file outcome=resolved`. WARN until a Card transaction has run |
| g | Sandbox payload | inside chat-proc, `platform_env._secret_records_payload` (unscoped, both yaml copies) carries every folder secret: a count. No sandbox is started |

## Assumptions
- Checks (e) and (g) need kdcube main with #340 merged: the folder store's `list_app_keys` and `list_keys`, and `_secret_records_payload`. On an older image they print `FAIL ... could not run`.
- Check (c) reads `docker logs`, so `WINDOW_START` must be a time docker accepts.
- The script was dry-run here against a synthetic config folder (checks b and d), and with a missing container (a, c, e and g correctly FAIL). It has not been run on dev-main.
