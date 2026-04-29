# /kdcube-verify-reload

Confirm that a KDCube bundle reload actually took effect — checks proc cache eviction and
bundle registry acceptance.

Arguments (from the text after `/kdcube-verify-reload`): `<bundle-id>`

Run:

```bash
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" verify-reload <bundle-id>
```

Print the output verbatim.

## Rules

- The CLI-native reload command is `kdcube reload <bundle_id> --workdir <workdir>`.
  This prompt verifies that the reload actually took effect in proc — always run it
  immediately after `/kdcube-runtime reload <bundle-id>` or `kdcube reload`.
- If the helper exits non-zero, report the error and tell the user the bundle may not be
  live — do not retry automatically.
- If the output contains `eviction: None`, warn the user that the bundle was not in the
  proc cache at reload time. This is normal on first load, unexpected on a re-deploy.
- Do not run this before a `reload` — it checks current proc state, not a pending one.