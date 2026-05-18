from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Callable, Literal, Optional, Sequence

from pydantic import BaseModel, Field

from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import ChannelSpec, stream_with_channels
from kdcube_ai_app.infra.service_hub.errors import ServiceError, ServiceException
from kdcube_ai_app.infra.service_hub.inventory import (
    ModelServiceBase,
    create_cached_human_message,
    create_cached_system_message,
)

from .models import MemoryRecord, MemorySearchResult, normalize_status, normalize_terms, normalize_visibility


MemoryReconciliationActionType = Literal["merge", "squash", "weaken", "retire", "no_op"]


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _dump_model(value: BaseModel) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


class MemoryReconciliationCandidate(BaseModel):
    """Compact aggregate-row view sent to the reconciler agent."""

    id: str = Field(..., description="Memory id. Actions may reference only ids from this candidate set.")
    memory: str = Field(..., description="Current canonical memory text.")
    context: str = Field(default="", description="Short bounded context for the memory.")
    kind: str = Field(default="fact", description="Memory kind, for example fact, preference, habit, relationship.")
    status: str = Field(default="active", description="Current memory status.")
    visibility: str = Field(default="user", description="Current memory visibility.")
    labels: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    tier: int = Field(default=3)
    confidence_score: float = Field(default=0.5)
    importance_score: float = Field(default=0.5)
    salience_score: float = Field(default=0.5)
    confirmation_rate: float = Field(default=0.0)
    evidence_count: int = Field(default=0)
    update_count: int = Field(default=0)
    confirmation_count: int = Field(default=0)
    contradiction_count: int = Field(default=0)
    updated_at: str | None = Field(default=None)
    last_confirmed_at: str | None = Field(default=None)
    revision: int = Field(default=1)


class MemoryReconciliationAction(BaseModel):
    """One proposed maintenance action.

    The reconciler only proposes. The service layer validates scope and applies
    changes transactionally in Postgres.
    """

    action: MemoryReconciliationActionType
    source_memory_id: str | None = Field(default=None)
    source_memory_ids: list[str] = Field(default_factory=list)
    target_memory_id: str | None = Field(default=None)
    memory_id: str | None = Field(default=None)
    merged_memory: str | None = Field(default=None)
    merged_context: str | None = Field(default=None)
    merged_kind: str | None = Field(default=None)
    merged_labels: list[str] = Field(default_factory=list)
    merged_keywords: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")


class MemoryReconciliationOut(BaseModel):
    actions: list[MemoryReconciliationAction] = Field(default_factory=list)
    notes: str = Field(default="")
    warnings: list[str] = Field(default_factory=list)


def candidate_from_memory_record(
    record: MemoryRecord | MemorySearchResult | MemoryReconciliationCandidate | dict[str, Any],
    *,
    memory_max_chars: int = 700,
    context_max_chars: int = 500,
    max_terms: int = 12,
) -> MemoryReconciliationCandidate:
    """Build a bounded reconciler candidate from a search result or record."""

    if isinstance(record, MemoryReconciliationCandidate):
        return record
    if isinstance(record, MemorySearchResult):
        record = record.memory
    if is_dataclass(record):
        raw = asdict(record)
    elif isinstance(record, dict):
        raw = dict(record)
    else:
        raw = {
            "id": getattr(record, "id", ""),
            "memory": getattr(record, "memory", ""),
            "context": getattr(record, "context", ""),
            "kind": getattr(record, "kind", "fact"),
            "status": getattr(record, "status", "active"),
            "visibility": getattr(record, "visibility", "user"),
            "labels": getattr(record, "labels", []),
            "keywords": getattr(record, "keywords", []),
            "tier": getattr(record, "tier", 3),
            "confidence_score": getattr(record, "confidence_score", 0.5),
            "importance_score": getattr(record, "importance_score", 0.5),
            "salience_score": getattr(record, "salience_score", 0.5),
            "confirmation_rate": getattr(record, "confirmation_rate", 0.0),
            "evidence_count": getattr(record, "evidence_count", 0),
            "update_count": getattr(record, "update_count", 0),
            "confirmation_count": getattr(record, "confirmation_count", 0),
            "contradiction_count": getattr(record, "contradiction_count", 0),
            "updated_at": getattr(record, "updated_at", None),
            "last_confirmed_at": getattr(record, "last_confirmed_at", None),
            "revision": getattr(record, "revision", 1),
        }

    return MemoryReconciliationCandidate(
        id=str(raw.get("id") or "").strip(),
        memory=_clip(raw.get("memory"), memory_max_chars),
        context=_clip(raw.get("context"), context_max_chars),
        kind=str(raw.get("kind") or "fact").strip() or "fact",
        status=normalize_status(raw.get("status")),
        visibility=normalize_visibility(raw.get("visibility")),
        labels=normalize_terms(raw.get("labels"))[:max_terms],
        keywords=normalize_terms(raw.get("keywords"))[:max_terms],
        tier=int(raw.get("tier") or 3),
        confidence_score=float(raw.get("confidence_score") or 0.0),
        importance_score=float(raw.get("importance_score") or 0.0),
        salience_score=float(raw.get("salience_score") or 0.0),
        confirmation_rate=float(raw.get("confirmation_rate") or 0.0),
        evidence_count=int(raw.get("evidence_count") or 0),
        update_count=int(raw.get("update_count") or 0),
        confirmation_count=int(raw.get("confirmation_count") or 0),
        contradiction_count=int(raw.get("contradiction_count") or 0),
        updated_at=_iso(raw.get("updated_at")),
        last_confirmed_at=_iso(raw.get("last_confirmed_at")),
        revision=int(raw.get("revision") or 1),
    )


def build_reconciliation_system_prompt(*, max_actions: int = 12) -> str:
    return (
        "You are the user-memory reconciliation maintenance agent.\n"
        "You review a bounded set of aggregate memory records and propose safe maintenance actions.\n\n"
        "Output protocol is strict:\n"
        "<channel:thinking>short maintenance status only; no private reasoning</channel:thinking>\n"
        "<channel:output>{\"actions\":[...],\"notes\":\"...\"}</channel:output>\n\n"
        "Allowed actions:\n"
        "- merge: source_memory_id, target_memory_id, confidence, reason, optional merged_memory, merged_context, merged_labels, merged_keywords.\n"
        "- squash: source_memory_ids, target_memory_id, merged_memory, confidence, reason, optional merged_context, merged_labels, merged_keywords.\n"
        "- weaken: memory_id, confidence, reason.\n"
        "- retire: memory_id, confidence, reason.\n"
        "- no_op: confidence, reason.\n\n"
        "Rules:\n"
        "- Use only candidate ids shown in the input. Never invent ids.\n"
        "- Prefer no_op when uncertain.\n"
        "- Merge only when two memories describe the same durable fact/preference and are compatible.\n"
        "- Use squash instead of many pairwise merges when three or more compatible memories should become one target memory.\n"
        "- Pick the clearer, stronger, or more confirmed memory as the merge target.\n"
        "- If merging would otherwise lose useful non-conflicting details, include merged_memory with the complete replacement text for the target memory.\n"
        "- Squash must include merged_memory, and that text must preserve durable, compatible details from all source memories and the target without duplicating wording.\n"
        "- Weaken a memory when it is stale, unsupported, or partially contradicted but still useful for review.\n"
        "- Retire only when the memory is redundant, invalid, or no longer useful.\n"
        "- Do not create new memories; merge/squash may only update the chosen target memory and retire or merge sources into it.\n"
        f"- Return at most {max_actions} actions.\n"
        "- Keep reasons short and auditable.\n"
    )


def build_reconciliation_user_prompt(
    candidates: Sequence[MemoryReconciliationCandidate],
    *,
    reason: str = "",
) -> str:
    packet = {
        "reason": reason or "periodic memory maintenance",
        "candidate_count": len(candidates),
        "candidates": [_dump_model(candidate) for candidate in candidates],
    }
    return (
        "Review this bounded user-memory candidate packet. Return only allowed actions.\n\n"
        f"{json.dumps(packet, ensure_ascii=False, sort_keys=True)}"
    )


def parse_reconciliation_output(raw: str) -> MemoryReconciliationOut:
    raw = str(raw or "").strip()
    if not raw:
        return MemoryReconciliationOut(actions=[], notes="empty output")
    try:
        try:
            return MemoryReconciliationOut.model_validate_json(raw)
        except AttributeError:
            return MemoryReconciliationOut.parse_raw(raw)
    except Exception as exc:
        return MemoryReconciliationOut(actions=[], notes="failed to parse output", warnings=[str(exc)])


def validate_reconciliation_output(
    output: MemoryReconciliationOut,
    *,
    candidate_ids: Sequence[str],
    max_actions: int = 12,
    min_merge_confidence: float = 0.7,
) -> MemoryReconciliationOut:
    """Remove unsafe action proposals before an application phase can use them."""

    allowed_ids = {str(mid) for mid in candidate_ids if str(mid)}
    valid: list[MemoryReconciliationAction] = []
    warnings: list[str] = list(output.warnings or [])

    for idx, action in enumerate(list(output.actions or [])[: max(0, max_actions)]):
        kind = action.action
        reason = _clip(action.reason, 500)

        if kind == "no_op":
            valid.append(
                action.model_copy(update={"reason": reason})
                if hasattr(action, "model_copy")
                else action.copy(update={"reason": reason})
            )
            continue

        if kind == "merge":
            source_id = str(action.source_memory_id or "")
            target_id = str(action.target_memory_id or "")
            if not source_id or not target_id:
                warnings.append(f"action[{idx}] merge rejected: missing source or target id")
                continue
            if source_id == target_id:
                warnings.append(f"action[{idx}] merge rejected: source equals target")
                continue
            if source_id not in allowed_ids or target_id not in allowed_ids:
                warnings.append(f"action[{idx}] merge rejected: unknown memory id")
                continue
            if float(action.confidence or 0.0) < min_merge_confidence:
                warnings.append(f"action[{idx}] merge rejected: confidence below threshold")
                continue
            update = {
                "reason": reason,
                "merged_memory": _clip(action.merged_memory, 1200),
                "merged_context": _clip(action.merged_context, 800),
                "merged_kind": _clip(action.merged_kind, 80),
                "merged_labels": normalize_terms(action.merged_labels)[:12],
                "merged_keywords": normalize_terms(action.merged_keywords)[:12],
            }
            valid.append(
                action.model_copy(update=update)
                if hasattr(action, "model_copy")
                else action.copy(update=update)
            )
            continue

        if kind == "squash":
            target_id = str(action.target_memory_id or "")
            source_ids: list[str] = []
            seen: set[str] = set()
            for item in action.source_memory_ids or []:
                source_id = str(item or "").strip()
                if not source_id or source_id == target_id or source_id in seen:
                    continue
                seen.add(source_id)
                source_ids.append(source_id)
            if not target_id:
                warnings.append(f"action[{idx}] squash rejected: missing target id")
                continue
            if target_id not in allowed_ids:
                warnings.append(f"action[{idx}] squash rejected: unknown target id")
                continue
            unknown_sources = [source_id for source_id in source_ids if source_id not in allowed_ids]
            if unknown_sources:
                warnings.append(f"action[{idx}] squash rejected: unknown source ids")
                continue
            if not source_ids:
                warnings.append(f"action[{idx}] squash rejected: no source ids")
                continue
            if float(action.confidence or 0.0) < min_merge_confidence:
                warnings.append(f"action[{idx}] squash rejected: confidence below threshold")
                continue
            merged_memory = _clip(action.merged_memory, 1200)
            if not merged_memory:
                warnings.append(f"action[{idx}] squash rejected: missing merged_memory")
                continue
            update = {
                "reason": reason,
                "source_memory_ids": source_ids,
                "merged_memory": merged_memory,
                "merged_context": _clip(action.merged_context, 800),
                "merged_kind": _clip(action.merged_kind, 80),
                "merged_labels": normalize_terms(action.merged_labels)[:12],
                "merged_keywords": normalize_terms(action.merged_keywords)[:12],
            }
            valid.append(
                action.model_copy(update=update)
                if hasattr(action, "model_copy")
                else action.copy(update=update)
            )
            continue

        memory_id = str(action.memory_id or "")
        if kind in {"weaken", "retire"}:
            if not memory_id:
                warnings.append(f"action[{idx}] {kind} rejected: missing memory_id")
                continue
            if memory_id not in allowed_ids:
                warnings.append(f"action[{idx}] {kind} rejected: unknown memory id")
                continue
            valid.append(
                action.model_copy(update={"reason": reason})
                if hasattr(action, "model_copy")
                else action.copy(update={"reason": reason})
            )
            continue

        warnings.append(f"action[{idx}] rejected: unsupported action")

    if len(output.actions or []) > max_actions:
        warnings.append(f"truncated actions to max_actions={max_actions}")

    return MemoryReconciliationOut(actions=valid, notes=_clip(output.notes, 1000), warnings=warnings)


async def memory_reconciler_stream(
    svc: ModelServiceBase,
    *,
    candidates: Sequence[MemoryRecord | MemorySearchResult | MemoryReconciliationCandidate | dict[str, Any]],
    reason: str = "",
    role: str = "memory.reconciler",
    on_thinking_delta: Optional[Callable[..., Any]] = None,
    max_candidates: int = 40,
    max_actions: int = 12,
    max_tokens: int = 1800,
    temperature: float = 0.1,
) -> tuple[MemoryReconciliationOut, dict[str, str], dict[str, Any]]:
    """Run the versatile-streamer memory reconciler.

    Returns `(validated_output, channel_dump, meta)`. The returned output is a
    proposal only; callers must apply it in a separate transactional service.
    """

    compact_candidates = [
        candidate_from_memory_record(candidate)
        for candidate in list(candidates or [])[: max(0, int(max_candidates or 0))]
    ]
    compact_candidates = [candidate for candidate in compact_candidates if candidate.id and candidate.memory]
    candidate_ids = [candidate.id for candidate in compact_candidates]

    if not compact_candidates:
        output = MemoryReconciliationOut(
            actions=[MemoryReconciliationAction(action="no_op", confidence=1.0, reason="no candidates")],
            notes="Skipped memory reconciliation because there were no candidates.",
        )
        return output, {"thinking": "", "output": json.dumps(_dump_model(output), ensure_ascii=False)}, {"skipped": True}

    system_msg = create_cached_system_message(
        [{"text": build_reconciliation_system_prompt(max_actions=max_actions), "cache": True}]
    )
    human_msg = create_cached_human_message(
        [{"text": build_reconciliation_user_prompt(compact_candidates, reason=reason)}]
    )

    async def _emit(**kwargs: Any) -> None:
        channel = kwargs.pop("channel", None)
        if channel == "thinking" and on_thinking_delta:
            await on_thinking_delta(text=kwargs.get("text") or "", completed=kwargs.get("completed", False))

    channels = [
        ChannelSpec(name="thinking", format="text", replace_citations=False, emit_marker="thinking"),
        ChannelSpec(
            name="output",
            format="json",
            model=MemoryReconciliationOut,
            replace_citations=False,
            emit_marker="subsystem",
        ),
    ]

    results, meta = await stream_with_channels(
        svc,
        messages=[system_msg, human_msg],
        role=role,
        channels=channels,
        emit=_emit,
        agent=role,
        artifact_name="memory.reconciliation",
        max_tokens=max_tokens,
        temperature=temperature,
        return_full_raw=True,
    )

    service_error = (meta or {}).get("service_error")
    if service_error:
        raise ServiceException(ServiceError.model_validate(service_error))

    res = results.get("output")
    if res and res.obj and isinstance(res.obj, MemoryReconciliationOut):
        parsed = res.obj
    else:
        parsed = parse_reconciliation_output((res.raw if res else "") or "")

    validated = validate_reconciliation_output(
        parsed,
        candidate_ids=candidate_ids,
        max_actions=max_actions,
    )
    channel_dump = {
        "thinking": (results.get("thinking").raw if results.get("thinking") else "") or "",
        "output": (results.get("output").raw if results.get("output") else "") or "",
    }
    return validated, channel_dump, meta or {}
