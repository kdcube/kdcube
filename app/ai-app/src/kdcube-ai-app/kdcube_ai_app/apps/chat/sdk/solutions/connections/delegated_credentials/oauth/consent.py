# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
The /oauth/authorize consent screen.

A real KDCube admin authorizes the Claude Code connection once. The screen shows
the requested scope(s) and lets the admin select, per-capability, which tools to
authorize for the session (granular consent) before approving. Selected tools are
carried into the authorization code and ultimately the issued grant.
"""
from __future__ import annotations

import html as _html
from typing import Any, Iterable, List, Mapping, Tuple

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    OAuthDelegatedClientConfig,
    oauth_delegated_config,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.flow import AuthorizeRequest
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.boundary_policy import (
    NamedServiceBoundaryCatalog,
)

CONSENT_AUTHORIZE_FIELD_NAMES: tuple[str, ...] = (
    "client_id",
    "redirect_uri",
    "response_type",
    "scope",
    "resource",
    "state",
    "code_challenge",
    "code_challenge_method",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def consent_authorize_fields(payload: Mapping[str, Any] | None) -> dict[str, str]:
    """Normalize the OAuth authorize fields passed to a custom consent renderer.

    Connection Hub sends these under ``payload["request"]``. The flat fallbacks
    keep product renderers tolerant while preserving the same protocol field
    names that must be posted back to ``form_action``.
    """

    data = _mapping(payload)
    request_data = _mapping(
        data.get("request")
        or data.get("authorization_request")
        or data.get("oauth_request")
    )
    scope = _first_text(request_data.get("scope"), data.get("scope"))
    if not scope:
        scope = " ".join(_text(item) for item in (request_data.get("scopes") or data.get("scopes") or ()))
    return {
        "client_id": _first_text(request_data.get("client_id"), data.get("client_id")),
        "redirect_uri": _first_text(request_data.get("redirect_uri"), data.get("redirect_uri")),
        "response_type": _first_text(request_data.get("response_type"), data.get("response_type")) or "code",
        "scope": scope,
        "resource": _first_text(request_data.get("resource"), data.get("resource")),
        "state": _first_text(request_data.get("state"), data.get("state")),
        "code_challenge": _first_text(request_data.get("code_challenge"), data.get("code_challenge")),
        "code_challenge_method": _first_text(
            request_data.get("code_challenge_method"),
            data.get("code_challenge_method"),
        ),
    }


def render_consent_authorize_hidden_inputs(
    fields: Mapping[str, Any],
    *,
    include: Iterable[str] = CONSENT_AUTHORIZE_FIELD_NAMES,
) -> str:
    """Render hidden authorize fields for a custom consent form."""

    return "\n".join(
        f'<input type="hidden" name="{_html.escape(name)}" '
        f'value="{_html.escape(_text(fields.get(name)))}">'
        for name in include
    )


def tools_for_scopes(
    scopes: List[str],
    *,
    config: OAuthDelegatedClientConfig | None = None,
    resource: str | None = None,
) -> List[Tuple[str, str, Tuple[str, ...]]]:
    cfg = config or oauth_delegated_config()
    return [
        (tool.name, tool.description or tool.label, tuple(tool.grants or ()))
        for tool in cfg.tools_for_scopes(scopes, resource=resource)
    ]


def platform_edge_grants_for_scopes(
    scopes: Iterable[str],
    *,
    config: OAuthDelegatedClientConfig | None = None,
) -> List[Tuple[str, str, str]]:
    """Consent rows for the grantor's platform-authority delegation edge."""

    cfg = config or oauth_delegated_config()
    caps = cfg.capability_map()
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for scope in scopes or ():
        grant = str(scope or "").strip()
        if not grant or grant in seen:
            continue
        seen.add(grant)
        cap = caps.get(grant)
        out.append((
            grant,
            cap.label if cap is not None else grant,
            cap.description if cap is not None else "",
        ))
    return out


def named_service_rows_for_scopes(
    scopes: Iterable[str],
    *,
    config: OAuthDelegatedClientConfig | None = None,
    resource: str | None = None,
) -> List[Tuple[str, str, str, Tuple[str, ...]]]:
    """Human-readable named-service namespace operation rows for consent."""

    cfg = config or oauth_delegated_config()
    resource_cfg = cfg.resource_config(resource)
    if resource_cfg is None or not isinstance(resource_cfg.named_services, Mapping):
        return []
    allowed = {str(scope or "").strip() for scope in (scopes or ()) if str(scope or "").strip()}
    out: list[tuple[str, str, str, tuple[str, ...]]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    catalog = NamedServiceBoundaryCatalog(resource_cfg.named_services)
    for namespace in catalog.list_public():
        namespace_label = str(namespace.get("label") or namespace.get("namespace") or "").strip()
        tools = namespace.get("tools")
        if not isinstance(tools, Mapping):
            continue
        for tool_name, raw_tool in tools.items():
            tool = raw_tool if isinstance(raw_tool, Mapping) else {}
            operations = tool.get("operations")
            if isinstance(operations, Mapping) and operations:
                for operation, raw_operation in operations.items():
                    operation_policy = raw_operation if isinstance(raw_operation, Mapping) else {}
                    grants = tuple(
                        str(item).strip()
                        for item in (operation_policy.get("grants") or ())
                        if str(item).strip()
                    )
                    if grants and not set(grants).issubset(allowed):
                        continue
                    key = (str(namespace.get("namespace") or namespace_label), str(operation), grants)
                    if key in seen:
                        continue
                    seen.add(key)
                    label = str(operation_policy.get("label") or operation or tool_name).strip()
                    desc = str(operation_policy.get("description") or tool.get("description") or "").strip()
                    out.append((namespace_label, label, desc, grants))
                continue
            grants = tuple(str(item).strip() for item in (tool.get("grants") or ()) if str(item).strip())
            if grants and not set(grants).issubset(allowed):
                continue
            operation = str(tool.get("operation") or tool_name).strip()
            key = (str(namespace.get("namespace") or namespace_label), operation, grants)
            if key in seen:
                continue
            seen.add(key)
            label = str(tool.get("label") or tool.get("operation") or tool_name).strip()
            desc = str(tool.get("description") or "").strip()
            out.append((namespace_label, label, desc, grants))
    return out


def _brand_monogram(brand: str) -> str:
    """1-2 uppercase initials from the brand name (first letters of first two words)."""
    words = [w for w in brand.split() if w]
    initials = "".join(w[0] for w in words[:2]).upper()
    return initials or "KC"


def _grant_family(grant: str) -> str:
    text = str(grant or "").strip()
    return text.split(":", 1)[0] if ":" in text else text or "other"


def _tool_group(name: str, grants: Iterable[str]) -> str:
    text = str(name or "").lower()
    grant_text = " ".join(str(grant or "").lower() for grant in (grants or ()))
    if any(word in text or word in grant_text for word in ("delete", "write", "upsert", "host_file", "action")):
        return "Write and action tools"
    if any(word in text for word in ("list", "about", "schema", "capabilities", "search", "get", "read", "export")):
        return "Read and discovery tools"
    return "Other tools"


def _render_grouped(items: Iterable[tuple[str, str]], *, css_class: str) -> str:
    groups: dict[str, list[str]] = {}
    for group, row_html in items:
        groups.setdefault(group, []).append(row_html)
    out: list[str] = []
    for group, rows in groups.items():
        out.append(
            f'    <section class="{css_class}-group">'
            f'<div class="group-head"><b>{_html.escape(group)}</b><span>{len(rows)}</span></div>\n'
            + "\n".join(rows)
            + "\n    </section>"
        )
    return "\n".join(out)


def render_consent_html(
    req: AuthorizeRequest,
    issuer: str,
    csrf_token: str = "",
    trusted: bool = False,
    brand: str = "KDCube",
    form_action: str = "/oauth/authorize/consent",
    config: OAuthDelegatedClientConfig | None = None,
    grantor_subject: str = "",
    grantor_label: str = "",
    signout_action: str = "/oauth/logout",
    return_to: str = "",
    connected_accounts: list | None = None,
    seeded_account_scope: Mapping[str, Any] | None = None,
) -> str:
    esc = _html.escape
    tools = tools_for_scopes(req.scopes, config=config, resource=req.resource)
    platform_edge_grants = platform_edge_grants_for_scopes(req.scopes, config=config)
    named_service_rows = named_service_rows_for_scopes(req.scopes, config=config, resource=req.resource)
    monogram = _brand_monogram(brand)

    tool_rows = _render_grouped((
        (
            _tool_group(name, grants),
            f'    <label class="tool"><input type="checkbox" name="tools" value="{esc(name)}" checked> '
            f'<b>{esc(name)}</b><span class="desc">{esc(desc)}</span>'
            f'<span class="grants">Requires: {esc(", ".join(grants) or "none")}</span></label>'
        )
        for name, desc, grants in tools
    ), css_class="tool") or '    <p class="desc">No selectable tools for the requested scope.</p>'

    edge_rows = _render_grouped((
        (
            _grant_family(grant),
            f'    <label class="edge"><input type="checkbox" name="platform_grants" value="{esc(grant)}" checked> '
            f'<b>{esc(label)}</b><span class="desc">{esc(desc)}</span>'
            f'<span class="grants">{esc(grant)}</span></label>'
        )
        for grant, label, desc in platform_edge_grants
    ), css_class="edge") or '    <p class="desc">No platform-authority grants are requested.</p>'

    namespace_rows = _render_grouped((
        (
            namespace,
            f'    <div class="namespace-row"><b>{esc(label)}</b>'
            f'<span class="desc">{esc(desc)}</span>'
            f'<span class="grants">{esc(", ".join(grants) or "none")}</span></div>'
        )
        for namespace, label, desc, grants in named_service_rows
    ), css_class="namespace")
    namespace_section = ""
    if namespace_rows:
        namespace_section = f"""
    <p class="pick">Named-service namespace boundaries:</p>
    <p class="desc">These are the concrete namespace operations covered by the selected grants.</p>
{namespace_rows}
"""

    # Per-account access is granted SEPARATELY from the connection ceiling
    # above, and the runtime is DEFAULT-CLOSED: a connected account this client
    # is not explicitly bound to is not usable by it, whatever the ceiling
    # says. Render the user's real connected accounts with per-claim
    # checkboxes; the picks become the grant card's account_scope. Nothing
    # pre-checked unless a prior card for this app seeded it (re-consent /
    # DCR reconnect).
    seeded: dict[str, dict[str, set]] = {}
    for provider, accounts_map in dict(seeded_account_scope or {}).items():
        seeded[str(provider)] = {
            str(account_id): {str(claim) for claim in (claims or ())}
            for account_id, claims in dict(accounts_map or {}).items()
        }

    def _account_checked(provider_id: str, account_id: str, claim: str) -> bool:
        entry = seeded.get(provider_id) or {}
        claims = entry.get(account_id)
        if claims is None:
            claims = entry.get("*")
        if claims is None:
            return False
        return "*" in claims or claim in claims

    accounts_by_provider: dict[str, list] = {}
    for account in connected_accounts or []:
        provider_id = str(account.get("provider_id") or "")
        if provider_id:
            accounts_by_provider.setdefault(provider_id, []).append(account)

    if accounts_by_provider:
        provider_blocks: list[str] = []
        for provider_id, provider_accounts in accounts_by_provider.items():
            rows: list[str] = []
            for account in provider_accounts:
                account_id = str(account.get("account_id") or "")
                label = str(account.get("label") or account_id)
                workspace = str(account.get("workspace") or "")
                claims = [str(c) for c in (account.get("claims") or []) if str(c or "").strip()]
                claim_boxes = "".join(
                    f'<label class="tool" style="display:inline-flex;margin-right:10px">'
                    f'<input type="checkbox" name="account_scope" '
                    f'value="{esc(provider_id)}|{esc(account_id)}|{esc(claim)}"'
                    f'{" checked" if _account_checked(provider_id, account_id, claim) else ""}> '
                    f"<code>{esc(claim)}</code></label>"
                    for claim in claims
                ) or '<span class="desc">no approved permissions on this account yet</span>'
                rows.append(
                    f'<div class="namespace-row"><b>{esc(label)}</b>'
                    f'{f"<span class=desc>{esc(workspace)}</span>" if workspace else ""}'
                    f'<span class="grants">{claim_boxes}</span></div>'
                )
            provider_blocks.append(
                f'    <section class="namespace-group">'
                f'<div class="group-head"><b>{esc(provider_id)}</b>'
                f"<span>{len(provider_accounts)}</span></div>\n"
                + "\n".join(rows)
                + "\n    </section>"
            )
        per_account_section = f"""
    <p class="pick">Which of your connected accounts may this app use?</p>
    <p class="desc">Nothing is granted until you tick it. The capabilities above are only the
    ceiling — each connected account stays unusable by this app until you bind it here, per
    account and per permission. You can change or revoke any of this later in Connection Hub
    &#8594; Delegated by KDCube, and a call that needs more will ask then.</p>
{"".join(provider_blocks)}
"""
    else:
        per_account_section = """
    <p class="pick">Your connected provider accounts:</p>
    <p class="desc">This approval covers the capabilities above only. None of your connected
    provider accounts is usable by this app until you grant it — per account and per
    permission — in Connection Hub &#8594; Delegated by KDCube, or through the approval that
    rises on the app's first use.</p>
"""

    hidden_fields = [
        ("client_id", req.client_id),
        ("redirect_uri", req.redirect_uri),
        ("response_type", req.response_type),
        ("scope", " ".join(req.scopes)),
        ("resource", req.resource or ""),
        ("state", req.state or ""),
        ("code_challenge", req.code_challenge),
        ("code_challenge_method", req.code_challenge_method),
        ("csrf_token", csrf_token),
    ]
    hidden = "\n".join(
        f'    <input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k, v in hidden_fields
    )
    # The raw scope tokens teach the user nothing here — every scope is
    # presented below as a labeled, narrowable capability row. The details
    # block shows only the shape of the request: how many capabilities, in
    # which families.
    scope_families: list[str] = []
    for token in req.scopes:
        family = token.split(":", 1)[0] if ":" in token else token
        if family and family not in scope_families:
            scope_families.append(family)
    scope_list = (
        f"{len(req.scopes)} capabilities ({esc(', '.join(scope_families))}) — review and narrow below"
        if req.scopes
        else "none"
    )
    account_value = grantor_label or grantor_subject or "current KDCube account"
    account_html = ""
    if grantor_subject or grantor_label:
        account_html = f"""
    <div class="account">
      <div>
        <span class="k">KDCube account</span>
        <strong>{esc(account_value)}</strong>
        {f'<code>{esc(grantor_subject)}</code>' if grantor_subject and grantor_subject != account_value else ''}
      </div>
      <form class="account-form" method="post" action="{esc(signout_action)}">
        <input type="hidden" name="next" value="{esc(return_to)}">
        <button class="signout" type="submit">Sign out of KDCube</button>
      </form>
    </div>
"""

    # Never present an arbitrary client with a hardcoded trusted brand. Show the
    # exact client_id + where the code will be sent, and flag clients that are not
    # pre-registered so a phishing link to /oauth/authorize is recognizable.
    if trusted:
        trust_badge = '<span class="badge ok">pre-registered client</span>'
    else:
        trust_badge = '<span class="badge warn">⚠ newly registered &mdash; verify you started this</span>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" type="image/svg+xml" href="/img/favicon.svg">
  <title>Authorize MCP connection · {esc(brand)}</title>
  <style>
    :root {{
      --accent: #01BEB2; --accent-ink: #009C92; --primary: #4372C3; --primary-700: #2B4B8A;
      --ink: #10304B; --body: #3A5672; --muted: #7A99B0;
      --line: #E6F1F0; --line-strong: #D8ECEB; --panel: #F6FAFA;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      background: #EEF5F5;
      max-width: 560px; margin: 0 auto; min-height: 100vh; padding: 6vh 1rem 2rem;
      color: var(--body); line-height: 1.5; font-size: 13px;
    }}
    .brand {{ display: flex; align-items: center; gap: .6rem; margin: 0 0 1rem .2rem; }}
    .brand .mark {{
      width: 32px; height: 32px; border-radius: 8px; flex: 0 0 auto;
      background: linear-gradient(135deg, var(--accent), var(--accent-ink));
      display: grid; place-items: center; color: #fff; font-weight: 800; font-size: .9rem;
      box-shadow: 0 1px 2px rgba(16,48,75,.08);
    }}
    .brand b {{ color: var(--ink); font-size: .92rem; letter-spacing: .2px; }}
    .brand span {{ color: var(--muted); font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }}
    .card {{
      background: #fff; border: 1px solid var(--line); border-radius: 12px;
      padding: 1.4rem 1.4rem 1.2rem; box-shadow: 0 1px 2px rgba(16,48,75,.04);
    }}
    h1 {{ font-size: 1.15rem; line-height: 1.3; margin: 0 0 .25rem; color: var(--ink); }}
    .sub {{ color: var(--muted); font-size: .88rem; margin: 0 0 1rem; }}
    .details {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: .7rem .85rem; margin: 0 0 .9rem; }}
    .details .row {{ display: flex; gap: .6rem; align-items: baseline; margin: .35rem 0; word-break: break-word; }}
    .k {{ flex: 0 0 96px; color: var(--muted); font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }}
    .account {{
      display: flex; justify-content: space-between; gap: 1rem; align-items: center;
      border: 1px solid var(--line); border-radius: 8px; padding: .7rem .85rem; margin: 0 0 .9rem;
      background: var(--panel);
    }}
    .account strong {{ display: block; font-size: .92rem; margin-top: .12rem; color: var(--ink); }}
    .account code {{ display: inline-block; margin-top: .2rem; max-width: 100%; word-break: break-all; }}
    .account-form {{ margin: 0; flex: 0 0 auto; }}
    .hub-link {{ color: var(--accent, #0f766e); font-weight: 600; text-decoration: underline; }}
    code, .scope {{ font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; }}
    code {{ background: var(--panel); border: 1px solid var(--line); padding: .06rem .32rem; border-radius: 5px; color: var(--ink); }}
    .badge {{ font-size: .68rem; padding: .12rem .45rem; border-radius: 999px; white-space: nowrap; font-weight: 600; }}
    .badge.ok {{ background: rgba(34,197,94,.12); color: #15803D; }}
    .badge.warn {{ background: rgba(248,113,113,.12); color: #B91C1C; }}
    .warn-text {{ background: rgba(245,158,11,.10); border: 1px solid rgba(245,158,11,.35); border-radius: 8px; padding: .6rem .8rem; font-size: .82rem; color: #B45309; }}
    .pick {{ font-weight: 700; margin: 1.1rem 0 .15rem; font-size: 10.5px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
    .tool-group, .edge-group, .namespace-group {{
      border: 1px solid var(--line); border-radius: 8px; margin: .55rem 0; overflow: hidden; background: #fff;
    }}
    .group-head {{
      display: flex; justify-content: space-between; align-items: center; gap: .75rem;
      padding: .45rem .7rem; background: var(--panel); border-bottom: 1px solid var(--line);
      color: var(--ink); font-size: .82rem; font-weight: 600;
      font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .group-head span {{
      min-width: 1.3rem; height: 1.3rem; border-radius: 999px; padding: .1rem .4rem;
      display: inline-grid; place-items: center; background: rgba(1,190,178,.10); color: var(--accent-ink); font-size: .72rem;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    }}
    .tool {{ display: flex; gap: .6rem; align-items: flex-start; padding: .55rem .7rem; margin: .4rem 0;
      border: 1px solid var(--line); border-radius: 8px; cursor: pointer; transition: border-color .15s, background .15s; }}
    .tool-group .tool, .edge-group .edge, .namespace-group .namespace-row {{
      margin: 0; border-left: 0; border-right: 0; border-top: 0; border-radius: 0;
    }}
    .tool-group .tool:last-child, .edge-group .edge:last-child, .namespace-group .namespace-row:last-child {{ border-bottom: 0; }}
    .tool:hover {{ background: var(--panel); }}
    .edge {{ display: flex; gap: .6rem; align-items: flex-start; padding: .55rem .7rem; margin: .4rem 0;
      border: 1px solid var(--line); border-radius: 8px; cursor: pointer; }}
    .edge:hover {{ background: var(--panel); }}
    .namespace-row {{ display: block; padding: .55rem .7rem; margin: .4rem 0;
      border: 1px solid var(--line); border-radius: 8px; }}
    .tool input, .edge input {{ margin-top: .2rem; width: 1rem; height: 1rem; accent-color: var(--accent); }}
    .tool b {{ font-size: .9rem; color: var(--ink); }}
    .edge b {{ font-size: .9rem; color: var(--ink); }}
    .namespace-row b {{ font-size: .9rem; color: var(--ink); }}
    .tool .desc, .edge .desc, .namespace-row .desc, .desc {{ display: block; color: var(--muted); font-size: .8rem; }}
    .tool .grants, .edge .grants, .namespace-row .grants {{ display: block; color: var(--accent-ink); font-size: .74rem; margin-top: .14rem; font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .actions {{ display: flex; gap: .75rem; margin-top: 1.2rem; }}
    button {{ flex: 1; padding: .55rem; border-radius: 8px; border: 0; font-size: .9rem; font-weight: 600; cursor: pointer; transition: filter .15s, opacity .15s; }}
    button:hover {{ filter: brightness(.96); }}
    button:disabled {{ opacity: .65; cursor: progress; }}
    button.busy {{ pointer-events: none; }}
    button.busy::after {{
      content: ""; display: inline-block; width: .85em; height: .85em; margin-left: .45rem;
      border: 2px solid currentColor; border-right-color: transparent; border-radius: 999px;
      vertical-align: -.12em; animation: spin .7s linear infinite;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    .approve {{ background: var(--primary); color: #fff; }}
    .approve:hover {{ background: var(--primary-700); filter: none; }}
    .deny {{ background: #fff; color: var(--body); border: 1px solid var(--line-strong); }}
    .signout {{ flex: 0 0 auto; background: #fff; color: #B91C1C; border: 1px solid rgba(248,113,113,.45); font-size: .82rem; padding: .45rem .7rem; }}
    footer {{ margin-top: 1.2rem; font-size: .78rem; color: var(--muted); text-align: center; }}
    footer a {{ color: var(--accent-ink); }}
  </style>
</head>
<body>
  <div class="brand">
    <div class="mark">{esc(monogram)}</div>
    <div><b>{esc(brand)}</b><br><span>MCP authorization</span></div>
  </div>
  <div class="card">
    <h1>Authorize an MCP connection to {esc(brand)}</h1>
    <p class="sub">An application is requesting access to this workspace's data over MCP.</p>
    <div class="details">
      <div class="row"><span class="k">Client</span> <span><code>{esc(req.client_id)}</code> {trust_badge}</span></div>
      <div class="row"><span class="k">Sends code to</span> <code>{esc(req.redirect_uri)}</code></div>
      <div class="row"><span class="k">Scope</span> <span class="scope">{scope_list}</span></div>
    </div>
    <p class="warn-text">Only approve if <strong>you</strong> started this connection and recognize the
    client and the redirect URL above. The connection can receive only the scopes and capabilities
    you approve here, and only if your KDCube account is allowed to delegate them.</p>
{account_html}
    <form method="post" action="{esc(form_action)}">
{hidden}
    <p class="pick">Platform account delegation edge:</p>
    <p class="desc">These grants let the external client represent your KDCube account only when this resource later needs platform authority.</p>
{edge_rows}
{namespace_section}
{per_account_section}
    <p class="pick">Select which capabilities to authorize for this connection:</p>
{tool_rows}
    <div class="actions">
      <button class="approve" type="submit" name="decision" value="approve">Approve</button>
      <button class="deny" type="submit" name="decision" value="deny">Deny</button>
    </div>
    </form>
  </div>
  <footer>Powered by <a href="https://kdcube.tech/" target="_blank" rel="noopener">KDCube</a> · {esc(issuer)}</footer>
  <script>
    document.querySelectorAll('form').forEach((form) => {{
      form.addEventListener('submit', (event) => {{
        if (form.dataset.submitted === '1') {{
          event.preventDefault();
          return;
        }}
        form.dataset.submitted = '1';
        const submitter = event.submitter;
        if (submitter) {{
          submitter.classList.add('busy');
          if (submitter.value === 'approve') submitter.textContent = 'Approving';
          else if (submitter.value === 'deny') submitter.textContent = 'Denying';
          else submitter.textContent = 'Working';
        }}
        form.querySelectorAll('button').forEach((button) => {{
          if (button !== submitter) button.disabled = true;
        }});
      }});
    }});
  </script>
</body>
</html>"""
