# KDCube Builder — Codex CLI extension

Port of the `kdcube-builder` Claude Code plugin for [OpenAI Codex CLI](https://github.com/openai/codex).
Codex does not have a plugin/marketplace system like Claude Code, so this ships as:

- `~/.codex/AGENTS.md` — a user-level rule block (always in context) that routes natural
  language to the right action.
- `~/.codex/prompts/kdcube-*.md` — slash commands for explicit invocation.
- `~/.codex/kdcube-builder/` — the shared helper script + descriptor templates (reused
  verbatim from the Claude Code plugin).

Once installed, talk to Codex naturally:

> "start KDCube"
> "reload the telegram-bot bundle"
> "create a bundle that wraps my FastAPI app"

Or invoke an explicit command:

- `/kdcube-dev <free-form task>` — main orchestrator
- `/kdcube-bundle-builder <task>` — bundle authoring
- `/kdcube-use-descriptors <dir>` — link an existing descriptor directory
- `/kdcube-bootstrap <bundle-id> <bundle-path>` — generate a fresh local descriptor set
- `/kdcube-runtime <action …>` — start / stop / reload / bundle-tests
- `/kdcube-verify-reload <bundle-id>` — confirm a reload took effect
- `/kdcube-cli <free-form intent>` — direct `kdcube` CLI operations
- `/kdcube-ui-test` — browser-test the chat UI (requires a Playwright MCP server — see below)

---

## Install

### From a local clone

```bash
git clone https://github.com/kdcube/kdcube-ai-app.git
cd kdcube-ai-app/app/ai-app/src/kdcube-ai-app/codex_plugin
./install.sh
```

`install.sh` copies the helper + templates from the sibling `builder_plugin/` directory
into `~/.codex/kdcube-builder/`, drops prompt files into `~/.codex/prompts/`, and merges
the AGENTS.md block (idempotent — rerun safely to pick up updates).

### Uninstall

```bash
./uninstall.sh
```

Removes the runtime dir, the `kdcube-*.md` prompts, and the AGENTS.md block. Leaves any
unrelated content in `~/.codex/AGENTS.md` intact.

### Update

Pull the repo and re-run `./install.sh`. The block between the `kdcube-builder:begin` /
`kdcube-builder:end` markers in `~/.codex/AGENTS.md` is replaced in place.

---

## Prerequisites

- Python 3.9+
- Docker (for running KDCube locally)
- `kdcube-cli` installed — see [kdcube-cli on PyPI](https://pypi.org/project/kdcube-cli/)
- For `/kdcube-ui-test`: a Playwright MCP server configured in
  `~/.codex/config.toml`, e.g.:

  ```toml
  [mcp_servers.playwright]
  command = "npx"
  args = ["@playwright/mcp@latest"]
  ```

---

## Configuration

All configuration is via env vars (set them in your shell rc or pass per-invocation):

| Variable | Purpose | Default |
|---|---|---|
| `KDCUBE_BUILDER_ROOT` | Where install.sh put the runtime + templates | `~/.codex/kdcube-builder` |
| `KDCUBE_WORKDIR` | KDCube workdir (where `config/assembly.yaml` lives) | `~/.kdcube/kdcube-runtime` |
| `KDCUBE_REPO_ROOT` | Optional local `kdcube-ai-app` clone — enables `bundle-tests` and local doc reads | unset |

Docs are always fetched from GitHub via the agent's built-in web fetch — the plugin
itself does not need the repo on disk. `KDCUBE_REPO_ROOT` is an opt-in fast path.

---

## What's inside

```
codex_plugin/
  install.sh           ← copies runtime + prompts, merges AGENTS.md
  uninstall.sh
  AGENTS.md            ← user-level rules (main orchestrator)
  prompts/
    kdcube-dev.md
    kdcube-bundle-builder.md
    kdcube-use-descriptors.md
    kdcube-bootstrap.md
    kdcube-runtime.md
    kdcube-verify-reload.md
    kdcube-cli.md
    kdcube-ui-test.md
```

The helper script and YAML templates live in the sibling `builder_plugin/` and are
copied in by `install.sh` — they are not duplicated in this directory.