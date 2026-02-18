# KV Cache (Service Hub)

This module provides a platform‑level Redis KV cache abstraction used across the SDK.
It supports:
- raw KV cache (no namespacing)
- namespaced KV cache (optionally tenant/project‑scoped)

All caches are async and backed by Redis.

## Concepts

### KVCache
Raw key/value cache with optional TTL. No namespace or tenant/project prefixing.

### NamespacedKVCache
Wraps KVCache with a namespace prefix. It can optionally include tenant/project
prefixes (default behavior).

## When to use what

- **KVCache**: pass around as a generic cache in integrations / runtime payloads.
- **NamespacedKVCache**: use only at the call site where you need key isolation.

## API

### Create a raw cache
```
from kdcube_ai_app.infra.service_hub.cache import create_kv_cache

cache = create_kv_cache()
```

### Create a raw cache from env
```
from kdcube_ai_app.infra.service_hub.cache import create_kv_cache_from_env

cache = create_kv_cache_from_env()
```

### Create a namespaced cache (tenant/project scoped)
```
from kdcube_ai_app.infra.service_hub.cache import create_namespaced_kv_cache
from kdcube_ai_app.infra.namespaces import REDIS
from kdcube_ai_app.apps.chat.sdk.config import get_settings

settings = get_settings()
cache = create_namespaced_kv_cache(
    namespace=REDIS.CACHE.FAVICON,
    tenant=settings.TENANT,
    project=settings.PROJECT,
)
```

### Convert a KVCache into a namespaced cache
```
from kdcube_ai_app.infra.service_hub.cache import ensure_namespaced_cache
from kdcube_ai_app.infra.namespaces import REDIS

ns_cache = ensure_namespaced_cache(
    cache,
    namespace=REDIS.CACHE.FAVICON,
    tenant="t1",
    project="p1",
)
```

### Cross‑tenant / global cache
If you want a cache shared across all tenants/projects, disable prefixing:
```
from kdcube_ai_app.infra.service_hub.cache import ensure_namespaced_cache
from kdcube_ai_app.infra.namespaces import REDIS

ns_cache = ensure_namespaced_cache(
    cache,
    namespace=REDIS.CACHE.FAVICON,
    use_tp_prefix=False,  # no tenant/project prefix
)
```

## Env vars

Required:
- `REDIS_URL`

Optional:
- `KV_CACHE_TTL_SECONDS` (default: 3600)
- `FAVICON_CACHE_TTL_SECONDS` (default: 86400)

## Notes

- `NamespacedKVCache` derives from `KVCache` and only overrides `_key()`.
- The runtime should pass **KVCache** through integrations; convert to namespaced
  only where needed.
