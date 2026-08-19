# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Descriptor-backed configuration for the OAuth delegated credential adapter."""
from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Mapping, Tuple

from kdcube_ai_app.apps.chat.sdk.solutions.connections.hub.resolver import (
    DEFAULT_DELEGATED_IDENTITY_SCOPE,
    normalize_delegated_identity_scope,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.boundary_policy import (
    NamedServiceBoundaryCatalog,
)

DEFAULT_CLAUDE_REDIRECT_URIS: tuple[str, ...] = (
    "http://localhost/callback",
    "http://127.0.0.1/callback",
    "https://claude.ai/api/mcp/auth_callback",
)

DEFAULT_DCR_REDIRECT_URIS: tuple[str, ...] = (
    "https://claude.ai/api/mcp/auth_callback",
    "http://localhost/callback",
    "http://127.0.0.1/callback",
)


@dataclass(frozen=True)
class OAuthDelegatedPublicClientConfig:
    client_id: str
    redirect_uris: tuple[str, ...]
    token_endpoint_auth_method: str = "none"
    application_type: str = "native"
    client_name: str = ""
    client_uri: str = ""
    logo_uri: str = ""


@dataclass(frozen=True)
class OAuthDelegatedDynamicClientRegistrationConfig:
    allowed_redirect_uris: tuple[str, ...]
    enabled: bool = True
    default_application_type: str = "native"


@dataclass(frozen=True)
class OAuthDelegatedClientMetadataDocumentsConfig:
    enabled: bool = True
    allowed_domains: tuple[str, ...] = ()
    allow_subdomains: bool = True
    fetch_timeout_seconds: float = 5.0
    max_document_bytes: int = 5 * 1024
    cache_ttl_seconds: int = 3600
    cache_max_ttl_seconds: int = 24 * 3600


@dataclass(frozen=True)
class OAuthDelegatedToolConfig:
    name: str
    label: str
    description: str = ""
    grants: tuple[str, ...] = ()


@dataclass(frozen=True)
class OAuthDelegatedAccountRequirement:
    """One provider account a door claim is backed by, for the consent page.

    A door claim (e.g. ``mail:read``) is provider-neutral: the connected account
    that satisfies it holds a PROVIDER claim (``gmail:read``) that differs from
    the door token. This declares that mapping so the consent page can resolve
    which account to connect for a door claim, not just for provider-claim
    tokens whose provider is discoverable from the claim vocabulary alone.
    """

    provider_id: str
    claims: tuple[str, ...] = ()


@dataclass(frozen=True)
class OAuthDelegatedCapabilityConfig:
    grant: str
    label: str
    description: str = ""
    tools: tuple[OAuthDelegatedToolConfig, ...] = ()
    delegable_roles: tuple[str, ...] = ()
    delegable_permissions: tuple[str, ...] = ()
    connected_accounts: tuple[OAuthDelegatedAccountRequirement, ...] = ()


@dataclass(frozen=True)
class OAuthDelegatedResourceConfig:
    resource: str
    grants: tuple[str, ...]
    tools: tuple[OAuthDelegatedToolConfig, ...] = ()
    label: str = ""
    identity_scope: str = DEFAULT_DELEGATED_IDENTITY_SCOPE
    named_services: Mapping[str, Any] = field(default_factory=dict)
    admin_only: bool = False


@dataclass(frozen=True)
class OAuthDelegatedConsentUIConfig:
    """Where the delegated credential consent screen is rendered.

    `connection_hub` uses the built-in renderer. `authority_provider` means the
    OAuth adapter should use the named authority provider's `entrypoints.consent`
    as the renderer while keeping the approve/deny POST and token issuance in
    Connection Hub.
    """

    mode: str = "connection_hub"
    authority_id: str = ""
    provider_id: str = ""
    entrypoint: str = "consent"
    host: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OAuthDelegatedClientConfig:
    enabled: bool
    issuer: str | None
    tenant: str
    project: str
    auth_cookie_name: str
    brand: str
    consent_ui: OAuthDelegatedConsentUIConfig
    public_clients: tuple[OAuthDelegatedPublicClientConfig, ...]
    dynamic_client_registration: OAuthDelegatedDynamicClientRegistrationConfig
    client_id_metadata_documents: OAuthDelegatedClientMetadataDocumentsConfig
    capabilities: tuple[OAuthDelegatedCapabilityConfig, ...]
    resources: tuple[OAuthDelegatedResourceConfig, ...]

    def capability_map(self) -> dict[str, OAuthDelegatedCapabilityConfig]:
        return {item.grant: item for item in self.capabilities}

    def ungrantable_resource_tools(self) -> tuple[tuple[str, str, str], ...]:
        """``(resource, tool, claim)`` for every catalog tool nobody can be
        granted — its claim is not declared under ``capabilities``.

        The catalog says WHICH endpoints may be asked for here and what each of
        their tools costs in claims; ``capabilities`` says who may delegate a
        claim at all. Both are this deployment's decision, written in the same
        block, and an entry that names a claim the deployment never declared is
        askable by nobody: the access card renders, the grant is refused, and
        the refusal talks about the claim rather than the missing declaration.
        Silent until now, so it was found by a person clicking Grant access.

        Pure and cheap: it compares two lists this config already holds."""
        declared = {item.grant for item in self.capabilities}
        missing: list[tuple[str, str, str]] = []
        for resource in self.resources:
            for tool in resource.tools or ():
                for claim in getattr(tool, "grants", ()) or ():
                    if claim not in declared:
                        missing.append((resource.resource, getattr(tool, "name", "") or "", claim))
        return tuple(missing)

    def connected_account_requirements(self) -> dict[str, tuple[OAuthDelegatedAccountRequirement, ...]]:
        """Door claim -> the provider account(s) that back it. Used by the
        consent page to resolve door claims (mail:read) whose provider is not
        discoverable from the provider claim vocabulary alone."""
        return {
            item.grant: item.connected_accounts
            for item in self.capabilities
            if item.connected_accounts
        }

    def resource_config(self, resource: str | None) -> OAuthDelegatedResourceConfig | None:
        """The row that serves a REQUESTED resource.

        The all-resource row answers whatever no specific row claims, which is
        what makes it usable as the declared admin surface at issuance time.
        """
        return self._match_resource(resource, wildcard_row_is_fallback=True)

    def card_selector_config(
        self,
        selector: str | None,
        *,
        request_resource: str = "",
    ) -> OAuthDelegatedResourceConfig | None:
        """The row that governs a CARD holding ``selector``.

        The concrete request URL still picks the row, because a card selector
        may be broader than the row it was granted through. What the selector
        decides is whether the all-resource row is reachable at all: it is a
        declared admin surface, answering only a card that holds ``*``. A card
        whose door left the catalog therefore matches nothing, which is the
        removal the guard, Save pruning, and drift each have to report.
        """
        return self._match_resource(
            str(request_resource or "").strip() or selector,
            wildcard_row_is_fallback=str(selector or "").strip().rstrip("/") == "*",
        )

    def _match_resource(
        self, resource: str | None, *, wildcard_row_is_fallback: bool
    ) -> OAuthDelegatedResourceConfig | None:
        text = str(resource or "").strip().rstrip("/")
        if not text:
            return None
        fallback: OAuthDelegatedResourceConfig | None = None
        for item in self.resources:
            pattern = item.resource.rstrip("/")
            if pattern == text:
                return item
            if pattern == "*":
                # Deferred so declaration order cannot let it shadow a specific
                # row; whether it is reachable at all is the caller's question.
                fallback = item
                continue
            if fnmatch(text, pattern):
                return item
        if wildcard_row_is_fallback and fallback is not None:
            if fnmatch(text, fallback.resource.rstrip("/")):
                return fallback
        return None

    def supported_scopes(self, resource: str | None = None) -> tuple[str, ...]:
        resource_cfg = self.resource_config(resource)
        if resource_cfg:
            grants = resource_cfg.grants or _ordered_union(
                grant for tool in resource_cfg.tools for grant in tool.grants
            )
            if grants:
                return grants
        return tuple(item.grant for item in self.capabilities)

    def tools_for_resource(self, resource: str | None = None) -> tuple[OAuthDelegatedToolConfig, ...]:
        resource_cfg = self.resource_config(resource)
        if resource_cfg:
            return resource_cfg.tools
        seen: dict[str, OAuthDelegatedToolConfig] = {}
        for cap in self.capabilities:
            for tool in cap.tools:
                seen.setdefault(
                    tool.name,
                    OAuthDelegatedToolConfig(
                        name=tool.name,
                        label=tool.label,
                        description=tool.description,
                        grants=tool.grants or (cap.grant,),
                    ),
                )
        return tuple(seen.values())

    def tools_for_scopes(
        self,
        scopes: tuple[str, ...] | list[str],
        *,
        resource: str | None = None,
    ) -> tuple[OAuthDelegatedToolConfig, ...]:
        allowed_grants = set(str(scope) for scope in (scopes or ()) if str(scope).strip())
        seen: dict[str, OAuthDelegatedToolConfig] = {}
        for tool in self.tools_for_resource(resource):
            required = set(tool.grants or ())
            if required and not required.issubset(allowed_grants):
                continue
            if not required and allowed_grants:
                continue
            seen.setdefault(tool.name, tool)
        return tuple(seen.values())

    def resource_tool_catalog(self, resource: str | None = None) -> tuple[OAuthDelegatedToolConfig, ...]:
        """Tool-centric catalog for protected-resource metadata and consent."""
        return self.tools_for_resource(resource)

    def resource_operation_catalog(self, resource: str | None = None) -> tuple[OAuthDelegatedToolConfig, ...]:
        """Operation-centric alias for REST/platform protected-resource consumers."""
        return self.resource_tool_catalog(resource)


def _state_for(source: Any) -> Any | None:
    if source is None:
        return None
    request_state = getattr(source, "state", None)
    if request_state is not None and hasattr(request_state, "oauth_delegated_config"):
        return request_state
    app = getattr(source, "app", None) or source
    return getattr(app, "state", None)


def _coerce_str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _coerce_string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        item = value.strip()
        return (item,) if item else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _coerce_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _coerce_float(value: Any, default: float, *, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _ordered_union(values: Any) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def _default_capabilities() -> tuple[OAuthDelegatedCapabilityConfig, ...]:
    return ()


def _parse_tool(item: Any) -> OAuthDelegatedToolConfig | None:
    if isinstance(item, str):
        name = item.strip()
        return OAuthDelegatedToolConfig(name=name, label=name) if name else None
    if not isinstance(item, Mapping):
        return None
    name = _coerce_str(item.get("name") or item.get("tool"))
    if not name:
        return None
    return OAuthDelegatedToolConfig(
        name=name,
        label=_coerce_str(item.get("label")) or name,
        description=_coerce_str(item.get("description")) or "",
        grants=_coerce_string_tuple(item.get("grants") or item.get("scopes") or item.get("required_grants")),
    )


def _parse_capabilities(raw: Any) -> tuple[OAuthDelegatedCapabilityConfig, ...]:
    if raw is None:
        return _default_capabilities()
    rows: list[Any]
    if isinstance(raw, Mapping):
        rows = [
            {"grant": grant, **(value if isinstance(value, Mapping) else {"label": value})}
            for grant, value in raw.items()
        ]
    elif isinstance(raw, (list, tuple)):
        rows = list(raw)
    else:
        rows = []
    out: list[OAuthDelegatedCapabilityConfig] = []
    for item in rows:
        if isinstance(item, str):
            item = {"grant": item}
        if not isinstance(item, Mapping):
            continue
        grant = _coerce_str(item.get("grant") or item.get("scope") or item.get("name"))
        if not grant:
            continue
        tools = tuple(
            tool for tool in (_parse_tool(row) for row in (item.get("tools") or item.get("actions") or ()))
            if tool is not None
        )
        out.append(
            OAuthDelegatedCapabilityConfig(
                grant=grant,
                label=_coerce_str(item.get("label")) or grant,
                description=_coerce_str(item.get("description")) or "",
                tools=tools,
                delegable_roles=_coerce_string_tuple(item.get("delegable_roles") or item.get("roles")),
                delegable_permissions=_coerce_string_tuple(
                    item.get("delegable_permissions") or item.get("permissions")
                ),
                connected_accounts=_parse_account_requirements(item.get("connected_accounts")),
            )
        )
    return tuple(out)


def _parse_account_requirements(raw: Any) -> tuple[OAuthDelegatedAccountRequirement, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[OAuthDelegatedAccountRequirement] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        provider_id = _coerce_str(item.get("provider_id") or item.get("provider"))
        if not provider_id:
            continue
        out.append(
            OAuthDelegatedAccountRequirement(
                provider_id=provider_id,
                claims=_coerce_string_tuple(item.get("claims")),
            )
        )
    return tuple(out)


def _nested_named_service_grants(raw: Any) -> tuple[str, ...]:
    grants: list[str] = []
    catalog = NamedServiceBoundaryCatalog(raw if isinstance(raw, Mapping) else {})
    for namespace_policy in catalog.list_public():
        tools = namespace_policy.get("tools")
        tools = tools if isinstance(tools, Mapping) else {}
        for tool_policy in tools.values():
            if isinstance(tool_policy, Mapping):
                grants.extend(_coerce_string_tuple(tool_policy.get("grants")))
                operations = tool_policy.get("operations")
                operations = operations if isinstance(operations, Mapping) else {}
                for operation_policy in operations.values():
                    if isinstance(operation_policy, Mapping):
                        grants.extend(_coerce_string_tuple(operation_policy.get("grants")))
    return _ordered_union(grants)


def _parse_resources(raw: Any) -> tuple[OAuthDelegatedResourceConfig, ...]:
    if raw is None:
        return ()
    rows: list[Any]
    if isinstance(raw, Mapping):
        rows = [
            {"resource": resource, **(value if isinstance(value, Mapping) else {})}
            for resource, value in raw.items()
        ]
    elif isinstance(raw, (list, tuple)):
        rows = list(raw)
    else:
        rows = []
    out: list[OAuthDelegatedResourceConfig] = []
    for item in rows:
        if isinstance(item, str):
            item = {"resource": item}
        if not isinstance(item, Mapping):
            continue
        resource = _coerce_str(item.get("resource") or item.get("url") or item.get("pattern"))
        if not resource:
            continue
        tools_raw = (
            item.get("tools")
            or item.get("operations")
            or item.get("allowed_tools")
            or item.get("actions")
        )
        if isinstance(tools_raw, Mapping):
            tools = tuple(
                tool
                for tool in (
                    _parse_tool({"name": name, **(data if isinstance(data, Mapping) else {})})
                    for name, data in tools_raw.items()
                )
                if tool is not None
            )
        else:
            tools = tuple(tool for tool in (_parse_tool(row) for row in (tools_raw or ())) if tool is not None)
        explicit_grants = _coerce_string_tuple(item.get("grants") or item.get("scopes"))
        named_services = dict(item.get("named_services") or {}) if isinstance(item.get("named_services"), Mapping) else {}
        namespace_grants = _nested_named_service_grants(named_services)
        out.append(
            OAuthDelegatedResourceConfig(
                resource=resource,
                grants=explicit_grants or _ordered_union(
                    [*(grant for tool in tools for grant in tool.grants), *namespace_grants]
                ),
                tools=tools,
                label=_coerce_str(item.get("label")) or "",
                identity_scope=normalize_delegated_identity_scope(item.get("identity_scope")),
                named_services=named_services,
                admin_only=_coerce_bool(item.get("admin_only") or item.get("adminOnly"), default=False),
            )
        )
    return tuple(out)


def _default_public_clients() -> tuple[OAuthDelegatedPublicClientConfig, ...]:
    return (
        OAuthDelegatedPublicClientConfig(
            client_id="claude",
            redirect_uris=DEFAULT_CLAUDE_REDIRECT_URIS,
        ),
    )


def _parse_public_clients(raw: Any) -> tuple[OAuthDelegatedPublicClientConfig, ...]:
    if raw is None:
        return _default_public_clients()
    if not isinstance(raw, (list, tuple)):
        return ()
    clients: list[OAuthDelegatedPublicClientConfig] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        client_id = _coerce_str(item.get("client_id"))
        redirects = _coerce_string_tuple(item.get("redirect_uris"))
        if not client_id or not redirects:
            continue
        clients.append(
            OAuthDelegatedPublicClientConfig(
                client_id=client_id,
                redirect_uris=redirects,
                token_endpoint_auth_method=_coerce_str(item.get("token_endpoint_auth_method")) or "none",
                application_type=_coerce_str(item.get("application_type")) or "native",
                client_name=_coerce_str(item.get("client_name")) or "",
                client_uri=_coerce_str(item.get("client_uri")) or "",
                logo_uri=_coerce_str(item.get("logo_uri")) or "",
            )
        )
    return tuple(clients)


def _parse_consent_ui(raw: Any) -> OAuthDelegatedConsentUIConfig:
    node = raw if isinstance(raw, Mapping) else {}
    ref = node.get("authority_ref")
    ref_node = ref if isinstance(ref, Mapping) else {}
    host = node.get("host")
    host_node = host if isinstance(host, Mapping) else {}
    mode = _coerce_str(node.get("mode")) or "connection_hub"
    authority_id = _coerce_str(
        node.get("authority_id")
        or ref_node.get("authority_id")
        or ref_node.get("authority")
    ) or ""
    provider_id = _coerce_str(
        node.get("provider_id")
        or ref_node.get("provider_id")
        or ref_node.get("provider")
    ) or ""
    entrypoint = _coerce_str(node.get("entrypoint") or ref_node.get("entrypoint")) or "consent"
    if authority_id and provider_id and mode == "connection_hub":
        mode = "authority_provider"
    if host_node and mode == "connection_hub":
        mode = "bundle_hosted"
    return OAuthDelegatedConsentUIConfig(
        mode=mode,
        authority_id=authority_id,
        provider_id=provider_id,
        entrypoint=entrypoint,
        host=host_node,
    )


def _parse_config(raw: Any, *, settings: Any | None = None) -> OAuthDelegatedClientConfig:
    node = raw if isinstance(raw, Mapping) else {}
    dcr = node.get("dynamic_client_registration")
    dcr_node = dcr if isinstance(dcr, Mapping) else {}
    allowed_redirects = _coerce_string_tuple(dcr_node.get("allowed_redirect_uris")) or DEFAULT_DCR_REDIRECT_URIS
    cimd = node.get("client_id_metadata_documents")
    cimd_node = cimd if isinstance(cimd, Mapping) else {}

    tenant = _coerce_str(node.get("tenant")) or _coerce_str(getattr(settings, "TENANT", None)) or "home"
    project = _coerce_str(node.get("project")) or _coerce_str(getattr(settings, "PROJECT", None)) or "demo"
    auth = getattr(settings, "AUTH", None)
    cookie_name = _coerce_str(node.get("auth_cookie_name")) or _coerce_str(getattr(auth, "AUTH_TOKEN_COOKIE_NAME", None)) or "__Secure-LATC"

    return OAuthDelegatedClientConfig(
        enabled=_coerce_bool(node.get("enabled"), False),
        issuer=_coerce_str(node.get("issuer")),
        tenant=tenant,
        project=project,
        auth_cookie_name=cookie_name,
        brand=_coerce_str(node.get("brand")) or "KDCube",
        consent_ui=_parse_consent_ui(node.get("consent_ui")),
        public_clients=_parse_public_clients(node.get("public_clients")),
        dynamic_client_registration=OAuthDelegatedDynamicClientRegistrationConfig(
            allowed_redirect_uris=allowed_redirects,
            enabled=_coerce_bool(dcr_node.get("enabled"), True),
            default_application_type=(
                _coerce_str(dcr_node.get("default_application_type")) or "native"
            ),
        ),
        client_id_metadata_documents=OAuthDelegatedClientMetadataDocumentsConfig(
            enabled=_coerce_bool(cimd_node.get("enabled"), True),
            allowed_domains=_coerce_string_tuple(cimd_node.get("allowed_domains")),
            allow_subdomains=_coerce_bool(cimd_node.get("allow_subdomains"), True),
            fetch_timeout_seconds=_coerce_float(
                cimd_node.get("fetch_timeout_seconds"), 5.0, minimum=0.1
            ),
            max_document_bytes=_coerce_int(
                cimd_node.get("max_document_bytes"), 5 * 1024, minimum=1024
            ),
            cache_ttl_seconds=_coerce_int(
                cimd_node.get("cache_ttl_seconds"), 3600, minimum=1
            ),
            cache_max_ttl_seconds=_coerce_int(
                cimd_node.get("cache_max_ttl_seconds"), 24 * 3600, minimum=1
            ),
        ),
        capabilities=_parse_capabilities(node.get("capabilities")),
        resources=_parse_resources(node.get("resources")),
    )


def oauth_delegated_config_from_connections(
    connections: Mapping[str, Any] | None,
) -> OAuthDelegatedClientConfig:
    """Parse a ``connections`` mapping with the same reader request paths use.

    The mapping comes from a registered catalog document, not from live props,
    so this never consults request or app state.
    """
    node = connections if isinstance(connections, Mapping) else {}
    delegated = node.get("delegated_credentials")
    delegated = delegated if isinstance(delegated, Mapping) else {}
    oauth = delegated.get("oauth")
    return _parse_config(oauth if isinstance(oauth, Mapping) else {})


def oauth_delegated_config(source: Any | None = None) -> OAuthDelegatedClientConfig:
    """Resolve OAuth delegated-credential config from app state or assembly.

    Tests may set ``app.state.oauth_delegated_config`` to a mapping or ``OAuthDelegatedClientConfig``.
    Connection Hub mounts this adapter by setting a request-local config from
    ``connection-hub@1-0`` bundle props. Without that explicit config, the
    adapter is disabled.
    tenant/project and cookie name come from the canonical Settings object, which
    already resolves ``context.*`` and ``auth.*`` from descriptors.
    """
    state = _state_for(source)
    override = getattr(state, "oauth_delegated_config", None) if state is not None else None
    if isinstance(override, OAuthDelegatedClientConfig):
        return override

    if override is not None:
        return _parse_config(override)

    from kdcube_ai_app.apps.chat.sdk.config import get_settings

    settings = get_settings()
    return _parse_config({}, settings=settings)
