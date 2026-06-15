# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
from __future__ import annotations

import logging
import mimetypes
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import quote

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    build_physical_artifact_path,
    split_logical_artifact_ref,
)
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore


LOGGER = logging.getLogger(__name__)


def _ref_namespace(ref: str) -> str:
    raw = str(ref or "").strip()
    head, sep, _tail = raw.partition(":")
    if not sep or not head:
        return ""
    return head.strip().lower()


def canonicalize_event_ref_for_context(ref: Any, *, conversation_id: str = "") -> str:
    """
    Return the durable canonical form for an event/object ref when the current
    runtime context can disambiguate it.

    `fi:turn_...` is valid inside the current ReAct runtime, but durable
    cross-surface consumers such as canvas need `fi:conv_<conversation>.turn_...`.
    React owns that rewrite because `fi:` is a React-owned file/event namespace.
    """
    value = str(ref or "").strip()
    if not value.startswith("fi:"):
        return value
    embedded_conversation_id, turn_id, namespace, relpath = split_logical_artifact_ref(value)
    if embedded_conversation_id or not conversation_id or not turn_id or not namespace or not relpath:
        return value
    from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import build_logical_artifact_path

    return build_logical_artifact_path(
        turn_id=turn_id,
        namespace=namespace,
        relpath=relpath,
        conversation_id=str(conversation_id or "").strip(),
    )


def _safe_storage_tails(*tails: str) -> list[str]:
    deduped: list[str] = []
    for tail in tails:
        safe_tail = str(PurePosixPath(str(tail or "").strip().lstrip("/")))
        if not safe_tail or safe_tail == "." or safe_tail.startswith("../") or "/../" in safe_tail:
            continue
        if safe_tail not in deduped:
            deduped.append(safe_tail)
    return deduped


def _owner_candidates(user_id: str) -> list[str]:
    user = str(user_id or "").strip()
    candidates = [user] if user else []
    candidates.extend(f"{role}/{user}" for role in ("registered", "anonymous", "privileged", "paid") if user)
    return list(dict.fromkeys(candidates))


def _guess_mime(filename: str) -> str:
    lower = str(filename or "").strip().lower()
    if lower.endswith((".md", ".markdown")):
        return "text/markdown"
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _fi_attachment_tail_from_storage_relpath(
    *,
    storage_relpath: str,
    turn_id: str,
    conversation_id: str,
    fallback: str,
) -> str:
    marker = f"/{conversation_id}/{turn_id}/"
    idx = str(storage_relpath or "").find(marker)
    if idx >= 0:
        tail = str(storage_relpath)[idx + len(marker):].strip().lstrip("/")
        if tail:
            return tail
    return fallback.strip().lstrip("/")


def _fi_download_url(
    *,
    tenant: str,
    project: str,
    user_id: str,
    conversation_id: str,
    turn_id: str,
    filename_path: str,
) -> str:
    return (
        f"/api/cb/resources/{quote(tenant, safe='')}/{quote(project, safe='')}"
        f"/conv/{quote(user_id, safe='')}/{quote(conversation_id, safe='')}"
        f"/turn/{quote(turn_id, safe='')}/attachment/{quote(filename_path, safe='/')}/download"
    )


async def read_event_ref_bytes(
    *,
    ref: str,
    tenant: str,
    project: str,
    user_id: str,
    storage_path: str | None = None,
    conversation_id: str = "",
) -> tuple[bytes, dict[str, str]]:
    """
    Resolve bytes for a namespaced event/object ref.

    For now the built-in byte-backed resolver is `fi:`, which is the canonical
    React-owned file/event reference. More namespaces should register here instead
    of teaching bundles their storage layouts.
    """
    namespace = _ref_namespace(ref)
    if namespace == "fi":
        return await _read_fi_bytes(
            ref=ref,
            tenant=tenant,
            project=project,
            user_id=user_id,
            storage_path=storage_path,
            conversation_id=conversation_id,
        )
    raise ValueError(f"no byte resolver registered for namespace: {namespace or '<none>'}")


async def resolve_event_ref_action(
    payload: Mapping[str, Any],
    *,
    tenant: str,
    project: str,
    user_id: str,
    storage_path: str | None = None,
    story_id: str = "",
    require_embedded_conversation: bool = False,
) -> dict[str, Any]:
    """
    Resolve an action against a canonical namespaced event/object reference.

    This is the generic resolver entry point used by bundles and, later, by SDK
    canvas-like components. Namespace implementations decide which actions are
    available. Unknown namespaces return an explicit unsupported result.
    """
    ref = str(
        payload.get("event_ref")
        or payload.get("object_ref")
        or payload.get("ref")
        or payload.get("logical_path")
        or ""
    ).strip()
    namespace = _ref_namespace(ref)
    if namespace == "fi":
        embedded_conversation_id, _turn_id, _artifact_namespace, _relpath = split_logical_artifact_ref(ref)
        if require_embedded_conversation and not embedded_conversation_id:
            action = str(payload.get("action") or "capabilities").strip().lower()
            return {
                "ok": False,
                "action": action,
                "ref": ref,
                "event_ref": ref,
                "object_ref": ref,
                "namespace": "fi",
                "resolver": "react.event_ref",
                "resolver_status": "invalid_ref",
                "error": "fi_ref_requires_embedded_conversation",
                "message": "Canvas fi: refs must include the cross-conversation prefix: fi:conv_<conversation_id>.turn_<turn_id>...",
                "status": 400,
            }
        return await _resolve_fi_action(
            payload,
            tenant=tenant,
            project=project,
            user_id=user_id,
            storage_path=storage_path,
            story_id=story_id,
        )
    return {
        "ok": False,
        "action": str(payload.get("action") or "capabilities").strip().lower(),
        "ref": ref,
        "event_ref": ref,
        "object_ref": ref,
        "namespace": namespace,
        "resolver_status": "unsupported_namespace",
        "error": "event_ref_resolver_not_registered",
        "status": 404,
    }


async def _read_fi_bytes(
    *,
    ref: str,
    tenant: str,
    project: str,
    user_id: str,
    storage_path: str | None = None,
    conversation_id: str = "",
) -> tuple[bytes, dict[str, str]]:
    embedded_conversation_id, turn_id, namespace, relpath = split_logical_artifact_ref(ref)
    source_conversation_id = embedded_conversation_id or str(conversation_id or "").strip()
    if not source_conversation_id or not turn_id or not namespace or not relpath:
        raise ValueError(f"invalid fi: artifact ref: {ref}")

    physical_tail = build_physical_artifact_path(turn_id=turn_id, namespace=namespace, relpath=relpath)
    tails = _safe_storage_tails(physical_tail, relpath)
    if not tails:
        raise FileNotFoundError(f"invalid fi: artifact path: {ref}")

    store = ConversationStore(storage_path or getattr(get_settings(), "STORAGE_PATH", None))
    base = f"cb/tenants/{tenant}/projects/{project}/attachments"
    for owner in _owner_candidates(user_id):
        for tail in tails:
            candidate = f"{base}/{owner}/{source_conversation_id}/{turn_id}/{tail}"
            try:
                data = await store.backend.read_bytes_a(candidate)
                return bytes(data or b""), {
                    "storage_relpath": candidate,
                    "conversation_id": source_conversation_id,
                    "turn_id": turn_id,
                    "namespace": namespace,
                    "relpath": relpath,
                }
            except FileNotFoundError:
                continue
            except Exception:
                LOGGER.debug("[react.event_ref.resolve] candidate failed ref=%s candidate=%s", ref, candidate, exc_info=True)
                continue
    raise FileNotFoundError(f"fi: artifact bytes not found for {ref}")


async def _resolve_fi_action(
    payload: Mapping[str, Any],
    *,
    tenant: str,
    project: str,
    user_id: str,
    storage_path: str | None = None,
    story_id: str = "",
) -> dict[str, Any]:
    ref = str(
        payload.get("event_ref")
        or payload.get("object_ref")
        or payload.get("ref")
        or payload.get("logical_path")
        or ""
    ).strip()
    action = str(payload.get("action") or "capabilities").strip().lower()
    mime_hint = str(payload.get("mime") or "").strip()
    base: dict[str, Any] = {
        "ok": True,
        "user_id": user_id,
        "story_id": story_id,
        "action": action,
        "ref": ref,
        "event_ref": ref,
        "object_ref": ref,
        "namespace": "fi",
        "resolver": "react.event_ref",
        "resolver_status": "implemented",
        "capabilities": {"preview": False, "open": False, "download": True, "rehost": False},
        "default_open_effect_action": "download",
    }
    if action in {"capabilities", "describe"}:
        return base
    if action != "download":
        return {**base, "ok": False, "error": "unsupported_fi_object_action", "status": 400}

    conversation_id, turn_id, artifact_namespace, relpath = split_logical_artifact_ref(ref)
    conversation_id = conversation_id or str(payload.get("conversation_id") or "").strip()
    if not conversation_id or not turn_id or not artifact_namespace or not relpath:
        return {**base, "ok": False, "error": "invalid_fi_ref", "status": 400}
    try:
        data, meta = await read_event_ref_bytes(
            ref=ref,
            tenant=tenant,
            project=project,
            user_id=user_id,
            storage_path=storage_path,
            conversation_id=conversation_id,
        )
    except Exception as exc:
        LOGGER.warning("[react.event_ref.resolve] failed action=%s ref=%s", action, ref, exc_info=True)
        return {**base, "ok": False, "error": "fi_ref_not_found", "message": str(exc), "status": 404}

    filename = PurePosixPath(relpath).name or "artifact"
    mime = mime_hint or _guess_mime(filename)
    filename_path = _fi_attachment_tail_from_storage_relpath(
        storage_relpath=str(meta.get("storage_relpath") or ""),
        conversation_id=str(meta.get("conversation_id") or conversation_id),
        turn_id=str(meta.get("turn_id") or turn_id),
        fallback=build_physical_artifact_path(turn_id=turn_id, namespace=artifact_namespace, relpath=relpath),
    )
    return {
        **base,
        "resolved": True,
        "conversation_id": meta.get("conversation_id") or conversation_id,
        "turn_id": meta.get("turn_id") or turn_id,
        "filename": filename,
        "mime": mime,
        "size": len(data),
        "storage_relpath": meta.get("storage_relpath") or "",
        "download_url": _fi_download_url(
            tenant=tenant,
            project=project,
            user_id=user_id,
            conversation_id=str(meta.get("conversation_id") or conversation_id),
            turn_id=str(meta.get("turn_id") or turn_id),
            filename_path=filename_path,
        ),
    }
