# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Process-local discovery metadata for examples baked into the platform image.

The entrypoint path names immutable image/shared-copy source. A failed import
also has a stable ``None`` result in that image: retrying it during every
registry read repeatedly compiles processor-only dependencies in ingress.
Only the declared ID is retained, never a registry, allowlist, properties,
actor or authority. Callers still check current existence and admission.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import RLock


_DISCOVERY_LOCK = RLock()


@lru_cache(maxsize=256)
def _discover(entrypoint: str) -> str | None:
    from kdcube_ai_app.infra.plugin.bundle_loader import get_declared_bundle_id

    return get_declared_bundle_id(Path(entrypoint).parent, "entrypoint")


def declared_example_bundle_id(path: Path) -> str | None:
    """Resolve one immutable example once, including an undeclared/unloadable ID.

    The lock makes cold discovery single-flight across registry-reader threads.
    The bounded LRU can rediscover an evicted path; the image's active examples
    fit within its 256-entry bound. Changes to image source use new processes
    or content-addressed shared paths, not a cached mutable registry snapshot.
    """
    entrypoint = str((Path(path) / "entrypoint.py").resolve())
    with _DISCOVERY_LOCK:
        return _discover(entrypoint)


def clear_example_discovery_cache() -> None:
    """Explicit discovery reset for tests or a process-local image refresh."""
    with _DISCOVERY_LOCK:
        _discover.cache_clear()
