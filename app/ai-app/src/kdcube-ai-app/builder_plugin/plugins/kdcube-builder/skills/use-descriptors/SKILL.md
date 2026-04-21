---
description: Point a KDCube profile at an existing directory with deployment descriptors (assembly.yaml, bundles.yaml, gateway.yaml, secrets.yaml) by creating a symlink.
argument-hint: <descriptors_dir> [--profile <name>]
disable-model-invocation: true
allowed-tools: Bash, Read
---

Run the plugin helper to link a profile's descriptors directory to `$ARGUMENTS`:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py use-descriptors $ARGUMENTS

Then print the output verbatim. Do not edit files in the target directory — `use-descriptors` only creates a symlink.

If the helper exits non-zero, stop and show the error to the user. Do not try to "fix" missing descriptor files by creating them.