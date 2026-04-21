# Runtime Flows

Two flows are worth documenting in detail because they are the ones that go
wrong in non-obvious ways: first-time setup and the reload / verify cycle.

## First-time setup

The orchestrator runs `status` first. If no descriptor profile is linked, it
asks the user which case applies.

### Case A — descriptors already exist on disk

User: *"I have descriptors at /path/to/dir"*.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" use-descriptors /path/to/dir
```

`cmd_use_descriptors` validates that the directory contains `assembly.yaml`,
`bundles.yaml`, `gateway.yaml`, `secrets.yaml`, then creates a symlink at
`~/.kdcube/builder-plugin/profiles/default/descriptors` pointing to it.

### Case B — no descriptors yet

The orchestrator asks for `bundle_id` and the absolute bundle path, then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" bootstrap <bundle_id> <bundle_path>
```

`cmd_bootstrap`:

1. Reads `templates/assembly.yaml`, `gateway.yaml`, `secrets.yaml`.
2. Substitutes tenant, project, host bundle paths, platform repo/ref.
3. Generates `bundles.yaml` with `default_bundle_id: <bundle_id>` and `path:
   /bundles/<bundle-id>` (container path — not the host path).
4. Writes everything into `~/.kdcube/builder-plugin/profiles/default/descriptors/`.

After either case, the orchestrator offers to start.

## Start

`cmd_start` copies the descriptor directory into a temp dir, expands `~/` and
`$HOME/` in every YAML value (the `kdcube` CLI does not do this itself), and
invokes:

```bash
kdcube --descriptors-location <tmp_dir> <mode-flags>
```

Modes exposed by the `local-runtime` skill:

| Mode             | CLI flags                   |
|------------------|-----------------------------|
| `upstream`       | `--build --upstream`        |
| `latest`         | `--build --latest`          |
| `latest-image`   | `--latest`                  |
| `release <ref>`  | `--build --release <ref>`   |
| `release-image <ref>` | `--release <ref>`      |

`kdcube-dev` defaults to `latest-image` — the fastest path for users who are
not building the platform themselves.

## Reload + verify

This is the hot path every time a bundle changes.

```bash
kdcube_local.py reload <bundle_id>
kdcube_local.py verify-reload <bundle_id>
```

**Why both calls are required:** `reload` returns **before** the proc cache
actually rotates. Without verification, there is no way to tell whether the new
code is live.

`cmd_verify_reload`:

1. Finds the running `chat-proc` container via `docker ps --filter name=chat-proc`.
2. `docker exec`s a short Python script into it.
3. The script POSTs `{"bundle_id": ...}` to
   `http://127.0.0.1:8020/internal/bundles/reset-env`.
4. The response is parsed for `status`, `bundle_id`, `count`, `eviction`.

Output to watch:

- **`eviction: <hash>`** and matching `bundle_id` — reload confirmed live.
- **`eviction: null`** — the bundle was not in the proc cache. On first load
  this is normal; on a redeploy it means the id or path in `bundles.yaml` is
  wrong, or the bundle never loaded.
- Non-zero exit — reload may not have taken effect. Do not retry
  automatically; surface the error.

## Restarts flush the proc cache

Any container restart — secrets injection (`kdcube --secrets-set` restarts
`chat-proc` + `chat-ingress`), `kdcube --stop`/`--start`, manual
`docker restart` — drops the proc cache. Every active bundle must be reloaded
afterwards.

## macOS `bundles.yaml` caveat

Docker Desktop on macOS does not re-read a file-level bind mount when the host
file's inode changes, and the Edit/Write tools replace inodes. After editing
`$WORKDIR/config/bundles.yaml`, the container keeps reading the old file until:

```bash
docker restart all_in_one_kdcube-chat-proc-1
```

Then `reload <bundle-id>` + `verify-reload`. This is the one reload-related
case where a raw `docker restart` is acceptable. Files **inside** the bundle
directory are fine — that's a directory bind, not a file bind.