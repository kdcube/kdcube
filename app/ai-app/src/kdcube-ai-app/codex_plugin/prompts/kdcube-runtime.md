# /kdcube-runtime

Start, stop, reload, or run bundle tests in the local KDCube runtime.

Arguments (from the text after `/kdcube-runtime`):

```
start upstream|latest|latest-image [--profile default]
start release <ref> [--profile default]
start release-image <ref> [--profile default]
reload <bundle-id>
stop
bundle-tests <bundle-path>
```

Run:

```bash
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" <args>
```

## Behavior

- `start upstream` — descriptor-driven source build from latest upstream repo state
- `start latest` — descriptor-driven source build from latest released platform
- `start latest-image` — descriptor-driven start from latest released images (no local build)
- `start release <ref>` — descriptor-driven source build from a specific release ref
- `start release-image <ref>` — start from a specific release image ref (no local build)
- `reload <bundle-id>` — reload one bundle in the running runtime
- `stop` — stop the local runtime
- `bundle-tests <bundle-path>` — run the shared bundle suite (requires `KDCUBE_REPO_ROOT`)

## Rules

- If the helper exits with "kdcube not found in PATH", tell the user to run `pip install --user kdcube-cli` and stop.
- If no descriptor profile is linked, tell the user to run `/kdcube-bootstrap` or `/kdcube-use-descriptors` first.
- After `reload`, always follow up with `verify-reload <bundle-id>` to confirm the cache rotated.
- After `bundle-tests`, summarize pass / fail clearly.
- `bundle-tests` needs `KDCUBE_REPO_ROOT` pointing at a local `kdcube-ai-app` clone — if unset, ask the user.

CLI docs: https://pypi.org/project/kdcube-cli/