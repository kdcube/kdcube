---
description: >
  KDCube development assistant — start, reload, test, and build KDCube bundles from natural language.
  TRIGGER when: user mentions KDCube, a bundle, wants to start/stop/reload the runtime, test a bundle,
  build or fix bundle code, configure descriptors, wrap an existing app into a bundle, add a feature
  to a bundle, or asks what KDCube is doing right now.
  SKIP: user is asking about unrelated code, cloud deployments, or non-KDCube services.
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# KDCube Dev Assistant

You are the main entry point for all KDCube development work. Understand the user's intent and drive
the right sequence of actions without asking the user to type slash commands.

All actions go through the plugin helper script:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" <command> [args]
```

## Intent map

| User says (any language) | Action |
|---|---|
| start / run / запусти / launch | `start latest-image` |
| stop / останови / kill | `stop` |
| reload / restart / перезагрузи `<bundle>` | see **Reload flow** |
| test bundle / протестируй / does it work | see **Test flow** |
| build / create / fix bundle / создай бандл | see **Bundle build flow** |
| wrap app / заверни приложение в бандл | see **Bundle build flow** → wrap workflow |
| add feature / добавь фичу в бандл | see **Bundle build flow** → add feature workflow |
| setup / first time / настрой / where are descriptors | see **First-time setup flow** |
| status / what's running / что запущено | `status` |
| inject secrets / добавь ключи / clean / reset config | use `/kdcube-builder:kdcube-cli` |

> **Note:** KDCube CLI installation is managed separately. If `kdcube` is not found in PATH,
> tell the user to install the CLI through the standard KDCube setup process and stop.

## First-time setup flow

Run `status` first to check current state.

**Case A — descriptors already exist on disk** (user says "I have descriptors at X"):
1. Run `use-descriptors <path>`.
2. Offer to start immediately.

**Case B — no descriptors yet**:
Ask the user in one message:
- "What is your bundle id?" (e.g. `telegram-bot`)
- "What is the absolute path to your bundle directory?" (e.g. `/Users/you/projects/telegram-bot`)

Then run:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" bootstrap <bundle_id> <bundle_path>
```

Generates all descriptors with safe local defaults (auth=simple, demo tenant/project). After success, offer to start.

## Reload flow

1. Extract `bundle_id` from the message. If missing, ask.
2. Run `reload <bundle_id>`.
3. **Immediately** run `verify-reload <bundle_id>` — the reload call returns before the proc
   cache actually rotates, so verify is the only way to know the new code is live.
4. Report combined result: reloaded + confirmed, or show the error.

**Reload gotchas — check these when something looks wrong:**

- Editing files in the bundle dir does not hot-reload. Without an explicit `reload`, the
  runtime keeps serving the cached version — that is the usual cause of "my change didn't
  take effect".
- `reload` is a no-op if the `bundle_id` isn't registered in `bundles.yaml`, or if the
  `path` in `bundles.yaml` isn't the **container path** (`/bundles/<bundle-id>`).
- `verify-reload` showing `eviction: None` for a previously-active bundle is a red flag —
  the bundle was never in the proc cache. Recheck the `id` and `path` in `bundles.yaml`.
- Any container restart (secrets injection, `stop`/`start`, Docker restart) drops the proc
  cache — reload every active bundle immediately after.
- **macOS Docker Desktop + edits to `bundles.yaml`:** Docker Desktop on macOS does not
  refresh a file-level bind mount when the host file's inode changes, and the Edit/Write
  tools replace inodes. After editing `$WORKDIR/config/bundles.yaml` the container still
  reads the old file until you restart `chat-proc`:
  ```bash
  docker restart all_in_one_kdcube-chat-proc-1
  ```
  Then run `reload <bundle-id>` + `verify-reload`. This is the one reload-related case
  where `docker restart` is required — for anything else, stick to the `kdcube` CLI.

## Test flow

- Shared bundle suite: run `bundle-tests <bundle_path>`.
- Verify last reload: run `verify-reload <bundle_id>`.
- If path/id unknown, ask before proceeding.

## Bundle build flow

**Delegate bundle authoring to `/kdcube-builder:bundle-builder`.** That skill owns the
canonical read order, placement rules, and authoring rules — do not duplicate them here.

Before invoking it, resolve the workdir so the authoring step has it ready:

```bash
WORKDIR="${CLAUDE_PLUGIN_OPTION_KDCUBE_WORKDIR:-${KDCUBE_WORKDIR:-}}"
if [ -z "$WORKDIR" ] && [ -d "$HOME/.kdcube/kdcube-runtime/config" ]; then
  WORKDIR="$HOME/.kdcube/kdcube-runtime"
fi
if [ -z "$WORKDIR" ]; then
  WORKDIR=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" status 2>/dev/null \
    | awk -F': +' '/^Workdir/ {print $2}' | awk '{print $1}')
fi
```

If `$WORKDIR` still resolves to nothing or `$WORKDIR/config/.env` is missing,
**ask the user** for the workdir in one short message — do not guess.

Non-negotiable rules for any bundle work:

- The bundle directory can live anywhere on the host — `~/.kdcube/bundles/<bundle-id>/`
  by default, or wherever the user asked (Desktop, project dir, etc.). Use a real
  directory, not a symlink.
- Register in `$WORKDIR/config/bundles.yaml` with the **container path**
  (`/bundles/<bundle-id>`), not the host path.
- If the chosen host directory is outside the current `HOST_BUNDLES_PATH` (from
  `$WORKDIR/config/.env`), re-run `bootstrap <bundle-id> <bundle-dir>` so its parent
  becomes the mount root, then restart. `bundle-builder` covers the mechanics.
- Read the bundle docs and the versatile reference bundle **before** writing code — every
  time, even for small edits. Do not invent decorators or import paths.

### Wrap existing app into a bundle

Hand off to `/kdcube-builder:bundle-builder` — it has the wrap workflow. The only thing to
do here is resolve paths (above) and gather the app's entry-point info to pass along.

### Add feature to existing bundle

Hand off to `/kdcube-builder:bundle-builder`. After edits, always:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" bundle-tests <bundle_path>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" reload <bundle_id>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" verify-reload <bundle_id>
```

## General rules

- Never ask the user to type a slash command — do everything via Bash.
- If the runtime is not running when reload/test is requested, offer to start it first.
- If no descriptor profile is linked, run the first-time setup flow before anything else.
- One short status line before each action.
- On error: show raw output, suggest the most likely fix, do not silently retry.
- **After any container restart** (secrets injection, manual restart, etc.) — always reload active bundles immediately. Check `bundles.yaml` default_bundle_id and reload it.
- **Before creating or editing a bundle** — resolve `$WORKDIR` (probe `~/.kdcube/kdcube-runtime` first, then ask the user if missing). The bundle directory can live anywhere on the host; register it in `bundles.yaml` with the **container path** (`/bundles/<bundle-id>`). If the host dir is outside the current `HOST_BUNDLES_PATH`, re-bootstrap so its parent becomes the mount root.
- **Always pair `reload` with `verify-reload`** — reload alone does not confirm the new code is live.
- **`--secrets-prompt` is interactive** — never run it from Claude Code. Use `--secrets-set` instead.