---
description: Verify that a KDCube bundle was successfully reloaded — checks that the proc cache was evicted and the bundle registry accepts the bundle id.
argument-hint: <bundle-id>
disable-model-invocation: true
allowed-tools: Bash, Read
---

Run the plugin helper to verify the reload:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py verify-reload $ARGUMENTS

Print the output verbatim.

Operational rules:

- Run this immediately after `/kdcube-builder:local-runtime reload <bundle-id>` to confirm the reload took effect.
- If the helper exits non-zero, report the error and tell the user the bundle may not have been reloaded — do not retry automatically.
- If the output contains `eviction: None`, warn the user that the bundle was not in the proc cache at reload time (normal on first load, unexpected on a re-deploy).
- Do not call this skill before a `reload` — it checks current proc state, not a pending one.