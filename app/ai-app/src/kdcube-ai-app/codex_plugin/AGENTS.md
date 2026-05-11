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

**Never guess descriptor shapes from memory — read the docs first, every time.**
Docs are NOT on disk — fetch from GitHub (`KDCUBE_REPO_ROOT` fast path: strip
`repo:kdcube-ai-app/` and read locally).

1. `repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md` — read in full
2. Matching descriptor doc with **header-first gate** (title + first section, then full if relevant).
   Base: `repo:kdcube-ai-app/app/ai-app/docs/configuration/<filename>`:
   `assembly-descriptor-README.md`, `bundles-descriptor-README.md`,
   `bundles-secrets-descriptor-README.md`, `gateway-descriptor-README.md`, `secrets-descriptor-README.md`
3. On macOS after editing `$WORKDIR/config/bundles.yaml`: restart `chat-proc` (see Reload gotchas), then reload + verify-reload.

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
- **macOS + `bundles.yaml` edits:** Docker Desktop doesn't refresh bind mounts on inode change.
  Run `docker restart all_in_one_kdcube-chat-proc-1` after editing, then reload + verify-reload.

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

Before invoking it, resolve the workdir: check `$KDCUBE_WORKDIR`, then
`$HOME/.kdcube/kdcube-runtime`, then `kdcube_local.py status | grep Workdir`.
If still unresolved or `$WORKDIR/config/.env` is missing, ask the user in one message.

Non-negotiable rules for any bundle work:

- **Read the docs before writing or editing anything — `/kdcube-bundle-builder` owns the
  canonical read order and authoring rules.**
- Register in `$WORKDIR/config/bundles.yaml` with the **container path**
  (`/bundles/<relative-from-host_bundles_path>`), not the host path.
- If the bundle directory is outside `HOST_BUNDLES_PATH`, use
  `bootstrap <id> <dir> --host-bundles-path <parent>` to widen the mount root atomically.

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
- **`.kdcube-runtime` is read-only — never use file-edit tools or shell writes on any
  file inside `$WORKDIR`.** You may read files there to inspect current state (e.g. check
  `bundles.yaml` or `assembly.yaml`). All mutations — descriptor edits, config changes,
  secrets — must go through the `kdcube` CLI or the `kdcube_local.py` helper script.
  Bundle source files that live outside `$WORKDIR` (e.g. `~/.kdcube/bundles/<id>/` or a
  user-specified directory) are editable as normal.