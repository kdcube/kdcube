# KDCube Builder Marketplace

This directory is a Claude Code marketplace root. It ships one installable plugin:

- `kdcube-builder`

The plugin is intended for people who want to:

- build KDCube bundles
- bootstrap a clean local descriptor set for one bundle
- run KDCube locally
- reload the bundle after edits
- run the shared bundle validation suite

## Layout

```text
builder_plugin/
  .claude-plugin/
    marketplace.json
  plugins/
    kdcube-builder/
      .claude-plugin/
        plugin.json
      README.md
      scripts/
        kdcube_local.py
      skills/
        bundle-builder/
          SKILL.md
        bootstrap-local/
          SKILL.md
        local-runtime/
          SKILL.md
      templates/
        assembly.yaml
        bundles.yaml
        bundles.secrets.yaml
        gateway.yaml
        secrets.yaml
```

## Install

Add this marketplace from the local path:

```bash
claude plugin marketplace add /abs/path/to/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/builder_plugin
```

Then install the plugin:

```bash
claude plugin install kdcube-builder@kdcube-builder-marketplace --scope user
```

For plugin development only, you can also run Claude with the plugin dir directly:

```bash
claude --plugin-dir /abs/path/to/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/builder_plugin/plugins/kdcube-builder
```

## First local flow

Generate a clean one-bundle local descriptor set:

```text
/kdcube-builder:bootstrap-local my.bundle@1-0 /abs/path/to/my.bundle@1-0
```

Start from latest upstream source:

```text
/kdcube-builder:local-runtime start upstream
```

Reload after bundle edits:

```text
/kdcube-builder:local-runtime reload my.bundle@1-0
```

Run the shared bundle suite:

```text
/kdcube-builder:local-runtime bundle-tests /abs/path/to/my.bundle@1-0
```

## What the plugin fixes

The hard part for new users is usually descriptors, not bundle code.

This plugin therefore:

- generates a minimal local descriptor set instead of reusing customer-heavy samples
- defaults local auth to `simple`
- creates a one-bundle `bundles.yaml`
- keeps the generated descriptors and git-bundle cache under `CLAUDE_PLUGIN_DATA`
- gives Claude exact commands for:
  - source build from upstream
  - source build from latest release
  - image-based start from latest release
  - bundle reload
  - stop
  - bundle tests

## Validate

From the marketplace root:

```bash
claude plugin validate .
```
