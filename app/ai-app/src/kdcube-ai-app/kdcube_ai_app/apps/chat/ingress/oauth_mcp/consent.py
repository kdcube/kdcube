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
from typing import List, Tuple

from kdcube_ai_app.apps.chat.ingress.oauth_mcp.flow import AuthorizeRequest

# scope -> [(tool_name, human description)]. Extensible as more MCP tools land.
SCOPE_TOOLS = {
    "conversations:read": [
        ("conversations_export", "Export conversation transcripts (read-only, all tenants/projects/bundles)"),
    ],
}


def tools_for_scopes(scopes: List[str]) -> List[Tuple[str, str]]:
    seen: dict[str, str] = {}
    for s in scopes:
        for name, desc in SCOPE_TOOLS.get(s, []):
            seen.setdefault(name, desc)
    return list(seen.items())


def render_consent_html(
    req: AuthorizeRequest, issuer: str, csrf_token: str = "", trusted: bool = False
) -> str:
    esc = _html.escape
    tools = tools_for_scopes(req.scopes)

    tool_rows = "\n".join(
        f'    <label class="tool"><input type="checkbox" name="tools" value="{esc(name)}" checked> '
        f'<b>{esc(name)}</b><span class="desc">{esc(desc)}</span></label>'
        for name, desc in tools
    ) or '    <p class="desc">No selectable tools for the requested scope.</p>'

    hidden_fields = [
        ("client_id", req.client_id),
        ("redirect_uri", req.redirect_uri),
        ("response_type", req.response_type),
        ("scope", " ".join(req.scopes)),
        ("state", req.state or ""),
        ("code_challenge", req.code_challenge),
        ("code_challenge_method", req.code_challenge_method),
        ("csrf_token", csrf_token),
    ]
    hidden = "\n".join(
        f'    <input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k, v in hidden_fields
    )
    scope_list = ", ".join(esc(s) for s in req.scopes)

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
  <title>Authorize MCP connection · KDCube</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 540px; margin: 6vh auto; padding: 0 1rem; color: #1a1a1a; }}
    .card {{ border: 1px solid #e2e2e2; border-radius: 12px; padding: 1.5rem; }}
    h1 {{ font-size: 1.2rem; }}
    .details {{ background: #f5f7fa; border-radius: 8px; padding: .75rem 1rem; margin: 1rem 0; }}
    .details div {{ margin: .35rem 0; word-break: break-all; }}
    .k {{ display: inline-block; min-width: 92px; color: #555; font-size: .85rem; }}
    code, .scope {{ font-family: monospace; }}
    .badge {{ font-size: .72rem; padding: .1rem .4rem; border-radius: 6px; }}
    .badge.ok {{ background: #e6f4ea; color: #1e7e34; }}
    .badge.warn {{ background: #fdecea; color: #b71c1c; }}
    .warn-text {{ background: #fff8e1; border: 1px solid #ffe082; border-radius: 8px; padding: .6rem .8rem; font-size: .85rem; }}
    .tool {{ display: block; margin: .5rem 0; }}
    .tool .desc, .desc {{ display: block; color: #555; font-size: .85rem; margin-left: 1.5rem; }}
    .actions {{ display: flex; gap: .75rem; margin-top: 1.25rem; }}
    button {{ flex: 1; padding: .6rem; border-radius: 8px; border: 0; font-size: 1rem; cursor: pointer; }}
    .approve {{ background: #1565c0; color: #fff; }}
    .deny {{ background: #eee; color: #333; }}
    footer {{ margin-top: 1.25rem; font-size: .8rem; color: #888; text-align: center; }}
    footer a {{ color: #1565c0; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Authorize an MCP connection to KDCube</h1>
    <div class="details">
      <div><span class="k">Client</span> <code>{esc(req.client_id)}</code> {trust_badge}</div>
      <div><span class="k">Sends code to</span> <code>{esc(req.redirect_uri)}</code></div>
      <div><span class="k">Scope</span> <span class="scope">{scope_list}</span></div>
    </div>
    <p class="warn-text">Only approve if <strong>you</strong> started this connection and recognize the
    client and the redirect URL above. Approving grants read-only access to all conversation
    transcripts across every tenant/project.</p>
    <form method="post" action="/oauth/authorize/consent">
{hidden}
    <p>Select which capabilities to authorize for this connection:</p>
{tool_rows}
    <div class="actions">
      <button class="approve" type="submit" name="decision" value="approve">Approve</button>
      <button class="deny" type="submit" name="decision" value="deny">Deny</button>
    </div>
    </form>
  </div>
  <footer>Powered by <a href="https://kdcube.tech/" target="_blank" rel="noopener">KDCube</a> · {esc(issuer)}</footer>
</body>
</html>"""
