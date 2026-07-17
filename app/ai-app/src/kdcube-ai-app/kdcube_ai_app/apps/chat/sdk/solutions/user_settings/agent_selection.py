# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The per-user agent-selection settings records — a concrete store on the
generic user-settings core (``store.UserSettingsStore``).

The user-default record is keyed ``agent_selection:<agent_id>``. A
conversation's effective selection is keyed
``conversation:<conversation_id>:agent_selection:<agent_id>`` in the same
``user_bundle_props`` table. Conversation rows own the capability/model pick;
the user-default row supplies their initial value and owns the standing cache
policy. The value is a deny-list record:

    {
      "schema_version": 1,
      "disabled": {
        "tools": {"<alias>": true | ["<tool_name>", ...]},
        "mcp": {"<server_id>": true | ["<tool_name>", ...]},
        "named_services": {"<namespace>": true},
        "skills": ["<namespace>.<skill_id>", ...],
        "subagents": true
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
    match_instruction_profile,
    match_supported_model,
    normalize_instruction_pick,
    normalize_model_pick,
)
from kdcube_ai_app.apps.chat.sdk.solutions.user_settings.store import (
    UserSettingsStore,
    utc_now_iso,
)

AGENT_SELECTION_SUBSYSTEM = "agents"
AGENT_SELECTION_KEY_PREFIX = "agent_selection:"
AGENT_SELECTION_CONVERSATION_KEY_PREFIX = "conversation:"

# set_selection sentinels: "not in this patch" (None means CLEAR the pick).
_MODEL_UNSET = object()
_INSTRUCTIONS_UNSET = object()

_DICT_CATEGORIES = ("tools", "mcp", "named_services")


def agent_selection_key(agent_id: str, *, conversation_id: str = "") -> str:
    base = f"{AGENT_SELECTION_KEY_PREFIX}{str(agent_id or '').strip() or 'main'}"
    conversation = str(conversation_id or "").strip()
    if not conversation:
        return base
    return f"{AGENT_SELECTION_CONVERSATION_KEY_PREFIX}{conversation}:{base}"


def merge_selection_patch(
    current: Mapping[str, Any] | None,
    patch: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge a partial toggle patch over the current ``disabled`` record.

    Dict categories (tools / mcp / named_services): per-key toggles —
    ``true`` or a non-empty name list sets/replaces the denial, ``false`` /
    ``null`` / empty list removes it; keys absent from the patch keep their
    current state. Skills accept either a list (replaces the whole denied set)
    or a ``{skill_id: bool}`` mapping (per-skill toggles). ``subagents`` is
    one bare toggle: ``true`` denies delegation, ``false``/``null``
    re-enables it, absent keeps the stored state.
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

    # Tri-state: an explicit stored value is kept either way (True = opted
    # out, False = opted in — the latter matters when the admin default is
    # off); absent in both = no preference, the admin default decides.
    if "subagents" in patch:
        out["subagents"] = bool(patch.get("subagents"))
    elif isinstance(current, Mapping) and "subagents" in current:
        out["subagents"] = bool(current.get("subagents"))
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
    for source in (base, patch):
        if isinstance(source, Mapping) and "subagents" in source:
            out["subagents"] = bool(source.get("subagents"))
    return out


class UserAgentSelectionStore(UserSettingsStore):
    """Postgres-backed user-default and per-conversation agent selections."""

    @staticmethod
    def _empty_selection() -> dict[str, Any]:
        now = utc_now_iso()
        return {
            "schema_version": 1,
            "disabled": {},
            "model": None,
            "instructions": None,
            "cache_policy": {},
            "pending": None,
            "created_at": now,
            "updated_at": now,
        }

    @classmethod
    def _selection_from_record(cls, record: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
        if record is None:
            return None
        value = record.get("value")
        value = value if isinstance(value, Mapping) else {}
        disabled = value.get("disabled")
        cache_policy = value.get("cache_policy")
        pending = value.get("pending")
        return {
            "schema_version": 1,
            "disabled": dict(disabled) if isinstance(disabled, Mapping) else {},
            "model": normalize_model_pick(value.get("model")),
            "instructions": normalize_instruction_pick(value.get("instructions")),
            "cache_policy": dict(cache_policy) if isinstance(cache_policy, Mapping) else {},
            "pending": dict(pending) if isinstance(pending, Mapping) else None,
            "created_at": str(record.get("created_at") or ""),
            "updated_at": str(record.get("updated_at") or ""),
        }

    @staticmethod
    def _value_from_selection(
        selection: Mapping[str, Any],
        *,
        include_cache_policy: bool,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": 1,
            "disabled": dict(selection.get("disabled") or {}),
            "updated_at": utc_now_iso(),
        }
        model = normalize_model_pick(selection.get("model"))
        if model:
            value["model"] = model
        instructions = normalize_instruction_pick(selection.get("instructions"))
        if instructions:
            value["instructions"] = instructions
        if include_cache_policy and selection.get("cache_policy"):
            value["cache_policy"] = dict(selection.get("cache_policy") or {})
        pending = selection.get("pending")
        if isinstance(pending, Mapping) and pending:
            value["pending"] = dict(pending)
        return value

    async def _get_exact_selection(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        conversation_id: str = "",
    ) -> Optional[dict[str, Any]]:
        record = await self.get_record(
            user_id=user_id,
            bundle_id=bundle_id,
            subsystem=AGENT_SELECTION_SUBSYSTEM,
            key=agent_selection_key(agent_id, conversation_id=conversation_id),
        )
        return self._selection_from_record(record)

    async def _write_value(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        value: Mapping[str, Any],
        conversation_id: str = "",
    ) -> None:
        await self.put_record(
            user_id=user_id,
            bundle_id=bundle_id,
            subsystem=AGENT_SELECTION_SUBSYSTEM,
            key=agent_selection_key(agent_id, conversation_id=conversation_id),
            value=value,
        )

    async def _ensure_conversation_selection(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        conversation_id: str,
        default: Mapping[str, Any],
    ) -> dict[str, Any]:
        seed = {
            "schema_version": 1,
            "disabled": dict(default.get("disabled") or {}),
            "updated_at": utc_now_iso(),
        }
        model = normalize_model_pick(default.get("model"))
        if model:
            seed["model"] = model
        instructions = normalize_instruction_pick(default.get("instructions"))
        if instructions:
            seed["instructions"] = instructions
        await self.put_record_if_absent(
            user_id=user_id,
            bundle_id=bundle_id,
            subsystem=AGENT_SELECTION_SUBSYSTEM,
            key=agent_selection_key(agent_id, conversation_id=conversation_id),
            value=seed,
        )
        stored = await self._get_exact_selection(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )
        return stored or {
            **self._empty_selection(),
            "disabled": dict(default.get("disabled") or {}),
            "model": model,
            "instructions": instructions,
        }

    @staticmethod
    def _compose_effective_selection(
        default: Mapping[str, Any],
        scoped: Optional[Mapping[str, Any]],
        *,
        conversation_id: str,
    ) -> dict[str, Any]:
        active = scoped or default
        scoped_pending = scoped.get("pending") if isinstance(scoped, Mapping) else None
        default_pending = default.get("pending")
        if isinstance(scoped_pending, Mapping) and scoped_pending:
            pending = dict(scoped_pending)
            pending_scope = "conversation"
        elif isinstance(default_pending, Mapping) and default_pending:
            pending = dict(default_pending)
            pending_scope = "user_default"
        else:
            pending = None
            pending_scope = None
        conversation = str(conversation_id or "").strip()
        return {
            "schema_version": 1,
            "disabled": dict(active.get("disabled") or {}),
            "model": normalize_model_pick(active.get("model")),
            "instructions": normalize_instruction_pick(active.get("instructions")),
            "cache_policy": dict(default.get("cache_policy") or {}),
            "pending": pending,
            "pending_scope": pending_scope,
            "scope": {
                "kind": "conversation" if conversation else "user_default",
                "conversation_id": conversation,
                "inherited": bool(conversation and scoped is None),
            },
            "created_at": str(active.get("created_at") or ""),
            "updated_at": str(active.get("updated_at") or ""),
        }

    async def _promote_exact_pending(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        conversation_id: str,
        catalog: Optional[Mapping[str, Any]],
    ) -> Optional[dict[str, Any]]:
        stored = await self._get_exact_selection(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )
        if stored is None:
            return None
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
        merged_instructions = normalize_instruction_pick(stored.get("instructions"))
        if "instructions" in pending:
            if pending.get("instructions") is None:
                merged_instructions = None
            else:
                candidate_id = normalize_instruction_pick(pending.get("instructions"))
                if candidate_id and catalog is not None:
                    candidate_id = match_instruction_profile(candidate_id, catalog.get("instruction_profiles"))
                if candidate_id:
                    merged_instructions = candidate_id
        promoted = {
            **stored,
            "disabled": merged,
            "model": merged_model,
            "instructions": merged_instructions,
            "pending": None,
        }
        await self._write_value(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            value=self._value_from_selection(promoted, include_cache_policy=not conversation_id),
        )
        return promoted

    async def get_selection(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        conversation_id: str = "",
        materialize: bool = False,
    ) -> dict[str, Any]:
        """Resolve the selection for a conversation or the user default.

        A missing conversation row inherits the user default. ``materialize``
        freezes that inherited model/capability selection for the conversation
        with an insert-if-absent; standing cache policy remains on the default
        row. A due ``next_conversation`` default is promoted before seeding.
        """
        conversation = str(conversation_id or "").strip()
        default = await self._get_exact_selection(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
        ) or self._empty_selection()

        pending = default.get("pending")
        if conversation and isinstance(pending, Mapping):
            if (
                str(pending.get("apply") or "").strip().lower() == "next_conversation"
                and conversation != str(pending.get("since_conversation_id") or "").strip()
            ):
                default = await self._promote_exact_pending(
                    user_id=user_id,
                    bundle_id=bundle_id,
                    agent_id=agent_id,
                    conversation_id="",
                    catalog=None,
                ) or default

        if not conversation:
            return self._compose_effective_selection(default, None, conversation_id="")

        scoped = await self._get_exact_selection(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            conversation_id=conversation,
        )
        if scoped is None and materialize:
            scoped = await self._ensure_conversation_selection(
                user_id=user_id,
                bundle_id=bundle_id,
                agent_id=agent_id,
                conversation_id=conversation,
                default=default,
            )
        return self._compose_effective_selection(default, scoped, conversation_id=conversation)

    async def set_selection(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        patch: Mapping[str, Any] | None,
        model: Any = _MODEL_UNSET,
        instructions: Any = _INSTRUCTIONS_UNSET,
        cache_policy: Optional[Mapping[str, Any]] = None,
        apply: str = "now",
        conversation_id: str = "",
        catalog: Optional[Mapping[str, Any]] = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Merge one selection change into its explicit scope.

        ``now`` and ``when_cold`` target the supplied conversation (or the
        user default when no conversation is supplied). ``next_conversation``
        parks a delta on the user default, anchored to ``conversation_id``.
        ``cache_policy`` always merges into the user default.
        """
        conversation = str(conversation_id or "").strip()
        apply_mode = str(apply or "now").strip().lower()
        if apply_mode not in ("now", "next_conversation", "when_cold"):
            apply_mode = "now"

        default = await self._get_exact_selection(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
        ) or self._empty_selection()
        pending = default.get("pending")
        if conversation and isinstance(pending, Mapping):
            if (
                str(pending.get("apply") or "").strip().lower() == "next_conversation"
                and conversation != str(pending.get("since_conversation_id") or "").strip()
            ):
                default = await self._promote_exact_pending(
                    user_id=user_id,
                    bundle_id=bundle_id,
                    agent_id=agent_id,
                    conversation_id="",
                    catalog=catalog,
                ) or default

        merged_policy = dict(default.get("cache_policy") or {})
        if isinstance(cache_policy, Mapping):
            for klass, value_ in cache_policy.items():
                text = str(value_ or "").strip()
                if text:
                    merged_policy[str(klass)] = text
        default = {**default, "cache_policy": merged_policy}

        target_conversation = "" if apply_mode == "next_conversation" else conversation
        if target_conversation:
            current = await self._get_exact_selection(
                user_id=user_id,
                bundle_id=bundle_id,
                agent_id=agent_id,
                conversation_id=target_conversation,
            )
            if current is None:
                current = await self._ensure_conversation_selection(
                    user_id=user_id,
                    bundle_id=bundle_id,
                    agent_id=agent_id,
                    conversation_id=target_conversation,
                    default=default,
                )
        else:
            current = default

        if replace:
            current_disabled: Mapping[str, Any] = {}
            current_model: Any = None
            current_instructions: Any = None
            current_pending: Optional[dict[str, Any]] = None
        else:
            current_disabled = current.get("disabled") or {}
            current_model = current.get("model")
            current_instructions = current.get("instructions")
            current_pending = current.get("pending")

        if apply_mode in ("next_conversation", "when_cold"):
            deferred: dict[str, Any] = dict(current_pending or {})
            pending_patch = _merge_patch_over_patch(deferred.get("disabled"), patch)
            if pending_patch:
                deferred["disabled"] = pending_patch
            else:
                deferred.pop("disabled", None)
            if model is not _MODEL_UNSET:
                candidate = normalize_model_pick(model) if model is not None else None
                if model is None:
                    deferred["model"] = None
                elif candidate is not None:
                    if catalog is not None:
                        candidate = match_supported_model(candidate, catalog.get("supported_models"))
                    if candidate:
                        deferred["model"] = candidate
            if instructions is not _INSTRUCTIONS_UNSET:
                if instructions is None:
                    deferred["instructions"] = None
                else:
                    candidate_id = normalize_instruction_pick(instructions)
                    if candidate_id and catalog is not None:
                        candidate_id = match_instruction_profile(candidate_id, catalog.get("instruction_profiles"))
                    if candidate_id:
                        deferred["instructions"] = candidate_id
            deferred["apply"] = apply_mode
            deferred["since_conversation_id"] = conversation
            deferred.setdefault("created_at", utc_now_iso())
            changed = {
                **current,
                "disabled": dict(current_disabled),
                "model": normalize_model_pick(current_model),
                "instructions": normalize_instruction_pick(current_instructions),
                "pending": (
                    deferred
                    if "disabled" in deferred or "model" in deferred or "instructions" in deferred
                    else None
                ),
            }
        else:
            merged = merge_selection_patch(current_disabled, patch)
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
            merged_instructions = normalize_instruction_pick(current_instructions)
            if instructions is None:
                merged_instructions = None
            elif instructions is not _INSTRUCTIONS_UNSET:
                candidate_id = normalize_instruction_pick(instructions)
                if candidate_id and catalog is not None:
                    candidate_id = match_instruction_profile(candidate_id, catalog.get("instruction_profiles"))
                if candidate_id:
                    merged_instructions = candidate_id
            changed = {
                **current,
                "disabled": merged,
                "model": merged_model,
                "instructions": merged_instructions,
                "pending": current_pending,
            }

        if not target_conversation:
            changed["cache_policy"] = merged_policy
        await self._write_value(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            conversation_id=target_conversation,
            value=self._value_from_selection(changed, include_cache_policy=not target_conversation),
        )

        if target_conversation and isinstance(cache_policy, Mapping):
            await self._write_value(
                user_id=user_id,
                bundle_id=bundle_id,
                agent_id=agent_id,
                value=self._value_from_selection(default, include_cache_policy=True),
            )

        return await self.get_selection(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            conversation_id=conversation,
            materialize=bool(conversation),
        )

    async def promote_pending(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        conversation_id: str = "",
        catalog: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Promote the pending delta in the requested exact scope."""
        await self._promote_exact_pending(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            conversation_id=str(conversation_id or "").strip(),
            catalog=catalog,
        )
        return await self.get_selection(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            materialize=bool(str(conversation_id or "").strip()),
        )


__all__ = [
    "AGENT_SELECTION_CONVERSATION_KEY_PREFIX",
    "AGENT_SELECTION_KEY_PREFIX",
    "AGENT_SELECTION_SUBSYSTEM",
    "UserAgentSelectionStore",
    "agent_selection_key",
    "merge_selection_patch",
]
