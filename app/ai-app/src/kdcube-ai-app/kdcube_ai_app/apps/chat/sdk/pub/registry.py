# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/pub/registry.py
"""Tiered public content registry.

Two storage tiers, mirroring the platform's prepared-data pattern
(see docs/service/synch-mechanisms/critical-section-README.md):

- **Durable record store** — ``BundleArtifactStorage`` (local-fs locally, S3 on
  cloud). Source of truth: full item records plus a per-alias ``generation``
  marker bumped on every mutation.
- **Hot serving tier** — files under the shared app storage root (local disk
  locally, EFS on cloud): a bounded per-alias index plus mirrored item
  records. Crawler-facing reads (item page, sitemap) never touch the durable
  backend on the request path.

Two concurrency moments, guarded differently:

- **Moment A — load-time bootstrap/rebuild** (``ensure_hot_index``): many
  workers across many instances race on app load. Guarded by
  ``run_once_for_shared_bundle_storage`` — lock-free signature fast path,
  mkdir directory lock (atomic on shared mounts), heartbeat + TTL reap,
  signature written last. The signature derives from the durable generation,
  so a hot tier that matches the durable state is never rebuilt.
- **Moment B — runtime publish/update/retract** (``publish``/``retract``):
  serialized by an observed file lock on the shared hot tier (every writer
  shares the mount), holding the durable write, generation bump, and hot-tier
  update in one critical section. Reads stay lock-free: hot files are replaced
  atomically, so a torn read is impossible.

Retracted items keep their record (``state=retracted``) so the serving layer
can answer 410 rather than 404.

Every filesystem/backend byte moved here goes through ``asyncio.to_thread`` —
a blocked event loop starves the Moment-A lock heartbeat and manifests as a
duplicate builder.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import time
from typing import Any, Awaitable, Callable, List, Optional

from kdcube_ai_app.apps.chat.sdk.pub.model import (
    PublicContentAliasIndex,
    PublicContentIndexEntry,
    PublicContentItem,
    normalize_slug_path,
    utc_now_iso,
)
from kdcube_ai_app.apps.chat.sdk.storage.bundle_artifact_storage import BundleArtifactStorage
from kdcube_ai_app.infra.plugin.bundle_once import run_once_for_shared_bundle_storage
from kdcube_ai_app.storage.observed_file_locks import observed_file_lock_async

_log = logging.getLogger("kdcube.sdk.pub.registry")

# Notification hook fired after a successful mutation. It is a notification
# only: the durable registry/generation marker stays authoritative, and
# consumers must tolerate missed notifications by resyncing from durable
# records (Data Bus events are not a delivery log).
ChangeNotifier = Callable[[str, PublicContentItem], Awaitable[None]]

_DURABLE_PREFIX = "public_content"
_HOT_DIRNAME = "_public_content"
_MUTATE_LOCK_WAIT_SECONDS = float(os.environ.get("KDCUBE_PUBLIC_CONTENT_MUTATE_LOCK_WAIT_SECONDS", "60") or "60")
_REBUILD_LOCK_WAIT_SECONDS = float(os.environ.get("KDCUBE_PUBLIC_CONTENT_REBUILD_LOCK_WAIT_SECONDS", "300") or "300")


def _atomic_write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _read_text(path: pathlib.Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


class PublicContentRegistry:
    """Registry for one app's public content alias."""

    def __init__(
        self,
        *,
        alias: str,
        durable: BundleArtifactStorage,
        hot_root: pathlib.Path | str,
        logger: Optional[Any] = None,
        notifier: Optional[ChangeNotifier] = None,
    ) -> None:
        self.alias = str(alias or "").strip().lower()
        if not self.alias:
            raise ValueError("public content alias is required")
        self.durable = durable
        self.hot_root = pathlib.Path(hot_root)
        self.logger = logger
        self.notifier = notifier

    # ------------------ path/key layout ------------------

    def _durable_item_key(self, slug: str) -> str:
        return f"{_DURABLE_PREFIX}/{self.alias}/items/{slug}.json"

    def _durable_items_prefix(self) -> str:
        return f"{_DURABLE_PREFIX}/{self.alias}/items/"

    def _durable_generation_key(self) -> str:
        return f"{_DURABLE_PREFIX}/{self.alias}/generation.json"

    @property
    def hot_alias_dir(self) -> pathlib.Path:
        return self.hot_root / _HOT_DIRNAME / self.alias

    @property
    def hot_index_path(self) -> pathlib.Path:
        return self.hot_alias_dir / "index.json"

    def _hot_item_path(self, slug: str) -> pathlib.Path:
        return self.hot_alias_dir / "items" / f"{slug}.json"

    @property
    def _signature_path(self) -> pathlib.Path:
        return self.hot_alias_dir / ".index.signature"

    @property
    def _mutate_lock_path(self) -> pathlib.Path:
        return self.hot_alias_dir / ".mutate.lock"

    # ------------------ durable tier (all off-loop) ------------------

    def _durable_read_generation_sync(self) -> int:
        try:
            raw = self.durable.read(self._durable_generation_key(), as_text=True)
            return int((json.loads(raw) or {}).get("generation") or 0)
        except Exception:
            return 0

    def _durable_write_generation_sync(self, generation: int) -> None:
        payload = json.dumps({"generation": int(generation), "updated_at": utc_now_iso()})
        self.durable.write(self._durable_generation_key(), payload, mime="application/json")

    def _durable_read_item_sync(self, slug: str) -> Optional[PublicContentItem]:
        try:
            raw = self.durable.read(self._durable_item_key(slug), as_text=True)
        except Exception:
            return None
        try:
            return PublicContentItem.model_validate_json(raw)
        except Exception:
            self._log(f"[pub.registry] corrupt durable record alias={self.alias} slug={slug}", "ERROR")
            return None

    def _durable_write_item_sync(self, item: PublicContentItem) -> None:
        self.durable.write(
            self._durable_item_key(item.slug),
            item.model_dump_json(),
            mime="application/json",
        )

    def _durable_list_slugs_sync(self) -> List[str]:
        """Walk the durable items tree; ``list()`` returns immediate children
        only, and slugs are slash-joined paths, so recurse (bounded depth)."""
        base = self._durable_items_prefix()
        slugs: List[str] = []
        max_depth = 8

        def _walk(rel_prefix: str, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                names = self.durable.list(f"{base}{rel_prefix}")
            except Exception:
                return
            for name in names:
                child = str(name or "").strip("/")
                if not child:
                    continue
                rel = f"{rel_prefix}{child}"
                if child.endswith(".json"):
                    slugs.append(rel[: -len(".json")])
                else:
                    _walk(f"{rel}/", depth + 1)

        _walk("", 0)
        return sorted(set(slugs))

    # ------------------ hot tier (all off-loop) ------------------

    def _hot_read_index_sync(self) -> Optional[PublicContentAliasIndex]:
        raw = _read_text(self.hot_index_path)
        if raw is None:
            return None
        try:
            return PublicContentAliasIndex.model_validate_json(raw)
        except Exception:
            self._log(f"[pub.registry] corrupt hot index alias={self.alias}; treating as missing", "WARNING")
            return None

    def _hot_write_index_sync(self, index: PublicContentAliasIndex) -> None:
        index.updated_at = utc_now_iso()
        _atomic_write_text(self.hot_index_path, index.model_dump_json())

    def _hot_read_item_sync(self, slug: str) -> Optional[PublicContentItem]:
        raw = _read_text(self._hot_item_path(slug))
        if raw is None:
            return None
        try:
            return PublicContentItem.model_validate_json(raw)
        except Exception:
            return None

    def _hot_write_item_sync(self, item: PublicContentItem) -> None:
        _atomic_write_text(self._hot_item_path(item.slug), item.model_dump_json())

    def _hot_write_signature_sync(self, generation: int) -> None:
        # Same "<content>\n" shape bundle_once._write_signature produces, so
        # Moment-A checks and Moment-B updates agree on the format.
        _atomic_write_text(self._signature_path, f"gen:{int(generation)}\n")

    # ------------------ reads (lock-free) ------------------

    async def read_index(self) -> Optional[PublicContentAliasIndex]:
        """Read the hot per-alias index. Lock-free; None when not built yet."""
        return await asyncio.to_thread(self._hot_read_index_sync)

    async def get_item(self, slug: str) -> Optional[PublicContentItem]:
        """Read one item, hot tier first, durable fallback (with hot refill)."""
        slug = normalize_slug_path(slug)
        item = await asyncio.to_thread(self._hot_read_item_sync, slug)
        if item is not None:
            return item
        item = await asyncio.to_thread(self._durable_read_item_sync, slug)
        if item is not None:
            # Best-effort refill; readers never take the mutation lock.
            try:
                await asyncio.to_thread(self._hot_write_item_sync, item)
            except Exception:
                pass
        return item

    # ------------------ Moment B: runtime mutation ------------------

    async def publish(self, item: PublicContentItem) -> PublicContentItem:
        """Publish or update one item. Serialized; bumps the generation."""
        if item.alias != self.alias:
            raise ValueError(f"item alias {item.alias!r} does not match registry alias {self.alias!r}")
        published = item.model_copy(update={"state": "published"})
        return await self._mutate("publish", published)

    async def update(self, item: PublicContentItem) -> PublicContentItem:
        """Alias of :meth:`publish` with an explicit lastmod bump."""
        bumped = item.model_copy(update={"lastmod": utc_now_iso(), "state": "published"})
        return await self._mutate("update", bumped)

    async def retract(self, slug: str) -> Optional[PublicContentItem]:
        """Retract one item. The record is kept so serving can answer 410."""
        slug = normalize_slug_path(slug)
        current = await asyncio.to_thread(self._durable_read_item_sync, slug)
        if current is None:
            return None
        retracted = current.model_copy(update={"state": "retracted", "lastmod": utc_now_iso()})
        return await self._mutate("retract", retracted)

    async def _mutate(self, op: str, item: PublicContentItem) -> PublicContentItem:
        """One serialized mutation: durable write -> generation bump -> hot update.

        The observed file lock lives on the shared hot tier, which every
        writer mounts (local volume or EFS), so it serializes publishers
        across workers and instances. Readers do not take it.
        """
        self.hot_alias_dir.mkdir(parents=True, exist_ok=True)
        async with observed_file_lock_async(
            lock_path=self._mutate_lock_path,
            resource_id=f"public-content:{self.alias}",
            operation=f"public-content.{op}",
            wait_seconds=_MUTATE_LOCK_WAIT_SECONDS,
        ):
            def _apply_sync() -> int:
                self._durable_write_item_sync(item)
                generation = self._durable_read_generation_sync() + 1
                self._durable_write_generation_sync(generation)
                self._hot_write_item_sync(item)
                index = self._hot_read_index_sync() or PublicContentAliasIndex(alias=self.alias)
                index.upsert(
                    PublicContentIndexEntry(
                        slug=item.slug,
                        title=item.title,
                        lastmod=item.lastmod,
                        published_at=item.published_at,
                        state=item.state,
                    )
                )
                index.generation = generation
                self._hot_write_index_sync(index)
                self._hot_write_signature_sync(generation)
                return generation

            generation = await asyncio.to_thread(_apply_sync)

        self._log(
            f"[pub.registry] {op} alias={self.alias} slug={item.slug} state={item.state} generation={generation}",
            "INFO",
        )
        if self.notifier is not None:
            try:
                await self.notifier(op, item)
            except Exception:
                self._log(f"[pub.registry] change notifier failed for {op} {item.slug} (ignored)", "WARNING")
        return item

    # ------------------ Moment A: load-time bootstrap/rebuild ------------------

    async def ensure_hot_index(self) -> None:
        """Bring the hot tier current with the durable store, once per fleet.

        Called from app load. Many workers across many instances race here;
        the once-helper gives a lock-free signature fast path, so on a warm
        tier this costs one durable generation read and one file read.
        """
        generation = await asyncio.to_thread(self._durable_read_generation_sync)
        signature = f"gen:{int(generation)}"

        async def _rebuild() -> None:
            def _rebuild_sync() -> None:
                index = PublicContentAliasIndex(alias=self.alias, generation=generation)
                for slug in self._durable_list_slugs_sync():
                    record = self._durable_read_item_sync(slug)
                    if record is None:
                        continue
                    self._hot_write_item_sync(record)
                    index.upsert(
                        PublicContentIndexEntry(
                            slug=record.slug,
                            title=record.title,
                            lastmod=record.lastmod,
                            published_at=record.published_at,
                            state=record.state,
                        )
                    )
                self._hot_write_index_sync(index)

            await asyncio.to_thread(_rebuild_sync)

        await run_once_for_shared_bundle_storage(
            storage_root=self.hot_alias_dir,
            operation=f"public-content-index-{self.alias}",
            signature_path=self._signature_path,
            signature=signature,
            ready=lambda: self.hot_index_path.exists(),
            action=_rebuild,
            logger=self.logger,
            owner_metadata={"alias": self.alias, "kind": "public-content-index"},
            lock_wait_seconds=_REBUILD_LOCK_WAIT_SECONDS,
            # Serving a seconds-stale index while another worker rebuilds is
            # better than failing app load; publish paths do not rely on this.
            allow_existing_while_locked=True,
            allow_existing_on_timeout=True,
            log_prefix="[pub.index]",
        )

    # ------------------ util ------------------

    def _log(self, message: str, level: str = "INFO") -> None:
        logger = self.logger
        if logger is not None:
            log_fn = getattr(logger, "log", None)
            if callable(log_fn):
                try:
                    log_fn(message, level)
                    return
                except TypeError:
                    pass
        getattr(_log, level.lower(), _log.info)(message)
