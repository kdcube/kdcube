---
description: Generate a clean local descriptor set for one KDCube bundle and print the exact next commands to start it.
argument-hint: "<bundle-id> <bundle-path> [--module entrypoint] [--profile default] [--tenant demo-tenant] [--project demo-project] [--singleton]"
disable-model-invocation: true
allowed-tools: Bash Read Write Edit Grep Glob LS
---

# Bootstrap Local KDCube

Parse `$ARGUMENTS` and run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" bootstrap $ARGUMENTS
```

Rules:

- `bundle-path` must be an absolute path to the bundle directory on the host.
- Use a one-bundle local descriptor set by default.
- Do not reuse customer or production descriptors.
- If the command succeeds, report:
  - the generated descriptor directory
  - the chosen host bundles root
  - the bundle path that will be visible inside the container
  - the exact next local start command
- If the bundle path is invalid, stop and explain the exact issue.
