# KDCube Builder Plugin

This is the installable Claude Code plugin shipped by the local marketplace in the parent directory.

## Install from the local repository

```bash
claude plugin uninstall kdcube-builder@kdcube-builder-marketplace --scope user
claude plugin marketplace remove kdcube-builder-marketplace
claude plugin marketplace add /Users/evgen/PycharmProjects/kdcube-ai-app
claude plugin install kdcube-builder@kdcube-builder-marketplace --scope user
```

## Remove the plugin

```bash
claude plugin uninstall kdcube-builder@kdcube-builder-marketplace --scope user
claude plugin marketplace remove kdcube-builder-marketplace
```

Main skills:

- `/kdcube-builder:bundle-builder`
  - bundle SDK/docs index and implementation guidance
- `/kdcube-builder:bootstrap-local <bundle-id> <bundle-path>`
  - generates a clean local descriptor set for one bundle
- `/kdcube-builder:local-runtime <action ...>`
  - starts KDCube, reloads a bundle, stops the runtime, or runs the shared bundle suite

Generated local state is kept under `${CLAUDE_PLUGIN_DATA}`:

- `profiles/<profile>/descriptors`
- `profiles/<profile>/managed-bundles`

The plugin does not copy customer descriptors. It generates a minimal local descriptor set intended for bundle development.
