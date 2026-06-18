# Local Cross-Site Scene Emulator Patched Files

This directory stores full-file snapshots for the local same-site cross-origin
scene emulator. The patch files one level up are useful when the target files
still match their old shape. These snapshots are useful when the surrounding
files have drifted and an agent needs the complete before/after picture.

## Files

`before/`

- `nginx_proxy.conf` — rollback shape for the local OpenResty proxy config.
- `assembly.yaml` — rollback shape for the local runtime descriptor.
- `kdcube.config.json` — website config before the `local-cross` profile.

`after/`

- `nginx_proxy.conf` — working OpenResty config for:
  - `https://local.kdcube.tech`
  - `https://runtime.local.kdcube.tech`
- `assembly.yaml` — runtime descriptor with local scene/frame/CORS origins.
- `kdcube.config.json` — website config with the `local-cross` profile.

## How To Use

Prefer real backups from the local runtime when they exist:

```bash
/Users/elenaviter/.kdcube/kdcube-runtime/demo-tenant__demo-project/config/*.before-local-scene-emulator
```

Use these snapshots when no local backups exist or when a future patch no
longer applies cleanly.

The snapshots are environment-specific examples for the local demo runtime:

```text
tenant:  demo-tenant
project: demo-project
parent:  https://local.kdcube.tech
runtime: https://runtime.local.kdcube.tech
```

If the local descriptor changes materially, refresh these snapshots after a
known-good emulator run.
