# KDCube development

This project uses the KDCube runtime. You (the assistant) are the main entry point for all
KDCube development work: starting the runtime, reloading bundles, testing, and authoring
or repairing bundle code.

All helper commands run through one script:

```bash
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" <command> [args]
```

Understand the user's intent and drive the right sequence of actions without asking them
to type slash commands. If an action has a matching `/kdcube-*` slash command, you can
invoke it directly or just run the equivalent shell command yourself.

## Intent map

| User says (any language) | Action |
|---|---|
| start / run / запусти / launch | `start latest-image` |
| stop / останови / kill | `stop` |
| reload / restart / перезагрузи `<bundle>` | **Reload flow** (below) |
| test bundle / протестируй / does it work | **Test flow** |
| build / create / fix bundle / создай бандл | **Bundle build flow** → `/kdcube-bundle-builder` |
| wrap app / заверни приложение в бандл | **Bundle build flow** → wrap workflow |
| add feature / добавь фичу в бандл | **Bundle build flow** → add feature workflow |
| setup / first time / настрой / where are descriptors | **First-time setup flow** |
| configure / настрой конфиг / how do I edit bundles.yaml / assembly.yaml | **Configuration flow** |
| status / what's running / что запущено | `status` |
| inject secrets / добавь ключи / clean / reset config | `/kdcube-cli` |
| test UI in browser | `/kdcube-ui-test` |

> **Note:** the `kdcube` CLI is installed separately. If `kdcube` is not found in PATH,
> tell the user to run `pip install --user kdcube-cli` and stop.

## First-time setup flow

Run `status` first to check current state.

**Case A — descriptors already exist on disk** (user says "I have descriptors at X"):
1. Run the helper with `use-descriptors <path>`.
2. Offer to start immediately.

**Case B — no descriptors yet** — ask in one message:
- "What is your bundle id?" (e.g. `telegram-bot`)
- "What is the absolute path to your bundle directory?" (e.g. `/Users/you/projects/telegram-bot`)

Then run:
```bash
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" bootstrap <bundle_id> <bundle_path>
```

Generates all descriptors with safe local defaults (auth=simple, demo tenant/project).
After success, offer to start.

## Configuration flow

When the user asks about editing descriptors, configuring a bundle, what a field in
`assembly.yaml` / `bundles.yaml` / `bundles.secrets.yaml` / `gateway.yaml` / `secrets.yaml`
means, or how props/secrets reach a bundle — **do not guess from memory. Ever. Read the
docs first, every single time, no exceptions.**

This rule is absolute. The most common failure mode here is skipping the read step on
"small" edits and ending up with a `bundles.yaml` entry that uses the host path instead
of the container path, or an `assembly.yaml` whose `host_bundles_path` does not actually
cover the bundle directory on disk. Both look fine until the runtime silently serves
nothing. Re-read even if you think you remember from a previous session — descriptor
shapes change between releases.

**Especially when the bundle lives outside the current runtime workdir / outside
`host_bundles_path`:** the host path is NOT the container path. `bundles.yaml` takes the
container path `/bundles/<relative-from-host_bundles_path>`. The documented fix is to
edit `assembly.yaml -> paths.host_bundles_path` to the parent that contains the bundle,
then `kdcube --workdir $WORKDIR --build --upstream`. The plugin's `bootstrap` helper with
`--host-bundles-path` does the same thing in one call.

Docs are NOT on disk — always fetch from GitHub. Only fall back to local `Read` if
`KDCUBE_REPO_ROOT` is already set; if it is, strip the
`https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/` prefix and read the
repo-relative path. Do not ask the user for a local repo.

1. Fetch the how-to first and read it in full:
   `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md`
2. Fetch the matching descriptor doc — **header-first gate:** read only the title and
   first section first, confirm it covers the specific field you need, then read the rest.
   Base URL `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/configuration/<filename>`:
   `assembly-descriptor-README.md`, `bundles-descriptor-README.md`,
   `bundles-secrets-descriptor-README.md`, `gateway-descriptor-README.md`,
   `secrets-descriptor-README.md`.
3. After editing `$WORKDIR/config/bundles.yaml` on macOS, restart `chat-proc` (see
   **Reload gotchas** below), then reload + verify-reload.

## Reload flow

1. Extract `bundle_id` from the message. If missing, ask.
2. Run:
   ```bash
   python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" reload <bundle_id>
   ```
3. **Immediately** run:
   ```bash
   python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" verify-reload <bundle_id>
   ```
   — the reload call returns before the proc cache actually rotates, so verify is the
   only way to know the new code is live.
4. Report combined result: reloaded + confirmed, or show the error.

### Reload gotchas — check these when something looks wrong

- Editing files in the bundle dir does not hot-reload. Without an explicit `reload`, the
  runtime keeps serving the cached version.
- `reload` is a no-op if the `bundle_id` isn't registered in `bundles.yaml`, or if the
  `path` in `bundles.yaml` isn't the **container path** (`/bundles/<bundle-id>`).
- `verify-reload` showing `eviction: None` for a previously-active bundle is a red flag —
  the bundle was never in the proc cache. Recheck the `id` and `path` in `bundles.yaml`.
- Any container restart (secrets injection, `stop`/`start`, Docker restart) drops the
  proc cache — reload every active bundle immediately after.
- **macOS Docker Desktop + edits to `bundles.yaml`:** Docker Desktop on macOS does not
  refresh a file-level bind mount when the host file's inode changes, and file edits
  replace inodes. After editing `$WORKDIR/config/bundles.yaml` the container still reads
  the old file until you restart `chat-proc`:
  ```bash
  docker restart all_in_one_kdcube-chat-proc-1
  ```
  Then reload + verify-reload.

## Test flow

- Shared bundle suite:
  ```bash
  python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" bundle-tests <bundle_path>
  ```
  (Requires `KDCUBE_REPO_ROOT` to point at a local `kdcube-ai-app` clone.)
- Verify last reload: `verify-reload <bundle_id>`.
- End-to-end UI: `/kdcube-ui-test`.

## Bundle build flow

**Delegate bundle authoring to `/kdcube-bundle-builder`.** That prompt owns the canonical
read order, placement rules, and authoring rules.

Before invoking it, resolve the workdir:

```bash
WORKDIR="${KDCUBE_WORKDIR:-}"
if [ -z "$WORKDIR" ] && [ -d "$HOME/.kdcube/kdcube-runtime/config" ]; then
  WORKDIR="$HOME/.kdcube/kdcube-runtime"
fi
if [ -z "$WORKDIR" ]; then
  WORKDIR=$(python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" status 2>/dev/null \
    | awk -F': +' '/^Workdir/ {print $2}' | awk '{print $1}')
fi
```

If `$WORKDIR` still resolves to nothing or `$WORKDIR/config/.env` is missing, **ask the
user** for the workdir in one short message — do not guess.

Non-negotiable rules for any bundle work:

- **Read the docs before writing or editing anything — every time, no exceptions.** Start
  with `how-to-write-bundle-README.md` and `how-to-configure-and-run-bundle-README.md`,
  plus the versatile reference bundle. The descriptor shapes and mount semantics change
  between releases and the runtime fails silently when you get them wrong.
- The bundle directory can live anywhere on the host — `~/.kdcube/bundles/<bundle-id>/`
  by default, or wherever the user asked. Use a real directory, not a symlink.
- Register in `$WORKDIR/config/bundles.yaml` with the **container path**
  (`/bundles/<relative-from-host_bundles_path>`), not the host path.
- If the chosen host directory is outside the current `HOST_BUNDLES_PATH`, widen
  `host_bundles_path` in `assembly.yaml` and rebuild with
  `kdcube --workdir $WORKDIR --build --upstream`. The `bootstrap <bundle-id> <bundle-dir>
  --host-bundles-path <parent>` helper does the same thing in one call.
- Do not invent decorators, import paths, or descriptor fields.

## General rules

- Never ask the user to type a slash command — do everything via shell invocations yourself.
- If the runtime is not running when reload/test is requested, offer to start it first.
- If no descriptor profile is linked, run the first-time setup flow before anything else.
- One short status line before each action.
- On error: show raw output, suggest the most likely fix, do not silently retry.
- **After any container restart** — always reload active bundles immediately. Check
  `bundles.yaml` `default_bundle_id` and reload it.
- **Always pair `reload` with `verify-reload`**.
- **`--secrets-prompt` is interactive** — never run it non-interactively. Use
  `--secrets-set` instead.