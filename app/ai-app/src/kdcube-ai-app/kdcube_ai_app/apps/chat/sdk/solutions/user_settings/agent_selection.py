# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The per-user agent-selection settings record — a concrete store on the
generic user-settings core (``store.UserSettingsStore``).

One record per (user, REAL bundle_id, agent): ``subsystem='agents'``,
``key='agent_selection:<agent_id>'``. The value is a deny-list record:

    {
      "schema_version": 1,
      "disabled": {
        "tools": {"<alias>": true | ["<tool_name>", ...]},
        "mcp": {"<server_id>": true | ["<tool_name>", ...]},
        "named_services": {"<namespace>": true},
        "skills": ["<namespace>.<skill_id>", ...]
      },
      "model": {"provider": "<provider>", "model": "<model_id>"},
      "cache_policy": {"<change class>": "<policy>"},
      "pending": {...},
      "updated_at": "<iso>"
    }

Absent record = full configured set (nothing disabled). Writes are merge-writes
of partial toggles, clamped against the live inventory catalog when one is
provided, so the selection can only ever narrow the configured set.

``model`` is the one PICK in the record (a choice from the admin-declared
``supported_models`` list, applied to the strong decision role for the user's
turns): absent/None = the configured default; writes clamp against
``supported_models`` so a pick can never leave the admin-allowed list.

Catalog building, config narrowing, and the clamp itself are runtime concerns
and stay in ``kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory``; this
module owns the record's shape and persistence semantics.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
    clamp_selection,
    match_supported_model,
    normalize_model_pick,
)
from kdcube_ai_app.apps.chat.sdk.solutions.user_settings.store import (
    UserSettingsStore,
    utc_now_iso,
)

AGENT_SELECTION_SUBSYSTEM = "agents"
AGENT_SELECTION_KEY_PREFIX = "agent_selection:"

# set_selection sentinel: "model not in this patch" (None means CLEAR the pick).
_MODEL_UNSET = object()

_DICT_CATEGORIES = ("tools", "mcp", "named_services")


def agent_selection_key(agent_id: str) -> str:
    return f"{AGENT_SELECTION_KEY_PREFIX}{str(agent_id or '').strip() or 'main'}"


def merge_selection_patch(
    current: Mapping[str, Any] | None,
    patch: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge a partial toggle patch over the current ``disabled`` record.

    Dict categories (tools / mcp / named_services): per-key toggles —
    ``true`` or a non-empty name list sets/replaces the denial, ``false`` /
    ``null`` / empty list removes it; keys absent from the patch keep their
    current state. Skills accept either a list (replaces the whole denied set)
    or a ``{skill_id: bool}`` mapping (per-skill toggles).
    """
    out: dict[str, Any] = {}
    current = current or {}
    patch = patch or {}

    for category in _DICT_CATEGORIES:
        merged: dict[str, Any] = {}
        existing = current.get(category)
        if isinstance(existing, Mapping):
            for name, value in existing.items():
                name = str(name or "").strip()
                if name and value:
                    merged[name] = True if value is True else [str(v) for v in value]
        raw = patch.get(category)
        if isinstance(raw, Mapping):
            for name, value in raw.items():
                name = str(name or "").strip()
                if not name:
                    continue
                if value is True:
                    merged[name] = True
                elif isinstance(value, (list, tuple, set)):
                    names = [str(v or "").strip() for v in value if str(v or "").strip()]
                    if names:
                        merged[name] = names
                    else:
                        merged.pop(name, None)
                else:
                    # false / None / anything else: re-enable.
                    merged.pop(name, None)
        if merged:
            out[category] = merged

    skills: list[str] = []
    existing_skills = current.get("skills")
    if isinstance(existing_skills, (list, tuple)):
        skills = [str(s or "").strip() for s in existing_skills if str(s or "").strip()]
    raw_skills = patch.get("skills")
    if isinstance(raw_skills, Mapping):
        for skill_id, value in raw_skills.items():
            skill_id = str(skill_id or "").strip()
            if not skill_id:
                continue
            if value:
                if skill_id not in skills:
                    skills.append(skill_id)
            elif skill_id in skills:
                skills.remove(skill_id)
    elif isinstance(raw_skills, (list, tuple, set)):
        skills = [str(s or "").strip() for s in raw_skills if str(s or "").strip()]
    if skills:
        out["skills"] = skills
    return out


def _merge_patch_over_patch(
    base: Mapping[str, Any] | None,
    patch: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge two PATCH-form toggle payloads (later wins per key) so repeated
    deferred writes coalesce into one pending delta."""
    out: dict[str, Any] = {}
    for category in _DICT_CATEGORIES:
        merged: dict[str, Any] = {}
        for source in (base, patch):
            raw = source.get(category) if isinstance(source, Mapping) else None
            if isinstance(raw, Mapping):
                for name, value in raw.items():
                    name = str(name or "").strip()
                    if name:
                        merged[name] = value
        if merged:
            out[category] = merged
    skills: dict[str, Any] = {}
    for source in (base, patch):
        raw = source.get("skills") if isinstance(source, Mapping) else None
        if isinstance(raw, Mapping):
            for skill_id, value in raw.items():
                skill_id = str(skill_id or "").strip()
                if skill_id:
                    skills[skill_id] = bool(value)
        elif isinstance(raw, (list, tuple, set)):
            for skill_id in raw:
                skill_id = str(skill_id or "").strip()
                if skill_id:
                    skills[skill_id] = True
    if skills:
        out["skills"] = skills
    return out


class UserAgentSelectionStore(UserSettingsStore):
    """Postgres-backed per-user agent selection (deny-list) store.

    Rides the ``user_bundle_props`` table via the generic user-settings core;
    this class owns the agent-selection record semantics (merge, clamp,
    pending deltas)."""

    async def get_selection(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """The stored selection record; ``{}`` disabled when no row exists."""
        record = await self.get_record(
            user_id=user_id,
            bundle_id=bundle_id,
            subsystem=AGENT_SELECTION_SUBSYSTEM,
            key=agent_selection_key(agent_id),
        )
        if record is None:
            now = utc_now_iso()
            return {
                "schema_version": 1,
                "disabled": {},
                "model": None,
                "cache_policy": {},
                "pending": None,
                "created_at": now,
                "updated_at": now,
            }
        value = record["value"]
        disabled = value.get("disabled")
        model = value.get("model")
        cache_policy = value.get("cache_policy")
        pending = value.get("pending")
        return {
            "schema_version": 1,
            "disabled": dict(disabled) if isinstance(disabled, Mapping) else {},
            # Single PICK (absent/None = the configured default model), riding
            # the same record as the deny-list toggles.
            "model": normalize_model_pick(model),
            # The user's standing selection-change policy per delta class.
            "cache_policy": dict(cache_policy) if isinstance(cache_policy, Mapping) else {},
            # A deferred selection change awaiting its trigger.
            "pending": dict(pending) if isinstance(pending, Mapping) else None,
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }

    async def set_selection(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        patch: Mapping[str, Any] | None,
        model: Any = _MODEL_UNSET,
        cache_policy: Optional[Mapping[str, Any]] = None,
        apply: str = "now",
        conversation_id: str = "",
        catalog: Optional[Mapping[str, Any]] = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Merge-write a partial toggle patch (or replace the whole record).

        When ``catalog`` (the live inventory) is provided the merged result is
        clamped against it: anything outside the inventory is stripped, and
        system tool aliases are always stripped (locked on).

        ``model`` is the single model pick: omitted keeps the stored pick,
        ``None`` clears it (back to the configured default), a ``{provider,
        model}`` mapping sets it — clamped against the catalog's
        ``supported_models`` when the catalog is provided (an out-of-list pick
        keeps the stored value).

        ``apply`` is the user's cold-cache choice for THIS change: ``now``
        merges into the active selection (the default); ``next_conversation``
        or ``when_cold`` stores the change as a PENDING delta (active selection
        untouched) that the runtime promotes when its trigger fires.
        ``cache_policy`` merges the user's standing per-class policy (callers
        clamp it against the admin-allowed set first).
        """
        current: Mapping[str, Any] = {}
        current_model: Any = None
        current_policy: dict[str, Any] = {}
        current_pending: Optional[dict[str, Any]] = None
        if not replace:
            stored = await self.get_selection(
                user_id=user_id,
                bundle_id=bundle_id,
                agent_id=agent_id,
            )
            current = stored.get("disabled") or {}
            current_model = stored.get("model")
            current_policy = dict(stored.get("cache_policy") or {})
            current_pending = stored.get("pending")

        merged_policy = dict(current_policy)
        if isinstance(cache_policy, Mapping):
            for klass, value_ in cache_policy.items():
                text = str(value_ or "").strip()
                if text:
                    merged_policy[str(klass)] = text

        apply_mode = str(apply or "now").strip().lower()
        if apply_mode in ("next_conversation", "when_cold"):
            # Deferred change: the active selection stays; the delta parks in
            # `pending` until the runtime sees its trigger.
            pending: dict[str, Any] = dict(current_pending or {})
            pending_patch = _merge_patch_over_patch(pending.get("disabled"), patch)
            if pending_patch:
                pending["disabled"] = pending_patch
            elif "disabled" in pending and not pending_patch:
                pending.pop("disabled", None)
            if model is not _MODEL_UNSET:
                candidate = normalize_model_pick(model) if model is not None else None
                if model is None:
                    pending["model"] = None
                elif candidate is not None:
                    if catalog is not None:
                        candidate = match_supported_model(candidate, catalog.get("supported_models"))
                    if candidate:
                        pending["model"] = candidate
            pending["apply"] = apply_mode
            pending["since_conversation_id"] = str(conversation_id or "")
            pending.setdefault("created_at", utc_now_iso())
            now = utc_now_iso()
            value: dict[str, Any] = {"schema_version": 1, "disabled": dict(current), "updated_at": now}
            if normalize_model_pick(current_model):
                value["model"] = normalize_model_pick(current_model)
            if merged_policy:
                value["cache_policy"] = merged_policy
            if "disabled" in pending or "model" in pending:
                value["pending"] = pending
            await self._write_value(user_id=user_id, bundle_id=bundle_id, agent_id=agent_id, value=value)
            return {
                "schema_version": 1,
                "disabled": dict(current),
                "model": normalize_model_pick(current_model),
                "cache_policy": merged_policy,
                "pending": value.get("pending"),
                "updated_at": now,
            }

        merged = merge_selection_patch(current, patch)
        if catalog is not None:
            merged = clamp_selection(merged, catalog)

        merged_model = normalize_model_pick(current_model)
        if model is None:
            merged_model = None
        elif model is not _MODEL_UNSET:
            candidate = normalize_model_pick(model)
            if catalog is not None:
                candidate = match_supported_model(candidate, catalog.get("supported_models"))
            if candidate:
                merged_model = candidate

        now = utc_now_iso()
        value: dict[str, Any] = {"schema_version": 1, "disabled": merged, "updated_at": now}
        if merged_model:
            value["model"] = merged_model
        if merged_policy:
            value["cache_policy"] = merged_policy
        if isinstance(current_pending, Mapping) and current_pending:
            value["pending"] = dict(current_pending)
        await self._write_value(user_id=user_id, bundle_id=bundle_id, agent_id=agent_id, value=value)
        return {
            "schema_version": 1,
            "disabled": merged,
            "model": merged_model,
            "cache_policy": merged_policy,
            "pending": value.get("pending"),
            "updated_at": now,
        }

    async def _write_value(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        value: Mapping[str, Any],
    ) -> None:
        await self.put_record(
            user_id=user_id,
            bundle_id=bundle_id,
            subsystem=AGENT_SELECTION_SUBSYSTEM,
            key=agent_selection_key(agent_id),
            value=value,
        )

    async def promote_pending(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        catalog: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Merge the pending delta into the active selection and clear it.

        Called by the runtime when the pending trigger fires (new conversation
        / cold cache). Returns the updated record.
        """
        stored = await self.get_selection(user_id=user_id, bundle_id=bundle_id, agent_id=agent_id)
        pending = stored.get("pending")
        if not isinstance(pending, Mapping) or not pending:
            return stored
        merged = merge_selection_patch(stored.get("disabled") or {}, pending.get("disabled"))
        if catalog is not None:
            merged = clamp_selection(merged, catalog)
        merged_model = normalize_model_pick(stored.get("model"))
        if "model" in pending:
            candidate = normalize_model_pick(pending.get("model")) if pending.get("model") is not None else None
            if pending.get("model") is None:
                merged_model = None
            elif candidate is not None:
                if catalog is not None:
                    candidate = match_supported_model(candidate, catalog.get("supported_models"))
                if candidate:
                    merged_model = candidate
        now = utc_now_iso()
        value: dict[str, Any] = {"schema_version": 1, "disabled": merged, "updated_at": now}
        if merged_model:
            value["model"] = merged_model
        if stored.get("cache_policy"):
            value["cache_policy"] = dict(stored["cache_policy"])
        await self._write_value(user_id=user_id, bundle_id=bundle_id, agent_id=agent_id, value=value)
        return {
            "schema_version": 1,
            "disabled": merged,
            "model": merged_model,
            "cache_policy": dict(stored.get("cache_policy") or {}),
            "pending": None,
            "updated_at": now,
        }


__all__ = [
    "AGENT_SELECTION_KEY_PREFIX",
    "AGENT_SELECTION_SUBSYSTEM",
    "UserAgentSelectionStore",
    "agent_selection_key",
    "merge_selection_patch",
]
