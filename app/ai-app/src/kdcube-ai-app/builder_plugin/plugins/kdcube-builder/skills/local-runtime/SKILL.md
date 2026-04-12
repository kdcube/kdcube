---
description: Start local KDCube, reload one bundle, stop the local runtime, or run the shared bundle suite.
argument-hint: "start upstream|latest|latest-image [--profile default] | start release <ref> [--profile default] | start release-image <ref> [--profile default] | reload <bundle-id> | stop | bundle-tests <bundle-path>"
disable-model-invocation: true
allowed-tools: Bash Read Write Edit Grep Glob LS
---

# KDCube Local Runtime

Parse `$ARGUMENTS` and run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" $ARGUMENTS
```

Behavior:

- `start upstream`
  - descriptor-driven source build from latest upstream repo state
- `start latest`
  - descriptor-driven source build from latest released platform
- `start latest-image`
  - descriptor-driven start from latest released images without local source build
- `start release <ref>`
  - descriptor-driven source build from a specific release ref
- `start release-image <ref>`
  - descriptor-driven start from a specific release image ref without local source build
- `reload <bundle-id>`
  - reloads one bundle in the existing runtime
- `stop`
  - stops the local runtime
- `bundle-tests <bundle-path>`
  - runs the shared bundle suite

Operational rules:

- If no descriptor profile was bootstrapped yet, tell the user to run `/kdcube-builder:bootstrap-local` first.
- After `reload`, tell the user what was reloaded.
- After `bundle-tests`, summarize pass/fail clearly.
