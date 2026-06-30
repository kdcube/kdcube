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
from typing import Iterable, List, Tuple

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    OAuthDelegatedClientConfig,
    oauth_delegated_config,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.flow import AuthorizeRequest


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


def _brand_monogram(brand: str) -> str:
    """1-2 uppercase initials from the brand name (first letters of first two words)."""
    words = [w for w in brand.split() if w]
    initials = "".join(w[0] for w in words[:2]).upper()
    return initials or "KC"


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
) -> str:
    esc = _html.escape
    tools = tools_for_scopes(req.scopes, config=config, resource=req.resource)
    platform_edge_grants = platform_edge_grants_for_scopes(req.scopes, config=config)
    monogram = _brand_monogram(brand)

    tool_rows = "\n".join(
        f'    <label class="tool"><input type="checkbox" name="tools" value="{esc(name)}" checked> '
        f'<b>{esc(name)}</b><span class="desc">{esc(desc)}</span>'
        f'<span class="grants">Requires: {esc(", ".join(grants) or "none")}</span></label>'
        for name, desc, grants in tools
    ) or '    <p class="desc">No selectable tools for the requested scope.</p>'

    edge_rows = "\n".join(
        f'    <label class="edge"><input type="checkbox" name="platform_grants" value="{esc(grant)}" checked> '
        f'<b>{esc(label)}</b><span class="desc">{esc(desc)}</span>'
        f'<span class="grants">{esc(grant)}</span></label>'
        for grant, label, desc in platform_edge_grants
    ) or '    <p class="desc">No platform-authority grants are requested.</p>'

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
    scope_list = ", ".join(esc(s) for s in req.scopes)
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
  <title>Authorize MCP connection · {esc(brand)}</title>
  <style>
    :root {{
      --accent: #1565c0; --accent-700: #0f4e9c; --ink: #1a2230; --muted: #5b6675;
      --line: #e5e9f0; --panel: #f4f7fb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      background: linear-gradient(180deg, #eef3f8 0%, #f7f9fc 100%);
      max-width: 560px; margin: 0 auto; min-height: 100vh; padding: 6vh 1rem 2rem;
      color: var(--ink); line-height: 1.5;
    }}
    .brand {{ display: flex; align-items: center; gap: .6rem; margin: 0 0 1rem .2rem; }}
    .brand .mark {{
      width: 34px; height: 34px; border-radius: 9px; flex: 0 0 auto;
      background: linear-gradient(135deg, var(--accent), var(--accent-700));
      display: grid; place-items: center; color: #fff; font-weight: 800; font-size: .95rem;
      box-shadow: 0 2px 6px rgba(21,101,192,.25);
    }}
    .brand b {{ color: var(--accent-700); font-size: .95rem; letter-spacing: .2px; }}
    .brand span {{ color: var(--muted); font-size: .8rem; }}
    .card {{
      background: #fff; border: 1px solid var(--line); border-radius: 16px;
      padding: 1.6rem 1.6rem 1.4rem; box-shadow: 0 8px 30px rgba(16,40,70,.08);
    }}
    h1 {{ font-size: 1.3rem; line-height: 1.3; margin: 0 0 .25rem; color: var(--accent-700); }}
    .sub {{ color: var(--muted); font-size: .9rem; margin: 0 0 1.1rem; }}
    .details {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: .85rem 1rem; margin: 0 0 1rem; }}
    .details .row {{ display: flex; gap: .6rem; align-items: baseline; margin: .4rem 0; word-break: break-word; }}
    .k {{ flex: 0 0 96px; color: var(--muted); font-size: .8rem; text-transform: uppercase; letter-spacing: .4px; }}
    .account {{
      display: flex; justify-content: space-between; gap: 1rem; align-items: center;
      border: 1px solid #cfe3f5; border-radius: 10px; padding: .8rem .9rem; margin: 0 0 1rem;
      background: #fbfdff;
    }}
    .account strong {{ display: block; font-size: .95rem; margin-top: .12rem; }}
    .account code {{ display: inline-block; margin-top: .2rem; max-width: 100%; word-break: break-all; }}
    .account-form {{ margin: 0; flex: 0 0 auto; }}
    code, .scope {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .85rem; }}
    code {{ background: #eef2f7; padding: .08rem .35rem; border-radius: 5px; }}
    .badge {{ font-size: .7rem; padding: .12rem .45rem; border-radius: 999px; white-space: nowrap; font-weight: 600; }}
    .badge.ok {{ background: #e6f4ea; color: #1e7e34; }}
    .badge.warn {{ background: #fdecea; color: #b71c1c; }}
    .warn-text {{ background: #fff8e1; border: 1px solid #ffe6a3; border-radius: 10px; padding: .7rem .9rem; font-size: .85rem; color: #6b5400; }}
    .pick {{ font-weight: 600; margin: 1.2rem 0 .5rem; font-size: .92rem; }}
    .tool {{ display: flex; gap: .6rem; align-items: flex-start; padding: .7rem .8rem; margin: .45rem 0;
      border: 1px solid var(--line); border-radius: 10px; cursor: pointer; transition: border-color .15s, background .15s; }}
    .tool:hover {{ border-color: #c7d6e6; background: #fafcff; }}
    .edge {{ display: flex; gap: .6rem; align-items: flex-start; padding: .7rem .8rem; margin: .45rem 0;
      border: 1px solid #cfe3f5; border-radius: 10px; cursor: pointer; background: #fbfdff; }}
    .tool input, .edge input {{ margin-top: .2rem; width: 1.05rem; height: 1.05rem; accent-color: var(--accent); }}
    .tool b {{ font-size: .95rem; }}
    .edge b {{ font-size: .95rem; }}
    .tool .desc, .edge .desc, .desc {{ display: block; color: var(--muted); font-size: .83rem; }}
    .tool .grants, .edge .grants {{ display: block; color: #365f86; font-size: .76rem; margin-top: .16rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .actions {{ display: flex; gap: .75rem; margin-top: 1.4rem; }}
    button {{ flex: 1; padding: .7rem; border-radius: 10px; border: 0; font-size: 1rem; font-weight: 600; cursor: pointer; transition: filter .15s, opacity .15s; }}
    button:hover {{ filter: brightness(.96); }}
    button:disabled {{ opacity: .65; cursor: progress; }}
    button.busy {{ pointer-events: none; }}
    button.busy::after {{
      content: ""; display: inline-block; width: .85em; height: .85em; margin-left: .45rem;
      border: 2px solid currentColor; border-right-color: transparent; border-radius: 999px;
      vertical-align: -.12em; animation: spin .7s linear infinite;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    .approve {{ background: var(--accent); color: #fff; }}
    .deny {{ background: #eef1f5; color: #33414f; }}
    .signout {{ flex: 0 0 auto; background: #fff; color: #c2185b; border: 1px solid #f4c6da; font-size: .86rem; padding: .55rem .75rem; }}
    footer {{ margin-top: 1.3rem; font-size: .8rem; color: var(--muted); text-align: center; }}
    footer a {{ color: var(--accent-700); }}
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
