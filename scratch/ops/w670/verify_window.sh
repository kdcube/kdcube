#!/usr/bin/env bash
# W670 post-window verification, READ-ONLY. Prints PASS/FAIL/WARN/SKIP per check with evidence.
# It never prints a secret value, its length or a digest: only key names, counts, modes and booleans.
#
# Usage (on the dev-main host, after the window):
#   CONFIG_DIR=<workdir>/config \
#   OAUTH_METADATA_URL='https://<host>/.../connection-hub@1-0/public/oauth/.well-known/oauth-authorization-server?resource=<...problem_board>' \
#   EXPECTED_ISSUER='https://<host>/api/integrations/bundles/<tenant>/<project>/connection-hub@1-0/public/oauth' \
#   WINDOW_START='2026-10-09T18:15:00Z' \
#   scratch/ops/w670/verify_window.sh
# Optional: CHAT_PROC (default custom-ui-managed-infra-chat-proc-1), COMPOSE_PREFIX (default custom-ui-managed-infra),
#   EXPECTED_USER_SECRETS (default "connection-hub@1-0=5,task-and-memo-app@1-0=2"), CONTAINER_CONFIG_DIR (default /config),
#   CONTAINER_PYTHON (default python).
set -u
CONFIG_DIR="${CONFIG_DIR:?set CONFIG_DIR to the host config folder}"
CHAT_PROC="${CHAT_PROC:-custom-ui-managed-infra-chat-proc-1}"
COMPOSE_PREFIX="${COMPOSE_PREFIX:-custom-ui-managed-infra}"
EXPECTED_USER_SECRETS="${EXPECTED_USER_SECRETS:-connection-hub@1-0=5,task-and-memo-app@1-0=2}"
CONTAINER_CONFIG_DIR="${CONTAINER_CONFIG_DIR:-/config}"
CONTAINER_PYTHON="${CONTAINER_PYTHON:-python}"
WINDOW_START="${WINDOW_START:-}"
OAUTH_METADATA_URL="${OAUTH_METADATA_URL:-}"
EXPECTED_ISSUER="${EXPECTED_ISSUER:-}"
FAILED=0
say() { printf '%-5s %-3s %s\n' "$1" "$2" "$3"; [ "$1" = FAIL ] && FAILED=1; return 0; }
since() { [ -n "$WINDOW_START" ] && printf -- '--since %s' "$WINDOW_START"; }

# a. kdcube up + OAuth metadata
unhealthy=$(docker ps --filter "name=${COMPOSE_PREFIX}" --format '{{.Names}} {{.Status}}' | grep -Ev '\(healthy\)|^[^ ]+ Up [^(]*$' || true)
running=$(docker ps --filter "name=${COMPOSE_PREFIX}" --format '{{.Names}}' | wc -l)
if [ "$running" -gt 0 ] && [ -z "$unhealthy" ]; then say PASS a "containers up: ${running}, none unhealthy"
else say FAIL a "containers up: ${running}; not healthy: ${unhealthy:-none listed}"; fi
if [ -n "$OAUTH_METADATA_URL" ]; then
  body=$(curl -sS -o /tmp/w670_meta.$$ -w '%{http_code}' "$OAUTH_METADATA_URL" 2>/dev/null || echo 000)
  issuer=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("issuer",""))' /tmp/w670_meta.$$ 2>/dev/null || true)
  rm -f /tmp/w670_meta.$$
  if [ "$body" = 200 ] && { [ -z "$EXPECTED_ISSUER" ] || [ "${issuer%/}" = "${EXPECTED_ISSUER%/}" ]; }; then
    say PASS a "OAuth metadata 200, issuer=${issuer}"
  else say FAIL a "OAuth metadata status=${body}, issuer=${issuer:-none}, expected=${EXPECTED_ISSUER:-any}"; fi
else say SKIP a "OAUTH_METADATA_URL not set"; fi

# b. card_transactions.enabled in the live bundles.yaml
ct=$(python3 - "$CONFIG_DIR/bundles.yaml" <<'PY' 2>/dev/null
import sys, yaml
data = yaml.safe_load(open(sys.argv[1])) or {}
items = ((data.get("bundles") or {}).get("items")) or []
hub = next((i for i in items if isinstance(i, dict) and str(i.get("id", "")).startswith("connection-hub@")), {})
props = hub.get("config") or hub.get("props") or {}
print(((props.get("connections") or {}).get("card_transactions") or {}).get("enabled"))
PY
)
[ "$ct" = True ] && say PASS b "connection-hub card_transactions.enabled=True" || say FAIL b "card_transactions.enabled=${ct:-unreadable}"

# c. agents connected; no withheld/failed refresh since the window
if command -v pb >/dev/null 2>&1; then
  if pb status 2>/dev/null | grep -q session_attending; then say PASS c "pb status: session_attending"; else say FAIL c "pb status: not session_attending"; fi
else say SKIP c "pb not on PATH"; fi
if logs=$(docker logs $(since) "$CHAT_PROC" 2>&1); then LOGS_OK=1; else LOGS_OK=0; fi
[ "$LOGS_OK" = 1 ] || say FAIL c "cannot read logs of ${CHAT_PROC}"
withheld=$(printf '%s\n' "$logs" | grep -c 'token withheld' || true)
failed=$(printf '%s\n' "$logs" | grep -c 'refresh issuance failed' || true)
ok=$(printf '%s\n' "$logs" | grep -Ec 'POST [^ ]*oauth/token[^"]*" 200|oauth/token.* 200 ' || true)
[ "$LOGS_OK" = 1 ] && [ "$withheld" -eq 0 ] && [ "$failed" -eq 0 ] && say PASS c "since ${WINDOW_START:-container start}: token withheld=0, refresh issuance failed=0" \
  || say FAIL c "since ${WINDOW_START:-container start}: token withheld=${withheld}, refresh issuance failed=${failed}"
[ "$ok" -gt 0 ] && say PASS c "successful oauth/token responses: ${ok}" \
  || say WARN c "no 200 oauth/token access-log line found (refresh success is not logged by the route; check access logging)"

# d. folder layout, counts and modes only
python3 - "$CONFIG_DIR" "$EXPECTED_USER_SECRETS" <<'PY'
import os, stat, sys, yaml
config, expected = sys.argv[1], sys.argv[2]
root = os.path.join(config, "secrets")
def say(level, text): print(f"{level:<5} d   {text}")
if not os.path.isdir(root):
    say("FAIL", f"no secrets folder at {root}"); sys.exit(0)
bad_modes, app_counts, user_counts = [], {}, {}
for bundle in sorted(os.listdir(root)):
    bpath = os.path.join(root, bundle)
    if not os.path.isdir(bpath) or bundle == "platform":
        continue
    for dirpath, dirnames, filenames in os.walk(bpath):
        mode = stat.S_IMODE(os.lstat(dirpath).st_mode)
        if mode != 0o700: bad_modes.append(f"dir {os.path.relpath(dirpath, root)} {oct(mode)}")
        rel = os.path.relpath(dirpath, bpath).split(os.sep)
        for name in filenames:
            if not name.endswith(".json"): continue
            fmode = stat.S_IMODE(os.lstat(os.path.join(dirpath, name)).st_mode)
            if fmode != 0o600: bad_modes.append(f"file {os.path.relpath(os.path.join(dirpath, name), root)} {oct(fmode)}")
            if rel[0] == "users": user_counts[bundle] = user_counts.get(bundle, 0) + 1
            elif rel == ["."]: app_counts[bundle] = app_counts.get(bundle, 0) + 1
say("PASS" if not bad_modes else "FAIL", "modes: all dirs 0700, files 0600" if not bad_modes else "bad modes: " + "; ".join(bad_modes[:10]))
say("INFO", "app secret files per bundle: " + (", ".join(f"{b}={n}" for b, n in sorted(app_counts.items())) or "none"))
want = dict(pair.split("=", 1) for pair in expected.split(",") if "=" in pair)
got = {b: str(n) for b, n in user_counts.items()}
say("PASS" if got == want else "FAIL", f"user secret files per bundle: {got or '{}'} (expected {want})")
try:
    items = ((yaml.safe_load(open(os.path.join(config, "bundles.secrets.yaml"))) or {}).get("bundles") or {}).get("items") or []
    left = [str(i.get("id")) for i in items if isinstance(i, dict) and i.get("secrets")]
    say("PASS" if not left else "FAIL", "bundles.secrets.yaml: no remaining secrets blocks" if not left else f"secrets blocks remain for: {left}")
except FileNotFoundError:
    say("INFO", "bundles.secrets.yaml absent")
glob = yaml.safe_load(open(os.path.join(config, "secrets.yaml"))) or {}
top = sorted((glob.get("secrets") if isinstance(glob.get("secrets"), dict) else glob).keys())
say("PASS" if top in (["platform"], []) else "FAIL", f"secrets.yaml top-level keys: {top}")
PY

# e. read-back inside chat-proc through the SDK get_secret with settings (resolved yes/no only)
in_proc() {  # $1 = check letter; stdin = python; a missing PASS line is a FAIL
  local out; out=$(docker exec -i "$CHAT_PROC" "$CONTAINER_PYTHON" - 2>&1)
  printf '%s\n' "$out" | grep -E "^(PASS|FAIL) " || true
  printf '%s\n' "$out" | grep -qE "^FAIL " && FAILED=1
  printf '%s\n' "$out" | grep -qE "^PASS " || say FAIL "$1" "could not run inside ${CHAT_PROC} (exec or import failed)"
  return 0
}
in_proc e <<'PY'
import asyncio
from kdcube_ai_app.apps.chat.sdk.config import get_secret, get_settings
from kdcube_ai_app.infra.secrets.manager import SecretsFileSecretsManager, get_secrets_manager
manager = get_secrets_manager(get_settings())
if not isinstance(manager, SecretsFileSecretsManager):
    print("FAIL  e   the configured manager is not secrets-file"); raise SystemExit(0)
store = manager._user_store()
keys = sorted(set(store.list_app_keys()) | set(store.list_keys()))
async def main():
    missing = []
    for key in keys:
        if not await get_secret(key):
            missing.append(key)
    print(("PASS" if not missing else "FAIL") + f"  e   resolved {len(keys) - len(missing)}/{len(keys)} folder keys via get_secret"
          + ("" if not missing else "; unresolved: " + ", ".join(missing)))
asyncio.run(main())
PY

# f. PB secret read log
pbl=$( [ "$LOGS_OK" = 1 ] && printf '%s\n' "$logs" | grep -c '\[problem-board.secrets\].*provider=secrets-file outcome=resolved' || true)
[ -n "$pbl" ] || pbl=0
[ "$pbl" -gt 0 ] && say PASS f "[problem-board.secrets] resolved lines: ${pbl}" \
  || say WARN f "no [problem-board.secrets] resolved line yet (appears once a Card transaction runs)"

# g. W675/W670 sandbox payload: what an isolated run would receive (no sandbox is started)
in_proc g <<'PY'
import base64, json
from kdcube_ai_app.infra.config import platform_env
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.secrets.manager import get_secrets_manager
store = get_secrets_manager(get_settings())._user_store()
expected = len(set(store.list_app_keys()) | set(store.list_keys()))
raw = platform_env._secret_records_payload(
    {"KDCUBE_RUNTIME_SECRETS_YAML_B64": "x", "KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64": "x"},
    bundle_id=None, descriptor_payload_scope=None)
carried = 0 if raw is None else len(json.loads(base64.b64decode(raw))["records"])
print(("PASS" if carried == expected and expected > 0 else "FAIL") + f"  g   unscoped sandbox payload carries {carried}/{expected} folder secrets")
PY

[ "$FAILED" -eq 0 ] && echo "RESULT: no FAIL" || echo "RESULT: at least one FAIL"
exit "$FAILED"
