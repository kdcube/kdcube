---
id: ks:docs/sdk/bundle/bundle-storage-cache-README.md
title: "Bundle Storage Cache"
summary: "Bundle storage backend (localfs/S3), shared local bundle storage (filesystem), and KV cache."
tags: ["sdk", "bundle", "storage", "cache", "s3", "filesystem"]
keywords: ["CB_BUNDLE_STORAGE_URL", "BUNDLE_STORAGE_ROOT", "localfs", "s3", "bundle storage", "shared local storage", "kv cache", "read/write paths", "artifacts"]
see_also:
  - ks:docs/sdk/storage/cache-README.md
  - ks:docs/sdk/bundle/bundle-dev-README.md
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
from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage

self.storage = AIBundleStorage(
    tenant="acme",
    project="myproj",
    ai_bundle_id=self.id,
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
cb/tenants/{tenant}/projects/{project}/ai-bundle-storage/{ai_bundle_id}/...
```

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
<bundles_root>/_bundle_storage/<tenant>/<project>/<bundle_id>[__<git_commit|ref|version>]
```

Authoring rule:
- if bundle code needs instance-local filesystem storage, do not hardcode your own root
- resolve it through the platform helper
- for normal mutable runtime data that should survive across requests on the same instance, prefer an unversioned subdirectory under the helper-resolved bundle storage root

Use from bundle code:

```python
from kdcube_ai_app.infra.plugin.bundle_storage import bundle_storage_dir

local_root = bundle_storage_dir(
    bundle_id=bundle_id,
    version=None,
    tenant=tenant,
    project=project,
    ensure=True,
) / "_my_subsystem"
local_root.mkdir(parents=True, exist_ok=True)
```

If you already have an entrypoint instance, use its helper first:

```python
storage_root = self.bundle_storage_root()
```

and only drop to `bundle_storage_dir(...)` when you specifically need the unversioned tenant/project/bundle path shape.

Practical split:
- `self.bundle_storage_root()`
  - versioned bundle storage root for the active bundle spec
  - good for versioned prepared assets and caches tied to the deployed bundle version
- `bundle_storage_dir(..., version=None) / "_name"`
  - unversioned tenant/project/bundle-local root
  - good for mutable local working state, checkouts, local mirrors, and long-lived runtime workspaces
- `AIBundleStorage`
  - backend storage API for bundle artifacts
  - separate from the shared local filesystem root

Example pattern used in real bundles:
- knowledge/index preparation may live under the bundle storage root
- mutable local workspaces such as a cloned repo or a daily pipeline state folder should live under an unversioned subdirectory like:
  - `<tenant>/<project>/<bundle_id>/_news`
  - `<tenant>/<project>/<bundle_id>/_knowledge_base_admin`

Version suffix rules (if present):
- `git_commit` (preferred, if set)
- else `ref`
- else `version`

ReAct integration (optional):
- You can expose a knowledge space (`ks:`) backed by this local storage.
- Example: `react.read(["ks:docs/README.md"])` reads from a bundle‑defined resolver.

See:
- [docs/sdk/bundle/bundle-knowledge-space-README.md](bundle-knowledge-space-README.md) (resolver + ks namespace)
- [docs/sdk/bundle/bundle-dev-README.md](bundle-dev-README.md) (on_bundle_load + usage)
- Example bundle: `.../examples/bundles/kdcube.copilot@2026-04-03-19-05`

---

## References (code)

- Storage: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/storage/ai_bundle_storage.py`
- KV cache: `src/kdcube-ai-app/kdcube_ai_app/infra/service_hub/cache.py`
- Shared local storage helpers: `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_storage.py`
