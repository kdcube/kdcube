# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import html
import json
import uuid

from fastapi.responses import HTMLResponse

from kdcube_ai_app.infra.plugin.app_readiness import (
    ApplicationLifecycleState,
    ApplicationReadinessSnapshot,
)


_RETRY_INITIAL_MS = 1500
_RETRY_MAX_MS = 20000
_RETRY_MAX_ATTEMPTS = 8


def application_site_wait_response(
    *,
    snapshot: ApplicationReadinessSnapshot,
) -> HTMLResponse:
    """Translate application readiness into a bounded browser wait state."""
    auto_retry = snapshot.state in {
        ApplicationLifecycleState.PENDING,
        ApplicationLifecycleState.PREPARING,
        ApplicationLifecycleState.RETRYING,
    }
    recovering = snapshot.state is ApplicationLifecycleState.RETRYING
    title = "This site is recovering" if recovering else "This site is getting ready"
    detail = (
        "The application is restarting. This page will continue when it is ready."
        if recovering
        else "The application is being prepared. This page will continue when it is ready."
    )
    if not auto_retry:
        title = "This site needs attention"
        detail = "An operator needs to restore the application before this page can open."

    config = json.dumps(
        {
            "autoRetry": auto_retry,
            "initialDelayMs": _RETRY_INITIAL_MS,
            "maxAttempts": _RETRY_MAX_ATTEMPTS,
            "maxDelayMs": _RETRY_MAX_MS,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).replace("<", "\\u003c")
    nonce = uuid.uuid4().hex
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    * {{ box-sizing: border-box; }}
    html, body {{ min-height: 100%; }}
    body {{ min-height: 100vh; min-height: 100dvh; margin: 0; background: #f7fafb; color: #132536; display: grid; place-items: center; padding: 32px 20px; }}
    main {{ width: min(100%, 34rem); }}
    .mark {{ width: 40px; height: 40px; margin-bottom: 22px; border: 3px solid #c7d9df; border-top-color: #087f8c; border-radius: 50%; animation: spin 1s linear infinite; }}
    h1 {{ margin: 0; font-size: 1.65rem; line-height: 1.2; letter-spacing: 0; }}
    p {{ margin: 12px 0 0; color: #50677a; font-size: 1rem; line-height: 1.55; letter-spacing: 0; }}
    #retry-status {{ min-height: 1.55em; color: #246678; }}
    button {{ margin-top: 22px; border: 1px solid #aac5cc; border-radius: 6px; background: #fff; color: #173f50; min-height: 40px; padding: 8px 14px; font: inherit; font-weight: 650; cursor: pointer; }}
    button:hover {{ border-color: #087f8c; color: #075f69; }}
    button:focus-visible {{ outline: 3px solid #8fd5d5; outline-offset: 2px; }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    @media (prefers-reduced-motion: reduce) {{ .mark {{ animation: none; border-top-color: #087f8c; }} }}
  </style>
</head>
<body>
  <main>
    <div class="mark" aria-hidden="true"></div>
    <h1>{html.escape(title)}</h1>
    <p>{html.escape(detail)}</p>
    <p id="retry-status" role="status" aria-live="polite"></p>
    <button id="retry-now" type="button">Try now</button>
  </main>
  <script nonce="{nonce}">
    (() => {{
      const config = {config};
      const status = document.getElementById("retry-status");
      const retry = document.getElementById("retry-now");
      const reload = () => window.location.reload();
      retry.addEventListener("click", reload);

      if (!config.autoRetry) {{
        status.textContent = "Try again after the application is restored.";
        return;
      }}

      let attempt = 0;
      const stop = (message) => {{ status.textContent = message; }};
      const schedule = () => {{
        if (attempt >= config.maxAttempts) {{
          stop("Automatic checks are paused because preparation is taking longer than expected. Use Try now to check again.");
          return;
        }}
        const delay = Math.min(config.maxDelayMs, config.initialDelayMs * (2 ** attempt));
        let remaining = Math.max(1, Math.ceil(delay / 1000));
        const renderCountdown = () => {{
          status.textContent = `Checking again in ${{remaining}} second${{remaining === 1 ? "" : "s"}}.`;
        }};
        renderCountdown();
        const countdown = window.setInterval(() => {{
          remaining = Math.max(1, remaining - 1);
          renderCountdown();
        }}, 1000);
        window.setTimeout(async () => {{
          window.clearInterval(countdown);
          attempt += 1;
          status.textContent = "Checking now...";
          try {{
            const response = await window.fetch(window.location.href, {{
              cache: "no-store",
              credentials: "same-origin",
              headers: {{ "Accept": "text/html" }},
            }});
            if (response.ok) {{
              window.location.replace(window.location.href);
              return;
            }}
            if (response.headers.get("X-KDCube-Site-Retryable") === "false") {{
              stop("The application needs attention. Try again after it is restored.");
              return;
            }}
          }} catch (error) {{
            // A transient network interruption follows the same bounded retry path.
          }}
          schedule();
        }}, delay);
      }};
      schedule();
    }})();
  </script>
</body>
</html>"""
    return HTMLResponse(
        content=document,
        status_code=503,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                f"script-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; "
                "form-action 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "Retry-After": "2",
            "X-KDCube-Application-State": snapshot.state.value,
            "X-KDCube-Site-Retryable": str(auto_retry).lower(),
            "X-Content-Type-Options": "nosniff",
        },
    )
