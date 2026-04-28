# /kdcube-dev

Drive a KDCube development action end-to-end from the free-form task the user typed
after `/kdcube-dev`.

Act as the main KDCube orchestrator. The detailed intent map, flows, and rules are in
`~/.codex/AGENTS.md` under the "KDCube development" section. Re-read that section before
acting.

Key points:

- All helper commands go through:
  ```bash
  python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" <command> [args]
  ```
- Never ask the user to type another slash command — do everything via shell yourself.
- Resolve `$WORKDIR` (`KDCUBE_WORKDIR` → `~/.kdcube/kdcube-runtime` → `status` output →
  ask the user) before any action that needs it.
- Always pair `reload` with `verify-reload`.
- Docs are NOT on disk — fetch from GitHub; only fall back to local `Read` if
  `KDCUBE_REPO_ROOT` is set.
- For bundle authoring specifically, hand off to `/kdcube-bundle-builder`.
- **`.kdcube-runtime` is read-only.** You may read files under `$WORKDIR` to inspect
  current state, but must never write or edit them. All runtime config mutations —
  descriptors, config files, secrets — must go through the helper script or the `kdcube`
  CLI. Bundle source files outside `$WORKDIR` are editable as normal.

Start with one short status line telling the user what you're doing, then execute.