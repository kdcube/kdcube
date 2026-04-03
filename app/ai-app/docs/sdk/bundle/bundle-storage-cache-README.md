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
