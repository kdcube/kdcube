# /kdcube-bootstrap

Generate a clean local descriptor set for one KDCube bundle.

Arguments (from the text after `/kdcube-bootstrap`):
`<bundle-id> <bundle-path> [--module entrypoint] [--profile default] [--tenant demo-tenant] [--project demo-project] [--singleton] [--host-bundles-path <parent>]`

Run:

```bash
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" bootstrap <args>
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