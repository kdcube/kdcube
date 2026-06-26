from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    MEMORY_RECONCILIATION_WORK_KIND,
    MemoryEntrypointMixin,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery import RedisNamedServiceDiscovery


class _RedisStub:
    def __init__(self):
        self.values = {}
        self.sets = {}
        self.deleted = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        return True

    async def sadd(self, key, *values):
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(values)
        return len(bucket) - before

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def expire(self, key, ttl):
        return True

    async def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)


class _FakeMemoryStore:
    def __init__(self):
        self.retired = []
        self.status_updates = []
        self.merges = []
        self.squashes = []
        self.edits = []
        self.memories = {}

    async def get_memory(self, **kwargs):
        return self.memories.get(kwargs.get("memory_id"))

    async def edit_memory(self, **kwargs):
        self.edits.append(kwargs)
        record = SimpleNamespace(
            id=kwargs.get("memory_id"),
            memory=kwargs.get("memory"),
            context=kwargs.get("context", ""),
            kind=kwargs.get("kind", "fact"),
            status=kwargs.get("status", "active"),
            visibility=kwargs.get("visibility", "user"),
            labels=kwargs.get("labels", []),
            keywords=kwargs.get("keywords", []),
            pinned=kwargs.get("pinned", False),
            importance_score=kwargs.get("importance", 0.7),
        )
        self.memories[record.id] = record
        return record

    async def retire_memory(self, **kwargs):
        self.retired.append(kwargs)
        return SimpleNamespace(id=kwargs.get("memory_id"))

    async def update_status(self, **kwargs):
        self.status_updates.append(kwargs)
        return SimpleNamespace(id=kwargs.get("memory_id"))

    async def merge_memories(self, **kwargs):
        self.merges.append(kwargs)
        return {
            "source": SimpleNamespace(id=kwargs.get("source_memory_id")),
            "target": self.memories.get(kwargs.get("target_memory_id")) or SimpleNamespace(id=kwargs.get("target_memory_id")),
        }

    async def squash_memories(self, **kwargs):
        self.squashes.append(kwargs)
        target_id = kwargs.get("target_memory_id")
        target = self.memories.get(target_id) or SimpleNamespace(id=target_id)
        target.memory = kwargs.get("merged_memory")
        target.context = kwargs.get("merged_context", "")
        self.memories[target_id] = target
        sources = [SimpleNamespace(id=item) for item in kwargs.get("source_memory_ids", [])]
        return {"source": sources[0] if sources else None, "sources": sources, "target": target, "skipped_sources": []}


class _MemoryStorageStub:
    def __init__(self, storage):
        self.storage = storage

    def delete(self, key):
        prefix = str(key or "")
        if prefix.endswith("/"):
            keys = [item for item in list(self.storage.keys()) if item.startswith(prefix)]
            for item in keys:
                self.storage.pop(item, None)
            return len(keys)
        existed = prefix in self.storage
        self.storage.pop(prefix, None)
        return 1 if existed else 0


class _MemoryHarness(MemoryEntrypointMixin):
    def __init__(self):
        self.storage = {}
        self.redis = _RedisStub()
        self.store = _FakeMemoryStore()
        self.comm_context = None
        self.pg_pool = object()
        self.settings = SimpleNamespace(TENANT="tenant-a", PROJECT="project-a")
        self.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="bundle@1"))
        self.bundle_props = {
            "surfaces": {
                "as_consumer": {
                    "agents": {
                        "main": {
                            "tools": [
                                {
                                    "kind": "named_service",
                                    "namespaces": {
                                        "mem": {
                                            "allowed": [
                                                "provider.about",
                                                "object.list",
                                                "object.search",
                                                "object.get",
                                                "object.schema",
                                                "object.upsert",
                                                "object.action",
                                                "object.delete",
                                            ]
                                        }
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }
        self.logger = SimpleNamespace(log=lambda *args, **kwargs: None)

    @property
    def configuration(self):
        return {
            "memory": {
                "enabled": True,
                "widget": {
                    "enabled": True,
                    "allow_all_user_memories": True,
                },
                "tools": {
                    "enabled": True,
                    "allow_write": False,
                    "default_scope_filter": "current_bundle",
                },
                "snapshots": {
                    "enabled": True,
                    "max_snapshots": 30,
                    "storage_prefix": "memory/snapshots",
                },
                "reconciliation": {
                    "enabled": True,
                    "max_jobs": 20,
                    "storage_prefix": "memory/reconciliation/jobs",
                },
            }
        }

    def _memory_scope(self):
        return SimpleNamespace(
            tenant="tenant-a",
            project="project-a",
            user_id="user-a",
            bundle_id="bundle@1",
        )

    def _memory_store(self):
        return self.store

    def _memory_named_service_store(self, ctx):
        return self.store

    def _memory_reconciliation_storage(self):
        return _MemoryStorageStub(self.storage)

    async def _memory_snapshot_create(self, **kwargs):
        snapshot_id = f"memsnap_safety_{len([key for key in self.storage if key.startswith('memory/snapshots/')])}"
        snapshot = {
            "snapshot_id": snapshot_id,
            "status": "succeeded",
            "scope": {
                "tenant": "tenant-a",
                "project": "project-a",
                "user_id": "user-a",
                "bundle_id": "bundle@1",
            },
            "scope_filter": kwargs.get("scope_filter", "current_bundle"),
            "memory_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "linked_job_id": kwargs.get("linked_job_id", ""),
            "artifacts": {
                "memories": {
                    "key": self._memory_snapshot_key(snapshot_id, "memories.json"),
                    "uri": f"mem://{self._memory_snapshot_key(snapshot_id, 'memories.json')}",
                    "mime": "application/json",
                }
            },
        }
        return await self._memory_snapshot_store(snapshot)

    async def _memory_reconciliation_write_text(self, key: str, content: str, *, mime: str = "text/plain") -> str:
        self.storage[key] = content
        return f"mem://{key}"

    async def _memory_reconciliation_read_text(self, key: str) -> str:
        return self.storage[key]


def _deep_update(target, updates):
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
    return target


def test_memory_mixin_exposes_mem_named_service_registry():
    harness = _MemoryHarness()

    registry = harness.named_services()
    entry = registry.resolve_namespace("mem")

    assert entry is not None
    assert entry.spec.provider_id == "sdk.memory"
    assert "mem" in entry.spec.namespaces
    assert {scope.namespace for scope in entry.spec.search_scopes} == {"mem"}
    assert entry.spec.object_kinds == ("memory.record",)


@pytest.mark.asyncio
async def test_memory_mixin_publishes_mem_named_service_discovery():
    harness = _MemoryHarness()
    await harness._register_memory_named_service_discovery()

    discovery = RedisNamedServiceDiscovery(harness.redis, tenant="tenant-a", project="project-a")
    entries = await discovery.providers(namespace="mem")

    assert len(entries) == 1
    assert entries[0].spec.provider_id == "sdk.memory"
    assert entries[0].endpoint["bundle_id"] == "bundle@1"
    assert entries[0].endpoint["registry_method"] == "named_services"
    assert {scope.namespace for scope in entries[0].spec.search_scopes} == {"mem"}
    assert entries[0].spec.object_kinds == ("memory.record",)


@pytest.mark.asyncio
async def test_snapshot_index_entries_keep_scope_for_authorized_listing():
    harness = _MemoryHarness()
    snapshot = {
        "snapshot_id": "memsnap_1",
        "status": "succeeded",
        "reason": "test",
        "scope": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-a",
            "bundle_id": "bundle@1",
        },
        "scope_filter": "all_user_memories",
        "memory_count": 2,
        "created_at": "2026-05-18T00:00:00+00:00",
        "artifacts": {},
    }

    await harness._memory_snapshot_store(snapshot)

    snapshots = await harness._memory_snapshot_load_index()
    assert len(snapshots) == 1
    assert snapshots[0]["scope"] == snapshot["scope"]
    assert harness._memory_snapshot_authorized(snapshots[0]) is True


@pytest.mark.asyncio
async def test_legacy_scope_less_snapshot_index_is_repaired_from_status():
    harness = _MemoryHarness()
    full_snapshot = {
        "snapshot_id": "memsnap_legacy",
        "status": "succeeded",
        "scope": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-a",
            "bundle_id": "bundle@1",
        },
        "scope_filter": "current_bundle",
        "memory_count": 1,
        "created_at": "2026-05-18T00:00:00+00:00",
        "artifacts": {},
    }
    harness.storage[harness._memory_snapshot_key("memsnap_legacy", "status.json")] = json.dumps(full_snapshot)
    harness.storage[harness._memory_snapshot_index_key()] = json.dumps(
        {
            "snapshots": [
                {
                    "snapshot_id": "memsnap_legacy",
                    "status": "succeeded",
                    "scope_filter": "current_bundle",
                }
            ]
        }
    )

    snapshots = await harness._memory_snapshot_load_index()

    assert snapshots[0]["scope"] == full_snapshot["scope"]
    repaired_index = json.loads(harness.storage[harness._memory_snapshot_index_key()])
    assert repaired_index["snapshots"][0]["scope"] == full_snapshot["scope"]


@pytest.mark.asyncio
async def test_snapshot_delete_requires_confirmation_and_removes_authorized_snapshot():
    harness = _MemoryHarness()
    snapshot = {
        "snapshot_id": "memsnap_delete",
        "status": "succeeded",
        "scope": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-a",
            "bundle_id": "bundle@1",
        },
        "scope_filter": "current_bundle",
        "memory_count": 1,
        "created_at": "2026-05-18T00:00:00+00:00",
        "artifacts": {},
    }
    await harness._memory_snapshot_store(snapshot)
    harness.storage[harness._memory_snapshot_key("memsnap_delete", "memories.json")] = "{}"

    rejected = await harness.memories_widget_snapshot_delete(snapshot_id="memsnap_delete", confirm=False)
    assert rejected["ok"] is False
    assert rejected["error"] == "memory_snapshot_delete_requires_confirmation"

    deleted = await harness.memories_widget_snapshot_delete(snapshot_id="memsnap_delete", confirm=True)

    assert deleted["ok"] is True
    assert deleted["snapshot_id"] == "memsnap_delete"
    assert await harness._memory_snapshot_load_index() == []
    assert harness._memory_snapshot_key("memsnap_delete", "status.json") not in harness.storage
    assert harness._memory_snapshot_key("memsnap_delete", "memories.json") not in harness.storage


@pytest.mark.asyncio
async def test_memory_background_completion_hook_fails_unfinished_job_and_releases_lock():
    harness = _MemoryHarness()
    job = {
        "job_id": "memrec_1",
        "status": "queued",
        "scope": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-a",
            "bundle_id": "bundle@1",
        },
        "scope_filter": "all_user_memories",
        "active_lock_key": "active-lock",
        "created_at": "2026-05-18T00:00:00+00:00",
    }
    harness.redis.values["active-lock"] = "memrec_1"
    await harness._memory_reconciliation_store_job(job)

    request = SimpleNamespace(
        payload={
            "work_kind": MEMORY_RECONCILIATION_WORK_KIND,
            "job_id": "memrec_1",
            "payload": {"job_id": "memrec_1"},
        }
    )
    context = SimpleNamespace(request=request)

    await harness.on_turn_completed(
        result={"ok": False, "handled": False, "error": {"code": "unsupported_job"}},
        status="completed",
        reason=None,
        comm_context=context,
        command="__kdcube_on_job__",
    )

    stored = json.loads(harness.storage[harness._memory_reconciliation_job_key("memrec_1", "status.json")])
    assert stored["status"] == "failed"
    assert stored["error"]["code"] == "unsupported_job"
    assert "active-lock" in harness.redis.deleted


@pytest.mark.asyncio
async def test_stale_active_reconciliation_job_no_longer_blocks_new_runs():
    harness = _MemoryHarness()
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    job = {
        "job_id": "memrec_stale",
        "status": "queued",
        "scope": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-a",
            "bundle_id": "bundle@1",
        },
        "scope_filter": "all_user_memories",
        "active_lock_key": "stale-lock",
        "created_at": old,
        "updated_at": old,
    }
    harness.redis.values["stale-lock"] = "memrec_stale"
    status_key = harness._memory_reconciliation_job_key("memrec_stale", "status.json")
    job["artifacts"] = {"status": {"key": status_key, "uri": f"mem://{status_key}", "mime": "application/json"}}
    harness.storage[status_key] = json.dumps(job)
    harness.storage[harness._memory_reconciliation_index_key()] = json.dumps(
        {"jobs": [job], "updated_at": old}
    )

    active = await harness._memory_reconciliation_active_job(scope_filter="all_user_memories")

    assert active is None
    stored = json.loads(harness.storage[harness._memory_reconciliation_job_key("memrec_stale", "status.json")])
    assert stored["status"] == "failed"
    assert stored["error"]["code"] == "memory_reconciliation_stale"
    assert "stale-lock" in harness.redis.deleted


@pytest.mark.asyncio
async def test_background_reconciliation_refreshes_bundle_props_before_enabled_check():
    class _RefreshingHarness(_MemoryHarness):
        def __init__(self):
            super().__init__()
            self.bundle_props = {}
            self.refresh_calls = []
            self.ran_job = False
            self._app_state = {
                "tenant": "tenant-a",
                "project": "project-a",
                "user": "user-a",
                "user_type": "privileged",
            }

        @property
        def configuration(self):
            config = json.loads(json.dumps(MemoryEntrypointMixin.memory_configuration_defaults(self)))
            return _deep_update(config, self.bundle_props)

        async def refresh_bundle_props(self, *, state, notify=False, reason=""):
            self.refresh_calls.append({"state": state, "notify": notify, "reason": reason})
            self.bundle_props = {
                "memory": {
                    "enabled": True,
                    "widget": {"enabled": True, "allow_all_user_memories": True},
                    "reconciliation": {"enabled": True},
                    "snapshots": {"enabled": True},
                }
            }
            return self.bundle_props

        async def _memory_reconciliation_run_job(
            self,
            *,
            job,
            scope_filter,
            limit,
            reason,
            agent_type,
            reconciliation_context,
        ):
            self.ran_job = True
            job = dict(job)
            job["status"] = "succeeded"
            job["scope_filter"] = scope_filter
            job["reason"] = reason
            job["agent_type"] = agent_type
            job["reconciliation_context"] = reconciliation_context
            await self._memory_reconciliation_store_job(job)

    harness = _RefreshingHarness()
    job = {
        "job_id": "memrec_refresh",
        "status": "queued",
        "scope": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-a",
            "bundle_id": "bundle@1",
        },
        "scope_filter": "all_user_memories",
        "reason": "test refresh",
        "created_at": "2026-05-18T00:00:00+00:00",
    }
    await harness._memory_reconciliation_store_job(job)

    result = await harness._memory_reconciliation_handle_background_job(
        envelope={},
        payload={"job_id": "memrec_refresh", "scope_filter": "all_user_memories"},
    )

    assert result["ok"] is True
    assert harness.ran_job is True
    assert harness.refresh_calls[0]["reason"] == "memory.reconciliation.background_job"


def test_reconciler_agent_type_selects_role_model_override():
    harness = _MemoryHarness()
    harness.config = SimpleNamespace(
        role_models={
            "memory.reconciler.regular": {"provider": "anthropic", "model": "sonnet-test"},
            "memory.reconciler.strong": {"provider": "anthropic", "model": "opus-test"},
        }
    )

    assert harness._memory_reconciler_agent_type("balanced") == "regular"
    assert harness._memory_reconciler_agent_type("STRONG") == "strong"
    assert harness._memory_reconciler_agent_type("unknown") == "regular"
    assert harness._memory_reconciler_role_override("strong") == {
        "memory.reconciler": {"provider": "anthropic", "model": "opus-test"}
    }


@pytest.mark.asyncio
async def test_reconciliation_request_hook_augments_context_without_new_api_fields():
    class _HookedHarness(_MemoryHarness):
        async def on_memory_reconciliation_request(self, *, request):
            return {
                "agent_type": "strong",
                "reconciliation_context": {
                    **request["reconciliation_context"],
                    "policy": "strict",
                },
            }

    harness = _HookedHarness()

    prepared = await harness._memory_prepare_reconciliation_request({
        "scope_filter": "all_user_memories",
        "limit": 50,
        "reason": "test",
        "agent_type": "lite",
        "reconciliation_context": {"source": "widget"},
    })

    assert prepared["agent_type"] == "strong"
    assert prepared["reconciliation_context"] == {"source": "widget", "policy": "strict"}


@pytest.mark.asyncio
async def test_reconciliation_apply_requires_confirmation():
    harness = _MemoryHarness()

    result = await harness.memories_widget_reconcile_apply(job_id="memrec_apply", confirm=False)

    assert result["ok"] is False
    assert result["error"] == "memory_reconciliation_apply_requires_confirmation"


@pytest.mark.asyncio
async def test_reconciliation_apply_mutates_from_succeeded_proposal_and_releases_lock():
    harness = _MemoryHarness()
    job = {
        "job_id": "memrec_apply",
        "status": "succeeded",
        "scope": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-a",
            "bundle_id": "bundle@1",
        },
        "scope_filter": "all_user_memories",
        "created_at": "2026-05-18T00:00:00+00:00",
        "active_lock_key": "apply-lock",
        "artifacts": {
            "proposal": {
                "key": "memory/reconciliation/jobs/memrec_apply/proposal.json",
                "uri": "mem://memory/reconciliation/jobs/memrec_apply/proposal.json",
                "mime": "application/json",
            }
        },
    }
    proposal = {
        "actions": [
            {"action": "retire", "memory_id": "memory-a", "reason": "redundant"},
            {"action": "weaken", "memory_id": "memory-b", "reason": "stale"},
            {
                "action": "merge",
                "source_memory_id": "memory-c",
                "target_memory_id": "memory-d",
                "reason": "same durable fact",
                "merged_memory": "The user has one son, Timur, born in 2009, who wears wide shoes.",
                "merged_context": "Merged from compatible family facts.",
                "merged_labels": ["family"],
                "merged_keywords": ["timur", "shoes"],
            },
            {"action": "no_op", "reason": "nothing else"},
        ]
    }
    harness.redis.values["apply-lock"] = "memrec_apply"
    harness.store.memories["memory-d"] = SimpleNamespace(
        id="memory-d",
        memory="The user has one son, Timur, born in 2009.",
        context="",
        kind="fact",
        status="active",
        visibility="user",
        labels=[],
        keywords=[],
        pinned=False,
        importance_score=0.8,
    )
    await harness._memory_reconciliation_store_job(job)
    await harness._memory_reconciliation_write_json("memory/reconciliation/jobs/memrec_apply/proposal.json", proposal)

    result = await harness.memories_widget_reconcile_apply(job_id="memrec_apply", confirm=True)

    assert result["ok"] is True
    assert result["job"]["status"] == "applied"
    assert result["apply_result"]["applied_count"] == 3
    assert result["apply_result"]["skipped_count"] == 1
    assert result["apply_result"]["safety_snapshot_id"].startswith("memsnap_safety_")
    assert harness.store.retired[0]["memory_id"] == "memory-a"
    assert harness.store.status_updates[0]["memory_id"] == "memory-b"
    assert harness.store.status_updates[0]["status"] == "weakened"
    assert harness.store.edits[0]["memory"] == "The user has one son, Timur, born in 2009, who wears wide shoes."
    assert harness.store.edits[0]["labels"] == ["family"]
    assert harness.store.merges[0]["source_memory_id"] == "memory-c"
    assert harness.store.merges[0]["target_memory_id"] == "memory-d"
    assert result["apply_result"]["results"][2]["target_rewritten"] is True
    assert "apply-lock" in harness.redis.deleted


@pytest.mark.asyncio
async def test_reconciliation_apply_squashes_group_into_single_target_rewrite():
    harness = _MemoryHarness()
    job = {
        "job_id": "memrec_squash",
        "status": "succeeded",
        "scope": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-a",
            "bundle_id": "bundle@1",
        },
        "scope_filter": "all_user_memories",
        "created_at": "2026-05-18T00:00:00+00:00",
        "artifacts": {
            "proposal": {
                "key": "memory/reconciliation/jobs/memrec_squash/proposal.json",
                "uri": "mem://memory/reconciliation/jobs/memrec_squash/proposal.json",
                "mime": "application/json",
            }
        },
    }
    proposal = {
        "actions": [
            {
                "action": "squash",
                "source_memory_ids": ["memory-a", "memory-b", "memory-a", "memory-target"],
                "target_memory_id": "memory-target",
                "reason": "same durable fact split across records",
                "confidence": 0.94,
                "merged_memory": "The user has one son, Timur, born in 2009, who wears wide shoes.",
                "merged_context": "Squashed from compatible family facts.",
                "merged_labels": ["family"],
                "merged_keywords": ["timur", "shoes"],
            }
        ]
    }
    harness.store.memories["memory-target"] = SimpleNamespace(
        id="memory-target",
        memory="The user has one son, Timur, born in 2009.",
        context="",
        kind="fact",
        status="active",
        visibility="user",
        labels=[],
        keywords=[],
        pinned=False,
        importance_score=0.8,
    )
    await harness._memory_reconciliation_store_job(job)
    await harness._memory_reconciliation_write_json("memory/reconciliation/jobs/memrec_squash/proposal.json", proposal)

    result = await harness.memories_widget_reconcile_apply(job_id="memrec_squash", confirm=True)

    assert result["ok"] is True
    assert result["apply_result"]["applied_count"] == 1
    assert result["apply_result"]["skipped_count"] == 0
    assert harness.store.squashes[0]["merged_memory"] == "The user has one son, Timur, born in 2009, who wears wide shoes."
    assert harness.store.squashes[0]["source_memory_ids"] == ["memory-a", "memory-b"]
    squash_result = result["apply_result"]["results"][0]
    assert squash_result["action"] == "squash"
    assert squash_result["source_memory_ids"] == ["memory-a", "memory-b"]
    assert squash_result["target_memory_id"] == "memory-target"
    assert squash_result["target_rewritten"] is True
