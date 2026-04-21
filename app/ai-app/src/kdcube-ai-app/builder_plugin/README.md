# KDCube Builder — Claude Code Plugin

Claude Code plugin for KDCube development: start the runtime, reload and test bundles, build bundle code.

Once installed, just talk to Claude naturally — no slash commands needed:

> "start KDCube"  
> "reload the telegram-bot bundle"  
> "create a bundle that wraps my FastAPI app"

---

## Install

### Option A — one-liner (recommended, no git clone needed)

```bash
claude plugin marketplace add https://github.com/kdcube/kdcube-ai-app --sparse .claude-plugin app/ai-app/src/kdcube-ai-app/builder_plugin
claude plugin install kdcube-builder@kdcube-builder-marketplace --scope user
```

`--scope user` installs globally for your user — available in every project.  
Use `--scope project` to install only for the current directory.

### Option B — from a local clone

```bash
git clone https://github.com/kdcube/kdcube-ai-app.git
claude plugin marketplace add /abs/path/to/kdcube-ai-app
claude plugin install kdcube-builder@kdcube-builder-marketplace --scope user
```

---

## Prerequisites

- [Claude Code](https://claude.ai/code) installed
- Python 3.9+
- Docker (for running KDCube locally)
- `kdcube-cli` installed — see [kdcube-cli on PyPI](https://pypi.org/project/kdcube-cli/)

---

## First run

Open Claude Code in your project and say:

```
what's running in kdcube?
```

Claude will check the descriptor profile, workdir, and Docker containers and report the status.

If descriptors are not configured yet, Claude will ask for the path to your descriptor directory
(`assembly.yaml`, `bundles.yaml`, `gateway.yaml`, `secrets.yaml`) and set it up.

---

## Update

### Option A (GitHub source)

```bash
claude plugin marketplace update kdcube-builder-marketplace
```

### Option B (local clone)

```bash
git pull
claude plugin update kdcube-builder@kdcube-builder-marketplace
```

---

## Uninstall

```bash
claude plugin uninstall kdcube-builder@kdcube-builder-marketplace --scope user
claude plugin marketplace remove kdcube-builder-marketplace
```

---

## Validate (for plugin development)

```bash
claude plugin validate /abs/path/to/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/builder_plugin
```

Load the plugin directly without installing:

```bash
claude --plugin-dir /abs/path/to/builder_plugin/plugins/kdcube-builder
```

---

## What's inside

```
builder_plugin/
  .claude-plugin/
    marketplace.json
  plugins/
    kdcube-builder/
      .claude-plugin/
        plugin.json
      scripts/
        kdcube_local.py       ← CLI wrapper (start, reload, status, ...)
      skills/
        kdcube-dev/           ← natural language orchestrator (main entry point)
        local-runtime/        ← start / reload / stop / bundle-tests
        verify-reload/        ← verify bundle cache eviction after reload
        use-descriptors/      ← point a profile at an existing descriptor directory
        bootstrap-local/      ← generate a fresh local descriptor set
        bundle-builder/       ← bundle authoring with docs and examples
      templates/
        assembly.yaml
        bundles.yaml
        ...
```