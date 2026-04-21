# Architecture

## Layout

```
builder_plugin/
  .claude-plugin/marketplace.json        ← marketplace manifest (one plugin)
  plugins/kdcube-builder/
    .claude-plugin/plugin.json           ← plugin manifest + userConfig + MCP
    scripts/kdcube_local.py              ← single entry point for all operations
    skills/                              ← prompt-skills (see skills.md)
    templates/                           ← YAML templates used by bootstrap
```

## Three layers

**1. Manifest (`plugin.json`)**
Declares the skills directory, the Playwright MCP server used by `kdcube-ui-test`,
and `userConfig` keys:

- `kdcube_repo_root` — optional absolute path to a local `kdcube-ai-app` clone.
  Used for local docs lookup during bundle authoring and for running the shared
  bundle test suite.
- `kdcube_workdir` — optional KDCube workdir. Default `~/.kdcube/kdcube-runtime`.

These reach the process as `CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT` and
`CLAUDE_PLUGIN_OPTION_KDCUBE_WORKDIR`.

**2. Skills**
Markdown files with YAML frontmatter. Each one is selected either by its
`description:` field (auto-invocation based on user intent) or by an explicit
`/kdcube-builder:<skill>` call. Skills contain no code — only instructions
telling Claude which CLI subcommand to run and how to interpret the result. See
[skills.md](./skills.md).

**3. `kdcube_local.py`**
A single argparse CLI that owns the actual logic. Subcommands:
`bootstrap`, `start`, `reload`, `stop`, `bundle-tests`, `verify-reload`,
`use-descriptors`, `status`, `install`.

Every skill ultimately runs the same shape of command:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" <subcommand> [args]
```

`CLAUDE_PLUGIN_ROOT` is injected by Claude Code and points at
`plugins/kdcube-builder/`.

## State directories

Plugin-owned state (descriptor profiles) lives under
`~/.kdcube/builder-plugin/profiles/<profile>/`:

```
profiles/default/
  descriptors/    ← real files (bootstrap) or a symlink (use-descriptors)
  git-bundles/    ← local git-bundle cache
```

The runtime workdir is separate — default `~/.kdcube/kdcube-runtime`. It holds
`config/.env`, `config/bundles.yaml`, and everything the container reads.

## Workdir resolution

Every skill follows the same order:

1. `CLAUDE_PLUGIN_OPTION_KDCUBE_WORKDIR` (set via `userConfig`)
2. `KDCUBE_WORKDIR` env var
3. `kdcube_local.py status` output
4. Default `~/.kdcube/kdcube-runtime`

## Version pinning

Plugin version and the default KDCube release ref live in three places:

- `plugins/kdcube-builder/scripts/kdcube_local.py::CURRENT_RELEASE`
- `.claude-plugin/marketplace.json`
- `plugins/kdcube-builder/.claude-plugin/plugin.json`

Bump all three together on release.