"""Connection Hub identity-family resolver.

This module answers a different question than request authentication:

    "Given the current actor/platform user, which linked identities belong to
    the same person for product-level aggregation?"

The first consumer is user memories: a linked Telegram actor should be able to
see memories created under both the Telegram actor id and the platform user id,
without every app reimplementing provider-specific parsing.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .identity_links import IdentityLinkStore


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _identity_ref(provider: str, subject: str) -> str:
    provider = _clean(provider)
    subject = _clean(subject)
    return f"{provider}:{subject}" if provider and subject else ""


def actor_user_id_for_identity(provider: str, subject: str, *, metadata: Optional[Mapping[str, Any]] = None) -> str:
    """Return the canonical runtime actor user id for a provider identity.

    Telegram actors are already standardized in KDCube runtime as
    ``telegram_<id>``. For generic future providers, use the provider-subject
    ref form (`provider:subject`) until that provider registers a stronger
    convention.
    """

    provider = _clean(provider).lower()
    subject = _clean(subject)
    meta = _safe_mapping(metadata)
    explicit = _clean(meta.get("actor_user_id") or meta.get("user_id"))
    if explicit:
        return explicit
    if provider == "telegram" and subject:
        return f"telegram_{subject}"
    return _identity_ref(provider, subject)


def parse_actor_user_id(user_id: str) -> dict[str, str]:
    """Best-effort parse of a runtime user id into provider identity fields."""

    text = _clean(user_id)
    if not text:
        return {}
    if text.startswith("telegram_") and len(text) > len("telegram_"):
        return {
            "provider": "telegram",
            "provider_subject": text[len("telegram_"):],
            "identity_ref": _identity_ref("telegram", text[len("telegram_"):]),
        }
    if ":" in text:
        provider, subject = text.split(":", 1)
        provider = _clean(provider).lower()
        subject = _clean(subject)
        if provider and subject:
            return {
                "provider": provider,
                "provider_subject": subject,
                "identity_ref": _identity_ref(provider, subject),
            }
    return {}


def _link_metadata(link: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _safe_mapping(link.get("metadata"))
    return {
        "source": _clean(metadata.get("source")),
        "authority_id": _clean(metadata.get("authority_id")),
        "integration_id": _clean(metadata.get("connection_id") or metadata.get("integration_id")),
        "authenticator_id": _clean(metadata.get("selected_authenticator") or metadata.get("authenticator_id")),
    }


def _identity_from_link(link: Mapping[str, Any]) -> dict[str, Any]:
    provider = _clean(link.get("provider")).lower()
    subject = _clean(link.get("provider_subject"))
    metadata = _link_metadata(link)
    user_id = actor_user_id_for_identity(provider, subject, metadata=link.get("metadata"))
    return {
        "kind": "integration",
        "provider": provider,
        "provider_subject": subject,
        "identity_ref": _identity_ref(provider, subject),
        "user_id": user_id,
        "authority_id": metadata.get("authority_id") or "",
        "integration_id": metadata.get("integration_id") or "",
        "authenticator_id": metadata.get("authenticator_id") or "",
        "platform_user_id": _clean(link.get("platform_user_id")),
        "label": _clean(link.get("label")) or subject,
        "status": _clean(link.get("status")) or "linked",
        "source": metadata.get("source") or "identity_link",
    }


def _platform_identity(platform_user_id: str) -> dict[str, Any]:
    user = _clean(platform_user_id)
    return {
        "kind": "authority",
        "authority_id": "platform",
        "provider": "platform",
        "provider_subject": user,
        "identity_ref": _identity_ref("platform", user),
        "user_id": user,
        "platform_user_id": user,
        "label": "KDCube platform user",
        "status": "linked",
        "source": "platform",
    }


def resolve_identity_family(
    store: IdentityLinkStore,
    *,
    input_user_id: str = "",
    actor_user_id: str = "",
    platform_user_id: str = "",
) -> dict[str, Any]:
    """Resolve the linked identity family for a platform or actor user id."""

    requested_user_id = _clean(input_user_id or actor_user_id or platform_user_id)
    current_actor = _clean(actor_user_id or requested_user_id)
    current_platform = _clean(platform_user_id)
    parsed = parse_actor_user_id(requested_user_id)
    requested_link: dict[str, Any] = {}

    family_platform_user_id = current_platform
    if parsed:
        link = store.resolve_link(
            provider=parsed.get("provider", ""),
            provider_subject=parsed.get("provider_subject", ""),
        )
        requested_link = _safe_mapping(link)
        family_platform_user_id = _clean(requested_link.get("platform_user_id")) or family_platform_user_id
    elif requested_user_id and requested_user_id != "anonymous":
        family_platform_user_id = requested_user_id

    identities: list[dict[str, Any]] = []
    if family_platform_user_id:
        identities.append(_platform_identity(family_platform_user_id))
        for link in store.list_links(platform_user_id=family_platform_user_id):
            identities.append(_identity_from_link(link))
    elif parsed:
        identities.append({
            "kind": "integration",
            "provider": parsed.get("provider", ""),
            "provider_subject": parsed.get("provider_subject", ""),
            "identity_ref": parsed.get("identity_ref", ""),
            "user_id": actor_user_id_for_identity(parsed.get("provider", ""), parsed.get("provider_subject", "")),
            "platform_user_id": "",
            "label": parsed.get("provider_subject", ""),
            "status": "unlinked",
            "source": "actor_user_id",
        })

    user_ids = []
    seen: set[str] = set()
    for identity in identities:
        user_id = _clean(identity.get("user_id"))
        if user_id and user_id not in seen:
            seen.add(user_id)
            user_ids.append(user_id)

    return {
        "ok": True,
        "schema": "connection_hub.identity_family.v1",
        "input": {
            "user_id": requested_user_id,
            "actor_user_id": current_actor,
            "platform_user_id": current_platform,
            **({"provider": parsed.get("provider", ""), "provider_subject": parsed.get("provider_subject", "")} if parsed else {}),
        },
        "linked": bool(family_platform_user_id),
        "platform_user_id": family_platform_user_id,
        "authority": _platform_identity(family_platform_user_id) if family_platform_user_id else {},
        "identities": identities,
        "user_ids": user_ids,
        "memory_user_ids": list(user_ids),
        "requested_identity_link": requested_link,
    }


__all__ = [
    "actor_user_id_for_identity",
    "parse_actor_user_id",
    "resolve_identity_family",
]
