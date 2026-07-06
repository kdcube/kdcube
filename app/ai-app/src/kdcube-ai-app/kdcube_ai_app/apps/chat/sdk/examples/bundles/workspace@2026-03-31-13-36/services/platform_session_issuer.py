from __future__ import annotations

import html
import json
from typing import Any, Mapping

from fastapi.responses import HTMLResponse

import kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_providers.bundle_session_login as bundle_session_login
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.consent import (
    consent_authorize_fields,
    render_consent_authorize_hidden_inputs,
)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _str(value: Any) -> str:
    return str(value or "").strip()


def _request_path(request: Any) -> str:
    return _str(getattr(getattr(request, "url", None), "path", None))


def _request_query_value(request: Any, key: str) -> str:
    query = getattr(request, "query_params", None)
    if query is not None:
        try:
            return _str(query.get(key))
        except Exception:
            pass
    if isinstance(request, Mapping):
        raw_query = request.get("query") or request.get("query_params")
        if isinstance(raw_query, Mapping):
            return _str(raw_query.get(key))
    return ""


def _same_bundle_operation_url(request: Any, operation: str) -> str:
    path = _request_path(request)
    if not path:
        return operation
    base = path.rsplit("/", 1)[0]
    return f"{base}/{operation}"


def _hidden_input(name: str, value: Any) -> str:
    return (
        f'<input type="hidden" name="{html.escape(_str(name))}" '
        f'value="{html.escape(_str(value))}">'
    )


def _checkbox_row(*, name: str, value: str, title: str, description: str = "", detail: str = "") -> str:
    desc_html = f'<span class="desc">{html.escape(description)}</span>' if description else ""
    detail_html = f'<code>{html.escape(detail)}</code>' if detail else ""
    return (
        f'<label class="row">'
        f'<input type="checkbox" name="{html.escape(name)}" value="{html.escape(value)}" checked>'
        f'<span><b>{html.escape(title or value)}</b>{desc_html}{detail_html}</span>'
        f'</label>'
    )


async def issue_telegram_session(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    payload: Mapping[str, Any] | None = None,
    bundle_id: str,
):
    return await bundle_session_login.issue_telegram_session(
        entrypoint,
        request=request,
        telegram_init_data=telegram_init_data,
        payload=payload,
        bundle_id=bundle_id,
        operation=bundle_session_login.DEFAULT_TELEGRAM_OPERATION,
    )


async def issue_google_session(
    entrypoint: Any,
    *,
    request: Any = None,
    credential: str = "",
    id_token: str = "",
    payload: Mapping[str, Any] | None = None,
    bundle_id: str,
):
    return await bundle_session_login.issue_google_session(
        entrypoint,
        request=request,
        credential=credential,
        id_token=id_token,
        payload=payload,
        bundle_id=bundle_id,
        operation=bundle_session_login.DEFAULT_GOOGLE_OPERATION,
    )


def delegated_consent_page(
    entrypoint: Any,
    *,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    del entrypoint
    data = _dict(payload)
    brand = _str(data.get("brand")) or "KDCube"
    oauth_fields = consent_authorize_fields(data)
    client_id = oauth_fields["client_id"]
    redirect_uri = oauth_fields["redirect_uri"]
    resource = oauth_fields["resource"]
    scope = oauth_fields["scope"]
    form_action = _str(data.get("form_action")) or "/oauth/authorize/consent"
    csrf_token = _str(data.get("csrf_token"))
    grantor_label = _str(data.get("grantor_label") or data.get("grantor_subject"))
    grantor_subject = _str(data.get("grantor_subject"))
    signout_action = _str(data.get("signout_action")) or "/oauth/logout"
    return_to = _str(data.get("return_to"))

    hidden = render_consent_authorize_hidden_inputs(oauth_fields)
    hidden += "\n" + _hidden_input("csrf_token", csrf_token)

    grants = data.get("platform_grants") if isinstance(data.get("platform_grants"), list) else []
    grant_rows = "\n".join(
        _checkbox_row(
            name="platform_grants",
            value=_str(item.get("grant")),
            title=_str(item.get("label") or item.get("grant")),
            description=_str(item.get("description")),
            detail=_str(item.get("grant")),
        )
        for item in grants
        if isinstance(item, Mapping) and _str(item.get("grant"))
    ) or '<p class="empty">No platform delegation grants requested.</p>'

    tools = data.get("tools") if isinstance(data.get("tools"), list) else []
    tool_rows = "\n".join(
        _checkbox_row(
            name="tools",
            value=_str(item.get("name")),
            title=_str(item.get("label") or item.get("name")),
            description=_str(item.get("description")),
            detail=", ".join(_str(grant) for grant in (item.get("grants") or []) if _str(grant)),
        )
        for item in tools
        if isinstance(item, Mapping) and _str(item.get("name"))
    ) or '<p class="empty">No individual tools requested.</p>'

    account_html = ""
    if grantor_label or grantor_subject:
        account_html = f"""
      <section class="account">
        <div>
          <span class="eyebrow">KDCube account</span>
          <strong>{html.escape(grantor_label or grantor_subject)}</strong>
          {f'<code>{html.escape(grantor_subject)}</code>' if grantor_subject and grantor_subject != grantor_label else ''}
        </div>
        <form method="post" action="{html.escape(signout_action)}">
          {_hidden_input("next", return_to)}
          <button class="ghost" type="submit">Sign out</button>
        </form>
      </section>
"""

    html_body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Authorize connection · {html.escape(brand)}</title>
  <style>
    :root {{ --ink: #102032; --muted: #6b8198; --line: #dbe5ef; --accent: #00a99d; --blue: #2468c9; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; background: #f6fafb; color: var(--ink); font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(720px, calc(100vw - 32px)); margin: 32px auto; }}
    .card {{ background: #fff; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 8px 30px rgba(18, 38, 63, .08); overflow: hidden; }}
    header {{ padding: 24px 26px 18px; border-bottom: 1px solid var(--line); }}
    .badge {{ display: inline-flex; border-radius: 999px; background: #c6f3ee; color: #007c76; padding: 4px 10px; font-weight: 800; letter-spacing: .08em; font-size: 12px; text-transform: uppercase; }}
    h1 {{ margin: 14px 0 8px; font-size: 24px; line-height: 1.2; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.45; }}
    .body {{ padding: 20px 26px 24px; }}
    .details, .account {{ border: 1px solid var(--line); border-radius: 8px; background: #fbfdff; padding: 14px; margin-bottom: 16px; }}
    .detail {{ display: grid; grid-template-columns: 112px 1fr; gap: 12px; padding: 5px 0; word-break: break-word; }}
    .eyebrow {{ display: block; color: #7890a8; font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em; }}
    code {{ display: inline-block; background: #edf4fb; color: #24445f; border-radius: 5px; padding: 2px 6px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
    .account {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; }}
    .account strong {{ display: block; margin: 4px 0; }}
    h2 {{ margin: 22px 0 8px; font-size: 16px; }}
    .hint {{ margin-bottom: 10px; font-size: 14px; }}
    .row {{ display: grid; grid-template-columns: 24px 1fr; gap: 12px; align-items: start; border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin: 8px 0; cursor: pointer; }}
    .row:hover {{ border-color: #b9cce0; background: #fbfdff; }}
    .row input {{ margin-top: 3px; accent-color: var(--blue); }}
    .row b {{ display: block; }}
    .desc {{ display: block; color: var(--muted); font-size: 13px; margin: 2px 0 4px; }}
    .empty {{ border: 1px dashed var(--line); border-radius: 8px; padding: 12px; }}
    .actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 22px; }}
    button {{ border: 0; border-radius: 8px; padding: 12px 14px; font-weight: 800; cursor: pointer; }}
    button.primary {{ color: white; background: var(--blue); }}
    button.secondary {{ color: #30475f; background: #edf2f7; }}
    button.ghost {{ color: #c2185b; background: #fff; border: 1px solid #f0c8dc; padding: 8px 12px; }}
    button.busy {{ pointer-events: none; opacity: .72; }}
    button.busy::after {{ content: ""; display: inline-block; width: .8em; height: .8em; margin-left: .45rem; border: 2px solid currentColor; border-right-color: transparent; border-radius: 999px; animation: spin .75s linear infinite; vertical-align: -.12em; }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  </style>
</head>
<body>
  <main>
    <section class="card">
      <header>
        <span class="badge">Versatile custom consent</span>
        <h1>Authorize this external client</h1>
        <p>This page is rendered by the Versatile bundle. Connection Hub still validates CSRF, narrows grants, and issues the delegated credential.</p>
      </header>
      <div class="body">
        <section class="details">
          <div class="detail"><span class="eyebrow">Client</span><code>{html.escape(client_id)}</code></div>
          <div class="detail"><span class="eyebrow">Redirect</span><code>{html.escape(redirect_uri)}</code></div>
          <div class="detail"><span class="eyebrow">Resource</span><code>{html.escape(resource)}</code></div>
          <div class="detail"><span class="eyebrow">Scope</span><code>{html.escape(scope)}</code></div>
        </section>
{account_html}
        <form method="post" action="{html.escape(form_action)}">
{hidden}
          <h2>Platform delegation</h2>
          <p class="hint">Select which platform grants this client may derive from your account.</p>
{grant_rows}
          <h2>Tool access</h2>
          <p class="hint">Select the concrete tools this client may call.</p>
{tool_rows}
          <div class="actions">
            <button class="primary" type="submit" name="decision" value="approve">Approve</button>
            <button class="secondary" type="submit" name="decision" value="deny">Deny</button>
          </div>
        </form>
      </div>
    </section>
  </main>
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
          submitter.textContent = submitter.value === 'deny' ? 'Denying' : 'Approving';
        }}
        form.querySelectorAll('button').forEach((button) => {{
          if (button !== submitter) button.disabled = true;
        }});
      }});
    }});
  </script>
</body>
</html>"""
    return {"html": html_body}


async def google_login_page(
    entrypoint: Any,
    *,
    request: Any = None,
    bundle_id: str,
) -> HTMLResponse:
    login_cfg = await bundle_session_login.google_login_client_config(
        entrypoint,
        bundle_id=bundle_id,
        operation=bundle_session_login.DEFAULT_GOOGLE_OPERATION,
    )
    provider_cfg = _dict(login_cfg.get("provider"))
    client_id = _str(login_cfg.get("client_id"))
    next_url = _request_query_value(request, "next")
    auth_url = _same_bundle_operation_url(request, bundle_session_login.DEFAULT_GOOGLE_OPERATION)
    title = _str(provider_cfg.get("login_label") or "Sign in to KDCube")
    subtitle = _str(
        provider_cfg.get("login_description")
        or "Use your Google account to continue."
    )
    html_body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <script src="https://accounts.google.com/gsi/client" async defer></script>
  <style>
    body {{ margin: 0; font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #122033; background: #f7fafc; }}
    .page {{ min-height: 100vh; display: grid; place-items: center; padding: 24px; box-sizing: border-box; }}
    .panel {{ width: min(520px, 100%); background: white; border: 1px solid #d8e2ec; border-radius: 8px; padding: 26px; box-shadow: 0 8px 28px rgba(14, 35, 58, .08); }}
    .badge {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 4px 10px; background: #bff3ec; color: #008a84; font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }}
    h1 {{ font-size: 24px; line-height: 1.2; margin: 18px 0 8px; }}
    p {{ color: #607892; line-height: 1.45; margin: 0 0 18px; }}
    .status {{ margin-top: 18px; padding: 12px 14px; border-radius: 6px; background: #eef7ff; color: #244e7c; display: none; }}
    .status.error {{ background: #fff1f2; color: #a12637; }}
  </style>
</head>
<body>
  <main class="page">
    <section class="panel">
      <span class="badge">Connection Hub</span>
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(subtitle)}</p>
      <div id="g_id_onload"
           data-client_id="{html.escape(client_id)}"
           data-callback="handleCredentialResponse"
           data-auto_prompt="false"></div>
      <div class="g_id_signin"
           data-type="standard"
           data-size="large"
           data-theme="outline"
           data-text="signin_with"
           data-shape="rectangular"
           data-logo_alignment="left"></div>
      <div id="status" class="status"></div>
    </section>
  </main>
  <script>
    const authUrl = {json.dumps(auth_url)};
    const nextUrl = {json.dumps(next_url)};
    const statusEl = document.getElementById("status");
    function showStatus(text, isError) {{
      statusEl.textContent = text;
      statusEl.className = "status" + (isError ? " error" : "");
      statusEl.style.display = "block";
    }}
    async function handleCredentialResponse(response) {{
      showStatus("Signing in...", false);
      try {{
        const res = await fetch(authUrl, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          credentials: "include",
          body: JSON.stringify({{ credential: response.credential }})
        }});
        const data = await res.json().catch(() => ({{}}));
        if (!res.ok || !data.ok) {{
          throw new Error(data.detail || data.error || "Sign-in failed");
        }}
        showStatus("Signed in. Returning...", false);
        if (nextUrl) {{
          window.location.assign(nextUrl);
        }}
      }} catch (error) {{
        showStatus(error && error.message ? error.message : "Sign-in failed", true);
      }}
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(html_body)
