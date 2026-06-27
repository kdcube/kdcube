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
    .tool input {{ margin-top: .2rem; width: 1.05rem; height: 1.05rem; accent-color: var(--accent); }}
    .tool b {{ font-size: .95rem; }}
    .tool .desc, .desc {{ display: block; color: var(--muted); font-size: .83rem; }}
    .actions {{ display: flex; gap: .75rem; margin-top: 1.4rem; }}
    button {{ flex: 1; padding: .7rem; border-radius: 10px; border: 0; font-size: 1rem; font-weight: 600; cursor: pointer; transition: filter .15s; }}
    button:hover {{ filter: brightness(.96); }}
    .approve {{ background: var(--accent); color: #fff; }}
    .deny {{ background: #eef1f5; color: #33414f; }}
    footer {{ margin-top: 1.3rem; font-size: .8rem; color: var(--muted); text-align: center; }}
    footer a {{ color: var(--accent-700); }}
  </style>
</head>
<body>
  <div class="brand">
    <div class="mark">KC</div>
    <div><b>KDCube</b><br><span>MCP authorization</span></div>
  </div>
  <div class="card">
    <h1>Authorize an MCP connection to KDCube</h1>
    <p class="sub">An application is requesting access to this workspace's data over MCP.</p>
    <div class="details">
      <div class="row"><span class="k">Client</span> <span><code>{esc(req.client_id)}</code> {trust_badge}</span></div>
      <div class="row"><span class="k">Sends code to</span> <code>{esc(req.redirect_uri)}</code></div>
      <div class="row"><span class="k">Scope</span> <span class="scope">{scope_list}</span></div>
    </div>
    <p class="warn-text">Only approve if <strong>you</strong> started this connection and recognize the
    client and the redirect URL above. Approving grants read-only access to all conversation
    transcripts across every tenant/project.</p>
    <form method="post" action="/oauth/authorize/consent">
{hidden}
    <p class="pick">Select which capabilities to authorize for this connection:</p>
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
