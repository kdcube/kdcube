---
id: ks:docs/sdk/bundle/bundle-storage-and-cache-README.md
title: "Bundle Storage And Cache"
summary: "Bundle storage surfaces: per-bundle durable storage, shared local bundle storage, and KV cache, including backend choices, path layout, and access patterns."
tags: ["sdk", "bundle", "storage", "cache", "s3", "filesystem"]
keywords: ["per bundle durable storage", "shared local bundle storage", "kv cache usage", "storage backend selection", "filesystem or s3 storage", "storage path layout", "artifact read write patterns", "cache backed bundle state"]
see_also:
  - ks:docs/sdk/storage/cache-README.md
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/bundle-index-README.md
---
# Bundle Storage + Cache

This guide covers **per‑bundle storage** and the **KV cache** available to bundle code.

---

## Bundle storage backend (localfs or S3)

Configure the storage backend in env:

```
CB_BUNDLE_STORAGE_URL=file:///absolute/path/prefix
```

S3 example:
```
CB_BUNDLE_STORAGE_URL=s3://my-bucket/prefix
```

Use inside a bundle:
```python
from kdcube_ai_app.apps.chat.sdk.storage.bundle_artifact_storage import BundleArtifactStorage

self.storage = BundleArtifactStorage(
    tenant="acme",
    project="myproj",
    bundle_id=self.id,
    storage_uri=None,  # or override per call
)
```

Common operations:
```python
self.storage.write("logs/run1.txt", "hello\n", mime="text/plain")
text = self.storage.read("logs/run1.txt", as_text=True)
names = self.storage.list("logs/")
self.storage.delete("logs/run1.txt")
```

Storage path layout (logical):
```
cb/tenants/{tenant}/projects/{project}/ai-bundle-storage/{bundle_id}/...
```

The `ai-bundle-storage` path segment is the current compatibility prefix for
existing deployments; new code should use the neutral `BundleArtifactStorage`
API name.

---

## KV cache (Redis)

Bundles can use the platform KV cache for lightweight state/config:

```python
from kdcube_ai_app.infra.service_hub.cache import create_kv_cache

cache = create_kv_cache()
await cache.set("my:key", {"value": 123})
data = await cache.get("my:key")
```

Use KV cache for:
- bundle props overrides
- small runtime config
- per‑tenant/project flags

---

## Shared bundle local storage (filesystem)

Bundles can prepare a **shared local filesystem** (local disk or EFS) to store
large read‑only assets, indexes, or any bundle‑specific data that should be
reused across conversations and instances.

This storage is **distinct** from `CB_BUNDLE_STORAGE_URL`:
- `CB_BUNDLE_STORAGE_URL` = per‑bundle, read/write storage backend (localfs/S3).
- `BUNDLE_STORAGE_ROOT` = shared **local filesystem** mounted into the proc container.

Env (filesystem path, not URI):
```
BUNDLE_STORAGE_ROOT=/bundles/_bundle_storage
```

Path layout (default):
```
<bundles_root>/_bundle_storage/<tenant>/<project>/<bundle_id>
```

Authoring rule:
- if bundle code needs instance-local filesystem storage, do not hardcode your own root
- resolve it through the platform helper
- for normal mutable runtime data that should survive across requests on the same instance, prefer an unversioned subdirectory under the helper-resolved bundle storage root

Use from bundle code:

```python
storage_root = self.bundle_storage_root()
local_root = storage_root / "_my_subsystem"
local_root.mkdir(parents=True, exist_ok=True)
```

If you are not inside a bundle entrypoint instance, use the low-level helper:

```python
from kdcube_ai_app.infra.plugin.bundle_storage import bundle_storage_dir

storage_root = bundle_storage_dir(
    bundle_id=bundle_id,
    tenant=tenant,
    project=project,
    ensure=True,
)
```

Authoring rule:
- inside entrypoint methods, use `self.bundle_storage_root()`
- outside entrypoint methods, use `bundle_storage_dir(bundle_id=..., tenant=..., project=...)`
- always create your own `_subsystem` directory under that stable root
- do not create alternative primary roots and do not rely on version-suffixed bundle roots

Practical split:
- `self.bundle_storage_root()`
  - canonical tenant/project/bundle-local root chosen by the platform
  - use this in entrypoint code
- `bundle_storage_dir(...)`
  - same canonical tenant/project/bundle-local root
  - use this only in helpers that do not have `self.bundle_storage_root()`
- `BundleArtifactStorage`
  - backend storage API for bundle artifacts
  - separate from the shared local filesystem root
- `bundle_tool_context.host_files(...)`
  - current conversation/turn artifact hosting helper for trusted catalog tools
  - available in normal and isolated supervisor/runtime tool execution
  - use only after the tool has written or materialized the user-visible file
  - requires SDK-prepared tool context: hosting service, tenant/project/user/
    conversation/turn scope, conversation storage, and output directory

Conversation file hosting is separate from durable bundle storage. User-visible
files produced during a React turn should use the strict tool result contract
`ret.artifact_type: "files"` with `ret.files[]`, or `host_files(...)` from a
trusted tool, so the platform can register hosted metadata and emit `chat.files`.

Example pattern used in real bundles:
- knowledge/index preparation may live under the bundle storage root
- mutable local workspaces such as a cloned repo or a daily pipeline state folder should live under an unversioned subdirectory like:
  - `<tenant>/<project>/<bundle_id>/_task_tracker`
  - `<tenant>/<project>/<bundle_id>/_knowledge_base_admin`

Important contract:
- the platform owns only the stable root selection:
  - `<bundle_storage_root>/<tenant>/<project>/<safe(bundle_id)>`
- the bundle owns everything below that root
- if the bundle wants rebuildable caches, it should create and manage them under that stable root explicitly

ReAct integration (optional):
- You can expose a knowledge space (`ks:`) backed by this local storage.
- Example: `react.read(["ks:docs/README.md"])` reads from a bundle‑defined resolver.

See:
- [docs/sdk/bundle/bundle-knowledge-space-README.md](bundle-knowledge-space-README.md) (resolver + ks namespace)
- [docs/sdk/bundle/bundle-developer-guide-README.md](bundle-developer-guide-README.md) (on_bundle_load + usage)
- Example bundle: `.../examples/bundles/kdcube.copilot@2026-04-03-19-05`

---

## References (code)

- Storage: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/storage/bundle_artifact_storage.py`
- KV cache: `src/kdcube-ai-app/kdcube_ai_app/infra/service_hub/cache.py`
- Shared local storage helpers: `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_storage.py`
