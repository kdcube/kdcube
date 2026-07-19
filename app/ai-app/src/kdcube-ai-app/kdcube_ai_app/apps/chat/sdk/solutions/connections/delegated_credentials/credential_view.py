# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The one canonical reader of a request's delegated credential.

A delegated bearer's authority is a ``CredentialEnvelope`` whose ``.attrs``
carry the grant facts. The guard stashes it on the request as
``request.state.delegated_credential = {credential, grant_record}`` — the same
facts appear in the credential's attrs AND (a copy) inside ``grant_record``,
and the resource lives in two forms: a single ``resource`` string (OAuth
single-resource clients) or a ``resource_grants`` map (agent clients — one key
per delegated resource; for the named-services door, the single door resource
covering every namespace).

Every consumer — the guard's accept logic, the bridge's grant check and trace,
the consent-denial builder — used to dig into that nesting by hand, six
readers across three files. One drifts (reads the wrong nested field for one
client family) and the failure ships masked. This module is the SINGLE reader:
know both shapes once, expose one flat view, and a new MCP surface inherits
correct behavior for free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Mapping

AGENT_CLIENT_PREFIX = "kdcube-agent:"


def _as_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.replace(",", " ").split() if item.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _normalize_account_scope(value: Any) -> dict[str, dict[str, tuple[str, ...]]]:
    """Nested per-account claim binding {provider: {account_id: (claims...)}}.

    Lazily delegates to the one shared normalizer (accepts the legacy list form
    and the nested form); the import stays local so this lightweight view module
    does not pull the automation-access chain at import time."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
        normalize_account_scope,
    )

    return normalize_account_scope(value)


def normalize_resource(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw.split("?", 1)[0].rstrip("/")


def resource_matches(credential_resource: str, request_resource: str) -> bool:
    credential_resource = normalize_resource(credential_resource)
    request_resource = normalize_resource(request_resource)
    if not credential_resource or not request_resource:
        return False
    return credential_resource == request_resource or fnmatch(request_resource, credential_resource)


def _attrs_of(value: Any) -> Mapping[str, Any]:
    """The ``attrs`` of a credential mapping, coerced from either a raw envelope
    dict (``{attrs: {...}}``) or a nested ``{credential: {...}}`` wrapper."""
    if not isinstance(value, Mapping):
        return {}
    nested = value.get("credential")
    if isinstance(nested, Mapping):
        return _attrs_of(nested)
    attrs = value.get("attrs")
    return attrs if isinstance(attrs, Mapping) else {}


@dataclass(frozen=True)
class DelegatedCredentialView:
    """A flat, shape-agnostic view of a request's delegated credential."""

    client_id: str = ""
    subject: str = ""
    authority_id: str = ""
    identity_scope: str = ""
    grantor_user_id: str = ""
    registry_access_id: str = ""
    resource_grants: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    grants: frozenset[str] = field(default_factory=frozenset)
    operations: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    grantor_roles: tuple[str, ...] = ()
    named_services: Mapping[str, Any] = field(default_factory=dict)
    # Per-agent, per-account claim binding:
    # {provider_id: {account_id: (claims...)}}. For a provider, which connected
    # account(s) this client may use AND, per account, the claims it may use
    # there. account "*" = any account; claim "*" = any claim.
    account_scope: Mapping[str, Mapping[str, tuple[str, ...]]] = field(default_factory=dict)
    present: bool = False

    def account_claim_scope(self, provider_id: str) -> Mapping[str, tuple[str, ...]] | None:
        """This client's per-account claim binding for ``provider_id`` —
        ``{account_id: (claims...)}`` (account "*" = any account, claim "*" =
        any claim), or None for no restriction (absent provider)."""
        entry = self.account_scope.get(str(provider_id or "").strip())
        if not entry:
            return None
        return {
            str(account_id).strip(): tuple(claims)
            for account_id, claims in dict(entry).items()
            if str(account_id or "").strip()
        }

    # ── derived views ──────────────────────────────────────────────────────

    @property
    def resources(self) -> tuple[str, ...]:
        """Normalized delegated-resource ids (the ``resource_grants`` keys)."""
        out: list[str] = []
        seen: set[str] = set()
        for key in self.resource_grants.keys():
            normalized = normalize_resource(key)
            if normalized and normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
        return tuple(out)

    @property
    def resource(self) -> str:
        """The bearer's delegated resource — the first ``resource_grants`` key,
        or "". For the named-services door there is exactly one."""
        for key in self.resource_grants.keys():
            key = str(key or "").strip()
            if key:
                return key
        return ""

    @property
    def is_agent(self) -> bool:
        return self.client_id.startswith(AGENT_CLIENT_PREFIX)

    @property
    def agent_client_id(self) -> str:
        """The ``kdcube-agent:<app>:<agent>`` identity, or "" for other client
        families (an OAuth external client keeps its own ``client_id``)."""
        return self.client_id if self.is_agent else ""

    def grants_for_resource(self, request_resource: str) -> set[str]:
        """The grants that apply to the resource this request targets
        (wildcard-matched, mirroring the guard's own resource matching)."""
        out: set[str] = set()
        for resource, grants in self.resource_grants.items():
            if resource_matches(str(resource or ""), request_resource):
                out.update(grants)
        return out

    # ── constructors ───────────────────────────────────────────────────────

    @classmethod
    def from_parts(
        cls,
        credential: Mapping[str, Any] | None,
        grant_record: Mapping[str, Any] | None,
    ) -> "DelegatedCredentialView":
        credential = credential if isinstance(credential, Mapping) else {}
        grant_record = grant_record if isinstance(grant_record, Mapping) else {}
        present = bool(credential or grant_record)

        # The three places grant facts hide: the credential's attrs, the grant
        # record's top level, and the grant record's embedded credential attrs.
        cred_attrs = _attrs_of(credential)
        record_cred_attrs = _attrs_of(grant_record)

        # Grants: union of scope/scopes/grants across all three (the door's
        # authorization reads exactly this set — do not fold resource_grants
        # values in here, keep it byte-for-byte the historical grant set).
        grants: set[str] = set()
        for src in (cred_attrs, grant_record, record_cred_attrs):
            grants.update(_as_list(src.get("scopes")))
            grants.update(_as_list(src.get("scope")))
            grants.update(_as_list(src.get("grants")))

        # resource_grants: the map (agent clients), or synthesized from a single
        # `resource` + scopes (OAuth single-resource clients).
        resource_grants: dict[str, tuple[str, ...]] = {}
        for src in (cred_attrs, grant_record, record_cred_attrs):
            rg = src.get("resource_grants")
            if isinstance(rg, Mapping):
                for res, vals in rg.items():
                    res_key = str(res or "").strip()
                    if res_key and res_key not in resource_grants:
                        resource_grants[res_key] = _as_list(vals)
        if not resource_grants:
            single = str(cred_attrs.get("resource") or grant_record.get("resource") or "").strip()
            if single:
                resource_grants[single] = tuple(sorted(grants))

        # Identity: client id (explicit, then subject-derived for an agent).
        client_id = str(
            grant_record.get("client_id")
            or cred_attrs.get("client_id")
            or record_cred_attrs.get("client_id")
            or ""
        ).strip()
        subject = str(credential.get("subject") or credential.get("sub") or cred_attrs.get("subject") or "").strip()
        if not subject:
            nested = grant_record.get("credential")
            if isinstance(nested, Mapping):
                subject = str(nested.get("subject") or nested.get("sub") or "").strip()
        if not client_id.startswith(AGENT_CLIENT_PREFIX):
            parts = subject.split(":")
            if len(parts) >= 4 and parts[0] == "integration" and parts[1] == AGENT_CLIENT_PREFIX.rstrip(":"):
                client_id = ":".join(parts[1:4])

        grantor_authority = grant_record.get("grantor_authority")
        grantor_authority = grantor_authority if isinstance(grantor_authority, Mapping) else {}

        return cls(
            client_id=client_id,
            subject=subject,
            authority_id=str(
                credential.get("issuer_authority_id")
                or credential.get("authority_id")
                or credential.get("authority")
                or ""
            ).strip(),
            identity_scope=str(
                cred_attrs.get("identity_scope") or grant_record.get("identity_scope") or ""
            ).strip(),
            grantor_user_id=str(
                cred_attrs.get("grantor_subject") or cred_attrs.get("grantor_user_id") or ""
            ).strip(),
            registry_access_id=str(grant_record.get("registry_access_id") or "").strip(),
            resource_grants=resource_grants,
            grants=frozenset(g for g in grants if g),
            operations=_as_list(grant_record.get("operations") or cred_attrs.get("operations")),
            tools=_as_list(grant_record.get("tools")),
            grantor_roles=_as_list(grantor_authority.get("grantor_roles")),
            named_services=(
                dict(grant_record.get("named_services"))
                if isinstance(grant_record.get("named_services"), Mapping)
                else {}
            ),
            account_scope=_normalize_account_scope(
                grant_record.get("account_scope")
                if isinstance(grant_record.get("account_scope"), Mapping)
                else cred_attrs.get("account_scope")
            ),
            present=present,
        )

    @classmethod
    def from_request(cls, request: Any) -> "DelegatedCredentialView":
        delegated = getattr(getattr(request, "state", None), "delegated_credential", None)
        if not isinstance(delegated, Mapping):
            return cls()
        return cls.from_parts(delegated.get("credential"), delegated.get("grant_record"))

    @classmethod
    def from_envelope(cls, envelope: Any, grant_record: Mapping[str, Any] | None = None) -> "DelegatedCredentialView":
        to_dict = getattr(envelope, "to_dict", None)
        credential = to_dict() if callable(to_dict) else (envelope if isinstance(envelope, Mapping) else {})
        return cls.from_parts(credential, grant_record)


def delegated_credential_view(request: Any) -> DelegatedCredentialView:
    """The one accessor: a flat view of ``request.state.delegated_credential``."""
    return DelegatedCredentialView.from_request(request)


__all__ = [
    "AGENT_CLIENT_PREFIX",
    "DelegatedCredentialView",
    "delegated_credential_view",
    "normalize_resource",
    "resource_matches",
]
