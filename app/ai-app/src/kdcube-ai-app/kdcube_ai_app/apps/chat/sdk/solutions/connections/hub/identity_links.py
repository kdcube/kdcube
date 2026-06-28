"""Identity-link storage and principal resolution helpers for connection-hub.

This module intentionally separates two concerns:

- identity links: external identity -> platform user id;
- principal/role resolution: platform user id -> roles/permissions.

The configured role resolver below is a development fixture. In the target
platform architecture, Connection Hub resolves identity and then calls a
platform principal/role resolver instead of deciding roles itself.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any, Mapping, Optional


def _now() -> int:
    return int(time.time())


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _identity_key(provider: str, provider_subject: str) -> str:
    return f"{provider}:{provider_subject}"


def _safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


class IdentityLinkStore:
    """Small JSON-backed identity-link store.

    The store is intentionally plain for this playground app. It can later move
    behind a platform service without changing the HTTP/API contract shape.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.path = self.root / "identity" / "identity-links.json"
        self.challenge_path = self.root / "identity" / "identity-link-challenges.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "links": {}}
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "links": {}}
        if not isinstance(parsed, dict):
            return {"version": 1, "links": {}}
        links = parsed.get("links")
        if not isinstance(links, dict):
            parsed["links"] = {}
        parsed.setdefault("version", 1)
        return parsed

    def _write(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def list_links(self, *, platform_user_id: Optional[str] = None) -> list[dict[str, Any]]:
        user = _clean(platform_user_id)
        data = self._read()
        links = data.get("links") if isinstance(data, dict) else {}
        out: list[dict[str, Any]] = []
        if isinstance(links, dict):
            for raw in links.values():
                row = _safe_mapping(raw)
                if user and _clean(row.get("platform_user_id")) != user:
                    continue
                out.append(row)
        out.sort(key=lambda row: (_clean(row.get("provider")), _clean(row.get("provider_subject"))))
        return out

    def upsert_link(
        self,
        *,
        provider: str,
        provider_subject: str,
        platform_user_id: str,
        label: str = "",
        created_by: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        status: str = "linked",
    ) -> dict[str, Any]:
        provider = _clean(provider)
        subject = _clean(provider_subject)
        user = _clean(platform_user_id)
        if not provider:
            raise ValueError("provider is required")
        if not subject:
            raise ValueError("provider_subject is required")
        if not user or user == "anonymous":
            raise ValueError("platform_user_id is required")

        data = self._read()
        links = data.setdefault("links", {})
        key = _identity_key(provider, subject)
        now = _now()
        previous = _safe_mapping(links.get(key)) if isinstance(links, dict) else {}
        previous_user = _clean(previous.get("platform_user_id"))
        if previous_user and previous_user != user:
            raise ValueError("identity is already linked to another platform user")
        row = {
            "provider": provider,
            "provider_subject": subject,
            "platform_user_id": user,
            "label": _clean(label) or previous.get("label") or subject,
            "status": _clean(status) or "linked",
            "verified_at": previous.get("verified_at") or now,
            "created_at": previous.get("created_at") or now,
            "updated_at": now,
            "created_by": _clean(created_by) or previous.get("created_by") or user,
            "metadata": _safe_mapping(metadata) if metadata is not None else _safe_mapping(previous.get("metadata")),
        }
        links[key] = row
        self._write(data)
        return row

    def remove_link(
        self,
        *,
        provider: str,
        provider_subject: str,
        platform_user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        provider = _clean(provider)
        subject = _clean(provider_subject)
        data = self._read()
        links = data.get("links") if isinstance(data, dict) else {}
        key = _identity_key(provider, subject)
        if not isinstance(links, dict) or key not in links:
            return {"ok": True, "removed": False}
        row = _safe_mapping(links.get(key))
        user = _clean(platform_user_id)
        if user and _clean(row.get("platform_user_id")) != user:
            return {"ok": False, "error": "identity_link_belongs_to_another_principal"}
        del links[key]
        self._write(data)
        return {"ok": True, "removed": True, "link": row}

    def resolve_link(self, *, provider: str, provider_subject: str) -> Optional[dict[str, Any]]:
        provider = _clean(provider)
        subject = _clean(provider_subject)
        data = self._read()
        links = data.get("links") if isinstance(data, dict) else {}
        if not isinstance(links, dict):
            return None
        row = links.get(_identity_key(provider, subject))
        return _safe_mapping(row) if isinstance(row, Mapping) else None

    def _read_challenges(self) -> dict[str, Any]:
        if not self.challenge_path.exists():
            return {"version": 1, "challenges": {}}
        try:
            parsed = json.loads(self.challenge_path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "challenges": {}}
        if not isinstance(parsed, dict):
            return {"version": 1, "challenges": {}}
        challenges = parsed.get("challenges")
        if not isinstance(challenges, dict):
            parsed["challenges"] = {}
        parsed.setdefault("version", 1)
        return parsed

    def _write_challenges(self, data: Mapping[str, Any]) -> None:
        self.challenge_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.challenge_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.challenge_path)

    def create_link_challenge(
        self,
        *,
        provider: str,
        platform_user_id: str,
        created_by: str,
        ttl_seconds: int = 600,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        provider = _clean(provider)
        user = _clean(platform_user_id)
        if not provider:
            raise ValueError("provider is required")
        if not user or user == "anonymous":
            raise ValueError("platform_user_id is required")
        now = _now()
        ttl = max(60, min(int(ttl_seconds or 600), 3600))
        challenge_id = secrets.token_urlsafe(24)
        row = {
            "challenge_id": challenge_id,
            "provider": provider,
            "platform_user_id": user,
            "status": "pending",
            "created_at": now,
            "expires_at": now + ttl,
            "created_by": _clean(created_by) or user,
            "metadata": _safe_mapping(metadata),
        }
        data = self._read_challenges()
        challenges = data.setdefault("challenges", {})
        challenges[challenge_id] = row
        self._write_challenges(data)
        return row

    def create_provider_claim_challenge(
        self,
        *,
        provider: str,
        provider_subject: str,
        label: str = "",
        created_by: str = "",
        ttl_seconds: int = 600,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        provider = _clean(provider)
        subject = _clean(provider_subject)
        if not provider:
            raise ValueError("provider is required")
        if not subject:
            raise ValueError("provider_subject is required")
        now = _now()
        ttl = max(60, min(int(ttl_seconds or 600), 3600))
        challenge_id = secrets.token_urlsafe(24)
        row = {
            "challenge_id": challenge_id,
            "provider": provider,
            "provider_subject": subject,
            "platform_user_id": "",
            "label": _clean(label) or subject,
            "status": "pending_platform_claim",
            "created_at": now,
            "expires_at": now + ttl,
            "created_by": _clean(created_by) or provider,
            "metadata": _safe_mapping(metadata),
        }
        data = self._read_challenges()
        challenges = data.setdefault("challenges", {})
        challenges[challenge_id] = row
        self._write_challenges(data)
        return row

    def get_link_challenge(self, *, challenge_id: str) -> Optional[dict[str, Any]]:
        cid = _clean(challenge_id)
        if not cid:
            return None
        data = self._read_challenges()
        challenges = data.get("challenges") if isinstance(data, dict) else {}
        if not isinstance(challenges, dict):
            return None
        row = challenges.get(cid)
        if not isinstance(row, Mapping):
            return None
        out = _safe_mapping(row)
        if out.get("status") in {"pending", "pending_platform_claim"} and int(out.get("expires_at") or 0) < _now():
            out["status"] = "expired"
            challenges[cid] = out
            self._write_challenges(data)
        return out

    def claim_provider_challenge(
        self,
        *,
        challenge_id: str,
        platform_user_id: str,
        claimed_by: str = "",
    ) -> dict[str, Any]:
        cid = _clean(challenge_id)
        user = _clean(platform_user_id)
        if not cid:
            raise ValueError("challenge_id is required")
        if not user or user == "anonymous":
            raise ValueError("platform_user_id is required")

        data = self._read_challenges()
        challenges = data.get("challenges") if isinstance(data, dict) else {}
        if not isinstance(challenges, dict) or cid not in challenges:
            return {"ok": False, "error": "identity_link_challenge_not_found"}
        challenge = _safe_mapping(challenges.get(cid))
        now = _now()
        status = _clean(challenge.get("status"))
        if status == "completed":
            if _clean(challenge.get("platform_user_id")) != user:
                return {"ok": False, "error": "identity_link_challenge_cross_user_access_denied", "challenge": challenge}
            link = self.resolve_link(
                provider=_clean(challenge.get("provider")),
                provider_subject=_clean(challenge.get("provider_subject")),
            )
            return {"ok": True, "challenge": challenge, "link": link}
        if status != "pending_platform_claim":
            return {"ok": False, "error": "identity_link_challenge_not_claimable", "challenge": challenge}
        if int(challenge.get("expires_at") or 0) < now:
            challenge["status"] = "expired"
            challenge["updated_at"] = now
            challenges[cid] = challenge
            self._write_challenges(data)
            return {"ok": False, "error": "identity_link_challenge_expired", "challenge": challenge}

        provider = _clean(challenge.get("provider"))
        subject = _clean(challenge.get("provider_subject"))
        if not provider or not subject:
            return {"ok": False, "error": "identity_link_challenge_missing_provider_identity", "challenge": challenge}
        try:
            link = self.upsert_link(
                provider=provider,
                provider_subject=subject,
                platform_user_id=user,
                label=_clean(challenge.get("label")) or subject,
                created_by=_clean(claimed_by) or user,
                metadata=_safe_mapping(challenge.get("metadata")),
            )
        except ValueError as exc:
            return {"ok": False, "error": "identity_link_conflict", "message": str(exc), "challenge": challenge}
        challenge.update(
            {
                "status": "completed",
                "platform_user_id": user,
                "claimed_at": now,
                "updated_at": now,
                "label": link.get("label") or subject,
            }
        )
        challenges[cid] = challenge
        self._write_challenges(data)
        return {"ok": True, "challenge": challenge, "link": link}

    def complete_link_challenge(
        self,
        *,
        challenge_id: str,
        provider: str,
        provider_subject: str,
        label: str = "",
        completed_by: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        cid = _clean(challenge_id)
        expected_provider = _clean(provider)
        subject = _clean(provider_subject)
        if not cid:
            raise ValueError("challenge_id is required")
        if not expected_provider:
            raise ValueError("provider is required")
        if not subject:
            raise ValueError("provider_subject is required")

        data = self._read_challenges()
        challenges = data.get("challenges") if isinstance(data, dict) else {}
        if not isinstance(challenges, dict) or cid not in challenges:
            return {"ok": False, "error": "identity_link_challenge_not_found"}
        challenge = _safe_mapping(challenges.get(cid))
        now = _now()
        if _clean(challenge.get("provider")) != expected_provider:
            return {"ok": False, "error": "identity_link_challenge_provider_mismatch", "challenge": challenge}
        if _clean(challenge.get("status")) != "pending":
            return {"ok": False, "error": "identity_link_challenge_not_pending", "challenge": challenge}
        if int(challenge.get("expires_at") or 0) < now:
            challenge["status"] = "expired"
            challenge["updated_at"] = now
            challenges[cid] = challenge
            self._write_challenges(data)
            return {"ok": False, "error": "identity_link_challenge_expired", "challenge": challenge}

        user = _clean(challenge.get("platform_user_id"))
        if not user or user == "anonymous":
            return {"ok": False, "error": "identity_link_challenge_missing_platform_user", "challenge": challenge}
        try:
            link = self.upsert_link(
                provider=expected_provider,
                provider_subject=subject,
                platform_user_id=user,
                label=_clean(label) or subject,
                created_by=_clean(completed_by) or "telegram",
                metadata=metadata,
            )
        except ValueError as exc:
            return {"ok": False, "error": "identity_link_conflict", "message": str(exc), "challenge": challenge}
        challenge.update(
            {
                "status": "completed",
                "completed_at": now,
                "updated_at": now,
                "provider_subject": subject,
                "label": link.get("label") or subject,
            }
        )
        challenges[cid] = challenge
        self._write_challenges(data)
        return {"ok": True, "challenge": challenge, "link": link}


def resolve_principal_roles(
    *,
    platform_user_id: str,
    identity_config: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Resolve a platform principal through the current configured fixture.

    The returned shape is deliberately compatible with a future platform
    resolver response: callers should treat it as resolver output, not as roles
    authored by this app.
    """

    user = _clean(platform_user_id)
    cfg = _safe_mapping(identity_config)
    role_resolver = _safe_mapping(cfg.get("role_resolver"))
    mode = _clean(role_resolver.get("mode")) or "platform"
    bindings = _safe_mapping(cfg.get("role_bindings"))
    binding = _safe_mapping(bindings.get(user))
    roles = [str(v) for v in binding.get("roles") or [] if str(v).strip()]
    permissions = [str(v) for v in binding.get("permissions") or [] if str(v).strip()]

    if mode == "configured":
        status = "resolved" if roles or permissions else "no_binding"
        source = "connection_hub.configured_role_bindings"
    elif mode in {"none", "disabled"}:
        status = "disabled"
        source = "connection_hub.role_resolver_disabled"
        roles = []
        permissions = []
    else:
        status = "platform_resolver_not_wired"
        source = "platform.principal_role_resolver"
        roles = []
        permissions = []

    return {
        "platform_user_id": user,
        "roles": roles,
        "permissions": permissions,
        "role_resolution": {
            "status": status,
            "source": source,
            "mode": mode,
            "note": (
                "Connection Hub resolved the identity. A platform principal/role "
                "resolver should own entitlement resolution."
            ),
        },
    }


__all__ = ["IdentityLinkStore", "resolve_principal_roles"]
