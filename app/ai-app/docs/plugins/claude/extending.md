# Extending the Plugin

## Where logic lives

- **A skill is a prompt, not code.** All real logic lives in
  `plugins/kdcube-builder/scripts/kdcube_local.py`.
- To add a new operation: add a subcommand in `build_parser()` and a `cmd_*`
  function, then extend the `kdcube-dev` intent map so the orchestrator knows
  when to invoke it. Add a dedicated SKILL.md wrapper only if you want a
  standalone slash-command entry point — usually the intent-map entry is enough.
- To change *how* Claude handles an existing operation, edit the SKILL.md
  frontmatter/instructions — no code change needed.

## Adding a skill

Minimum viable skill:

```markdown
---
description: >
  One-line description. TRIGGER when: <cases>. SKIP: <cases>.
argument-hint: "<args>"
disable-model-invocation: true    # only reachable via /slash or dispatcher
allowed-tools: Bash, Read
---

# Skill title

Run:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py <subcommand> $ARGUMENTS

Operational rules:
- …
```

`disable-model-invocation: true` is the default for specialized sub-skills —
they should only be reached through `kdcube-dev` or an explicit slash command,
not auto-invoked on ambiguous user messages.

## Gotchas

- **`--secrets-prompt` is interactive** — never run it from Claude Code. The
  `kdcube-cli` skill always uses `--secrets-set`.
- **macOS `bundles.yaml` inode bug** — see
  [runtime-flows.md](./runtime-flows.md). This is the only place in the plugin
  flow where a raw `docker restart` is acceptable.
- **Tilde expansion in YAML** — `cmd_start` copies descriptors into a temp dir
  and expands `~/` / `$HOME/` before invoking `kdcube`. Keep this behavior when
  adding new start modes; the CLI does not expand these itself.
- **`bundle-tests` needs the repo root** — the shared suite is imported from
  `kdcube_ai_app.apps.chat.sdk.tests.bundle`, so `kdcube_repo_root` must be
  configured. Handle the "not set" case with a clear error, not silent
  fallback.
- **`verify-reload` depends on one container name pattern** (`chat-proc`).
  `_docker_container_name` errors if zero or >1 containers match — keep that
  explicit if you add new internal endpoints.
- **Version pinning in three places.** `CURRENT_RELEASE` in `kdcube_local.py`,
  `marketplace.json`, and `plugin.json` must be bumped together.
- **Don't add destructive ops as plugin subcommands.** Destructive flows
  (`--stop --remove-volumes`, `--clean`) stay in the `kdcube-cli` skill with
  explicit user confirmation, not in `kdcube_local.py`.

## Validating changes

```bash
claude plugin validate app/ai-app/src/kdcube-ai-app/builder_plugin
claude --plugin-dir app/ai-app/src/kdcube-ai-app/builder_plugin/plugins/kdcube-builder
```

The second form loads the plugin directly without going through the
marketplace — convenient while iterating.