# Codex CLI Extension

Source: `app/ai-app/src/kdcube-ai-app/codex_plugin`.

Port of the `kdcube-builder` Claude Code plugin for
[OpenAI Codex CLI](https://github.com/openai/codex). Codex has no plugin or
marketplace system, so the extension ships as a set of files installed into
`~/.codex/`:

- `AGENTS.md` — a user-level rule block, always loaded into every Codex session,
  that routes natural-language KDCube requests to the right action. The orchestrator
  is equivalent to the `kdcube-dev` skill in the Claude Code plugin.
- `prompts/kdcube-*.md` — eight slash commands for explicit invocation.
- `kdcube-builder/` — the shared `kdcube_local.py` helper + YAML descriptor
  templates, copied verbatim from the sibling `builder_plugin/`.

## Differences from the Claude Code plugin

| Dimension          | Claude Code plugin                       | Codex extension                          |
|--------------------|------------------------------------------|------------------------------------------|
| Distribution       | Marketplace (`claude plugin install`)    | Shell script (`./install.sh`)            |
| Auto-invocation    | `description:` field in SKILL.md        | `AGENTS.md` always-in-context block      |
| Slash commands     | `/kdcube-builder:<skill>`               | `/kdcube-<name>` prompts                 |
| Config             | `userConfig` + env vars                 | Env vars only                            |
| Helper script path | `$CLAUDE_PLUGIN_ROOT/scripts/kdcube_local.py` | `$KDCUBE_BUILDER_ROOT/kdcube_local.py` |
| Default BUILDER_ROOT | N/A                                  | `~/.codex/kdcube-builder`               |

Capabilities are identical: same CLI subcommands, same bundle authoring rules,
same Tier 1/Tier 2 doc split, same reload + verify cycle.

## Prompt files

| Prompt file                  | Equivalent Claude skill         | Purpose                                  |
|------------------------------|---------------------------------|------------------------------------------|
| `kdcube-dev.md`              | `kdcube-dev`                    | Main orchestrator — intent routing       |
| `kdcube-bundle-builder.md`   | `bundle-builder`                | Bundle authoring (Tier 1/2 gate)         |
| `kdcube-bootstrap.md`        | `bootstrap-local`               | Generate fresh descriptor set            |
| `kdcube-use-descriptors.md`  | `use-descriptors`               | Symlink an existing descriptor directory |
| `kdcube-runtime.md`          | `local-runtime`                 | start / stop / reload / bundle-tests     |
| `kdcube-verify-reload.md`    | `verify-reload`                 | Confirm proc cache eviction after reload |
| `kdcube-cli.md`              | `kdcube-cli`                    | Secrets injection, clean, export         |
| `kdcube-ui-test.md`          | `kdcube-ui-test`                | Browser-test the chat UI via Playwright  |

## Configuration

All configuration via env vars:

| Variable              | Purpose                                              | Default                        |
|-----------------------|------------------------------------------------------|--------------------------------|
| `KDCUBE_BUILDER_ROOT` | Where install.sh put the runtime + templates         | `~/.codex/kdcube-builder`     |
| `KDCUBE_WORKDIR`      | KDCube workdir (where `config/assembly.yaml` lives)  | `~/.kdcube/kdcube-runtime`    |
| `KDCUBE_REPO_ROOT`    | Optional local `kdcube-ai-app` clone — enables `bundle-tests` and local doc reads | unset |

## Install / update / uninstall

```bash
# Install (or update after git pull)
cd app/ai-app/src/kdcube-ai-app/codex_plugin
./install.sh

# Uninstall
./uninstall.sh
```

`install.sh` is idempotent: it replaces the block between
`<!-- kdcube-builder:begin -->` / `<!-- kdcube-builder:end -->` markers in
`~/.codex/AGENTS.md` in place, so any unrelated content in that file is preserved.

`uninstall.sh` removes the runtime dir, the `kdcube-*.md` prompts, and strips the
AGENTS.md block.

## Playwright MCP (for `kdcube-ui-test`)

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.playwright]
command = "npx"
args = ["@playwright/mcp@latest"]
```

## Known differences in `AGENTS.md`

The `AGENTS.md` Configuration flow section uses the same Tier 1 doc URLs as
the Claude Code `bundle-builder` skill (base `docs/configuration/` and
`docs/sdk/bundle/build/`). If the builder plugin skills are updated, keep
`AGENTS.md` in sync — the URL paths and doc names must match.