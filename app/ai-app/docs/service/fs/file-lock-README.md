---
id: ks:docs/service/fs/file-lock-README.md
title: "Observed File Locks"
summary: "Explains KDCube observed filesystem locks, lock-file metadata, git bundle materialization, knowledge registry builds, and UI build coordination."
tags: ["service", "fs", "locks", "efs", "docker-compose", "runtime", "git-bundles"]
keywords: ["observed file lock", "fcntl", "EFS lock", "docker compose lock", "CLI runtime", "bundle git lock", "lock metadata", "managed bundles", "knowledge build"]
see_also:
  - ks:docs/configuration/assembly-descriptor-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/service/cicd/ngrok-README.md
---
# Observed File Locks

KDCube uses observed file locks when several workers may need to prepare the
same filesystem resource, but only one worker should perform the mutation.

The current implementation lives in:

```text
kdcube_ai_app/storage/observed_file_locks.py
```

Current runtime users of this guarded-build family are:

```text
kdcube_ai_app/infra/plugin/git_bundle.py
  raw observed file lock for git checkout/fetch materialization

kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/entrypoint.py
  raw observed file lock for the documentation knowledge registry/index build

kdcube_ai_app/infra/plugin/bundle_once.py
kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py
  higher-level once-per-signature helper for main UI and widget builds
```

UI component builds use the same operational rule, but not the raw helper
directly: one builder owns the output, waiters observe progress, and the
signature is written only after the expected files are ready.

## Problem

KDCube is a concurrent runtime:

- the processor can run multiple uvicorn worker processes even when there is
  only one logical deployment instance, including local `kdcube start`;
- in ECS, there can also be several processor replicas sharing the same
  EFS-backed managed bundle storage;
- multiple requests may hit the same bundle-owned shared resource that must be
  prepared first, such as a git checkout, generated index, knowledge registry,
  or built UI asset;
- non-singleton bundles can construct fresh entrypoint instances per request.

For resources stored on shared filesystem paths, we need one writer and many
waiters. The waiters must also be able to tell what they are waiting for.

## Lock Shape

The observed file lock has two layers:

```text
same Python worker process
        |
        | threading lock keyed by lock_path
        v
same host / docker-compose shared volume / EFS-shared workers
        |
        | fcntl.flock(lock_file, LOCK_EX)
        v
shared resource mutation
```

The process lock prevents concurrent coroutines/threads in the same worker from
entering the critical section together. The `fcntl` lock coordinates sibling
processes and containers sharing the same mounted filesystem, whether the
runtime is local docker-compose storage or cloud EFS-backed storage. Both are
advisory: they work only when all participants use the same helper.

The lock file itself contains JSON metadata while the lock is held. There is no
separate sidecar file.

Example lock-file content while held:

```json
{
  "created_at": "2026-05-13T11:26:15.411392+00:00",
  "created_ts": 1778667975.411392,
  "hostname": "ip-10-0-1-23",
  "instance_id": "proc-19b81ee1",
  "operation": "git.bundle.materialize",
  "owner_token": "7b3b5c...",
  "pid": 52302,
  "resource_id": "git-bundle:kdcube.copilot@2026-04-03-19-05.knowledge:2026.5.13.117"
}
```

On release, the same file is truncated back to empty and unlocked.

## Acquire Flow

```text
caller
  |
  v
observed_file_lock(lock_path, resource_id, operation, wait_seconds=...)
  |
  |-- acquire in-process lock for this lock_path
  |
  |-- open lock file with a+
  |
  |-- try fcntl LOCK_EX | LOCK_NB
  |     |
  |     | success
  |     v
  |   write JSON metadata into the lock file
  |     |
  |     v
  |   yield critical section
  |
  | failure: somebody else holds it
  |     |
  |     v
  |   read same lock file metadata
  |     |
  |     v
  |   log/wait callback:
  |     age, owner instance, host, pid, operation
  |     |
  |     v
  |   poll until the lock is available or wait_seconds expires
```

Release:

```text
critical section exits
  |
  v
clear lock file content
  |
  v
fcntl LOCK_UN
  |
  v
release in-process lock
```

Callers can pass a wait budget. If the owner process dies, the OS releases the
`fcntl` lock and the next caller proceeds. If the owner process is still alive
but stuck, the waiter fails after the configured budget instead of blocking an
MCP request or route handler forever.

## Runtime Lifecycle Usage

### Git Bundle Materialization

Git-backed bundles are materialized under the managed bundles root. The runtime
may request this during startup preload, bundle load, route handling, or
processor task preparation.

```text
descriptor contains git bundle
  |
  v
processor / proc route needs bundle path
  |
  v
ensure_git_bundle() or ensure_git_bundle_async()
  |
  v
optional Redis lock:
  kdcube:bundles:git-lock:<tenant>:<project>:shared:<bundle>:<ref>
  |
  v
observed file lock:
  <managed-bundles>/.bundle-locks/<bundle-ref>.lock
  |
  v
clone / fetch / checkout / validate bundle subdir
  |
  v
release lock
  |
  v
bundle entrypoint can load from stable local path
```

Current direct callers:

- `ensure_git_bundle()` uses `_redis_bundle_lock()` then `_bundle_lock()`.
- `ensure_git_bundle_async()` uses `_async_redis_bundle_lock()` then
  `_async_bundle_lock()`.

`_bundle_lock()` and `_async_bundle_lock()` are thin wrappers around
`observed_file_lock()` and `observed_file_lock_async()`.

### Startup Preload

When bundle preload is enabled, workers attempt to warm bundles early.

```text
proc startup
  |
  v
bundle registry load
  |
  v
preload git-backed bundles
  |
  v
all workers may discover the same git ref
  |
  v
Redis shared lock serializes processor replicas
that share the managed bundle root
  |
  v
observed file lock serializes mounted filesystem mutation
  |
  v
one worker materializes; others wait or find fresh output
```

The file lock is still needed even when Redis is enabled because it protects the
actual filesystem path used for clone/fetch/checkout.

### Documentation MCP / Knowledge Bundle

The built-in `kdcube.copilot@2026-04-03-19-05` bundle is a concrete example of
this mechanism. It exposes a public, read-only documentation MCP endpoint over
an indexed knowledge space. To keep that MCP endpoint fast and safe in local
docker-compose and cloud runtimes, the bundle protects the shared prepared
knowledge state with exclusive build guards.

That example has two guarded phases:

- git materialization of the source repo/ref;
- knowledge-space registry/index build under the bundle storage root.

The intended hot path is:

```text
MCP request: search/read docs
  |
  v
bundle instance checks shared knowledge signature
  |
  | cache fresh
  |---------------------> serve existing index/docs
  |
  | cache missing/stale
  v
materialize configured docs repo/ref
  |
  v
ensure_git_bundle[_async]()
  |
  v
Redis lock + observed file lock
  |
  v
source repo/ref is locally materialized
  |
  v
observed knowledge build lock:
  <bundle-storage>/.knowledge.lock
  |
  v
build or refresh knowledge output:
  docs/, index.json, index.md, copied source roots
  |
  v
verify output readiness
  |
  v
write shared signature
  |
  v
serve docs
```

The lock should not be on the normal read path when the shared signature and
outputs are already fresh. It is only for the rare materialization/build path.

Knowledge build completion is defined by both conditions:

```text
.knowledge.signature == expected signature
and
_knowledge_outputs_ready(...) == true
```

The signature is written only after `prepare_knowledge_space(...)` returns and
the output readiness check succeeds.

The knowledge lock has a bounded wait budget controlled by
`KDCUBE_COPILOT_KNOWLEDGE_LOCK_WAIT_SECONDS` (default: `300`). A stuck owner
therefore becomes a visible build failure instead of an indefinitely hanging
MCP read/search call.

### Helper Use In Bundle Code

Bundle code can use the same helper for a bundle-owned shared object such as an
index, local mirror, generated registry, or prepared model asset. The pattern is
always:

1. compute the expected signature;
2. return immediately if signature and output readiness are already current;
3. acquire the observed lock;
4. re-check under the lock;
5. build into a temporary or otherwise safe location;
6. verify readiness;
7. write the signature last.

Example:

```python
import pathlib

from kdcube_ai_app.storage.observed_file_locks import observed_file_lock


def _signature_path(storage_root: pathlib.Path) -> pathlib.Path:
    return storage_root / ".my-index.signature"


def _index_ready(storage_root: pathlib.Path) -> bool:
    return (storage_root / "my-index" / "index.json").exists()


def _read_signature(storage_root: pathlib.Path) -> str | None:
    try:
        return _signature_path(storage_root).read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _write_signature(storage_root: pathlib.Path, signature: str) -> None:
    _signature_path(storage_root).write_text(f"{signature}\n", encoding="utf-8")


def ensure_my_index(self) -> pathlib.Path:
    storage_root = self.bundle_storage_root()
    bundle_id = str(getattr(getattr(self.config, "ai_bundle_spec", None), "id", "") or self.BUNDLE_ID)
    signature = self.bundle_prop("knowledge.ref", default="local")

    if _read_signature(storage_root) == signature and _index_ready(storage_root):
        return storage_root / "my-index"

    with observed_file_lock(
        lock_path=storage_root / ".my-index.lock",
        resource_id=f"{bundle_id}:my-index",
        operation="my.bundle.index.build",
        wait_seconds=300,
    ):
        if _read_signature(storage_root) == signature and _index_ready(storage_root):
            return storage_root / "my-index"

        build_my_index(storage_root / "my-index")
        if not _index_ready(storage_root):
            raise RuntimeError("index build completed but output is not ready")
        _write_signature(storage_root, signature)

    return storage_root / "my-index"
```

For async code that must not block the event loop while waiting for a lock, use
`observed_file_lock_async(...)`.

### UI Component Builds

Bundle UI builds use a related once-per-signature helper:

```text
kdcube_ai_app/infra/plugin/bundle_once.py
```

Current UI build users:

```text
kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py
  _ensure_static_ui_app_build(...)
```

UI builds are keyed per output:

```text
main UI:
  <bundle-storage>/ui
  <bundle-storage>/.ui.signature
  operation: ui-main-view

widget UI:
  <bundle-storage>/ui/widgets/<alias>
  <bundle-storage>/.ui.widgets/<alias>.signature
  operation: ui-widget-<alias>
```

The UI build lifecycle is:

```text
bundle on_load / props change
  |
  v
compute source tree signature
  |
  v
if signature + index.html are current:
  skip
  |
  v
otherwise acquire once-build lock
  |
  v
copy source to temp build dir
  |
  v
npm install/build into temp output
  |
  v
verify temp output has index.html
  |
  v
atomically swap temp output into final destination
  |
  v
write signature
  |
  v
waiters observe signature + index.html and proceed
```

Callers now wait for the current build to finish. They no longer return stale
existing UI output while a newer signature is being built, and they do not treat
timeout-with-existing-output as success. If the build does not finish within the
configured wait budget, the caller sees a build failure instead of silently
serving an old artifact as if it were current.

While waiting, the helper logs the operation, storage root, lock age, and owner
metadata from `<bundle-storage>/.kdcube.once/<operation>.lock/owner.json`. A
successful waiter exits through the `became_current` path only after both the
signature and `index.html` are present.

`bundle_once.py` is the higher-level UI helper because UI builds need more than
a raw critical section: they need an operation-specific lock, source signature,
output readiness predicate, and atomic publication. The low-level observed file
lock remains the primitive used where a caller already owns its own
signature/readiness lifecycle, such as git materialization and the knowledge
registry build.

## Why Metadata Is In The Lock File

The lock file is already the coordination object. Keeping metadata in the same
file makes operational inspection simple:

```text
cat <managed-bundles>/.bundle-locks/<bundle-ref>.lock
```

The helper writes metadata only after acquiring the `fcntl` lock. Waiters read
the same file before blocking, so logs can include:

- how old the lock is;
- which instance/host/pid owns it;
- which operation is running;
- which resource is protected.

The lock file is cleared on normal release. If a process dies while holding the
lock, the OS releases the `fcntl` lock when the file descriptor closes, but the
JSON content may remain. In that case the next owner overwrites it after
acquiring the lock. A stale JSON body without an active `fcntl` lock is
diagnostic residue, not ownership.

## Redis Companion Lock

For git bundle materialization, KDCube also has an observed Redis lock:

```text
kdcube_ai_app/storage/observed_redis_locks.py
```

The Redis key uses a constant shared segment:

```text
kdcube:bundles:git-lock:<tenant>:<project>:shared:<bundle_id>:<ref>
```

The owner identity is stored in the Redis value, not in the key. This matters:
if `INSTANCE_ID` were part of the key, two processor replicas would take
different locks and both could mutate the same shared storage path.

Redis protects cross-task coordination before filesystem work starts. The file
lock protects the filesystem mutation itself.

## Current Scope

Observed file locks are currently used for:

- platform-managed git bundle materialization;
- the built-in copilot bundle knowledge-space build lock.

They are not yet the general replacement for:

- `kdcube_ai_app/storage/distributed_locks.py`, which is an explicit
  storage-backend lock/queue helper;
- `kdcube_ai_app/apps/knowledge_base/index/index_rebuild_tracker.py`, which
  records persistent rebuild operations and heartbeats.

Those modules solve related but different problems. The observed file lock is a
small runtime primitive for mounted-filesystem critical sections with readable
owner metadata. UI builds currently use `bundle_once.py`, which combines an
exclusive once-build lock with signature and readiness checks.

## Operational Notes

Lock locations are subsystem-specific:

- Git bundle materialization uses:
  `<managed-bundles>/.bundle-locks/<bundle-ref>.lock`
- The built-in copilot documentation knowledge build uses:
  `<bundle-storage>/.knowledge.lock`
- UI main/widget builds use the higher-level `bundle_once.py` helper and keep
  operation locks under:
  `<bundle-storage>/.kdcube.once/<operation>.lock/`

Filesystem shape:

```text
<runtime-storage>/
  managed-bundles/
    .bundle-locks/
      <bundle-ref>.lock                 # git bundle materialization lock
    <git-bundle-materialized-dir>/       # cloned/fetched git bundle source

  bundle-storage/
    <tenant>/
      <project>/
        <specific-bundle-storage-dir>/
          .knowledge.lock               # copilot docs knowledge build lock
          .knowledge.signature          # copilot docs knowledge build signature
          docs/
          index.json
          index.md

          .kdcube.once/
            ui-main-view.lock/
              owner.json                # main UI build owner/wait metadata
            ui-widget-<alias>.lock/
              owner.json                # widget build owner/wait metadata

          .ui.signature                 # main UI build signature
          .ui.widgets/
            <alias>.signature           # widget build signature
          ui/
            index.html                  # main UI built output
            widgets/
              <alias>/
                index.html              # widget built output
```

The exact top-level root depends on deployment mode. In local `kdcube start` /
docker-compose it is the mounted runtime data directory. In cloud deployments it
is the configured shared filesystem location, commonly EFS-backed.

For raw observed file locks:

- the lock file may be empty when no process owns it
- non-empty JSON means either an owner currently holds the lock or a previous
  owner died before clearing diagnostic content
- the lock itself is the OS `fcntl` lock, not the JSON body
- waiters fail after the caller's wait budget when a lock owner is alive but
  stuck

For git bundle materialization specifically:

- Redis lock timeout fails closed: if the shared Redis lock cannot be acquired
  in time, the caller raises instead of proceeding without coordination
- git commands are bounded by `bundle_git_command_timeout_seconds`
