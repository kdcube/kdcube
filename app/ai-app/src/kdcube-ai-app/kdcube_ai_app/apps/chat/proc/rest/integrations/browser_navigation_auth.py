# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import html
from urllib.parse import quote, urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from kdcube_ai_app.auth.sessions import UserSession, UserType


def is_top_level_html_navigation(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    if "text/html" not in accept or "application/json" in accept:
        return False

    fetch_destination = request.headers.get("sec-fetch-dest", "").strip().lower()
    if fetch_destination and fetch_destination != "document":
        return False

    fetch_mode = request.headers.get("sec-fetch-mode", "").strip().lower()
    if fetch_mode and fetch_mode != "navigate":
        return False

    return True


_FRAME_DESTINATIONS = frozenset({"iframe", "frame"})


def is_framed_html_navigation(request: Request) -> bool:
    """A document navigation inside a frame, such as a widget pane the platform app embeds (W847).

    Browsers send ``Sec-Fetch-Dest: iframe`` (or ``frame``) with ``Sec-Fetch-Mode: navigate`` for it.
    It is a person looking at a page, not a script, so it must not get a JSON body.
    """
    accept = request.headers.get("accept", "").lower()
    if "text/html" not in accept or "application/json" in accept:
        return False
    if request.headers.get("sec-fetch-dest", "").strip().lower() not in _FRAME_DESTINATIONS:
        return False
    fetch_mode = request.headers.get("sec-fetch-mode", "").strip().lower()
    return not fetch_mode or fetch_mode == "navigate"


def is_browser_html_navigation(request: Request) -> bool:
    """A person's page load: top level, or framed inside a page of the same app."""
    return is_top_level_html_navigation(request) or is_framed_html_navigation(request)


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

# In a frame the sign-in page opens at the top level, returning to the page that framed this one
# (same origin), without the signed-out marker. Without script the link still works and returns to
# this frame's own path.
_TOP_LEVEL_NEXT_SCRIPT = (
    "<script>(function(){try{var a=document.getElementById('kdcube-sign-in');"
    "var t=window.top.location;var q=new URLSearchParams(t.search);q.delete('signed_out');"
    "var s=q.toString();var n=t.pathname+(s?'?'+s:'')+t.hash;"
    "var u=new URL(a.getAttribute('href'),t.href);u.searchParams.set('next',n);"
    "a.setAttribute('href',u.pathname+u.search);}catch(e){}})();</script>"
)


def _signed_out_page(sign_in: str, *, heading: str, framed: bool) -> HTMLResponse:
    target = ' target="_top"' if framed else ""
    script = _TOP_LEVEL_NEXT_SCRIPT if framed else ""
    return HTMLResponse(
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Signed out</title>"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"></head>"
        "<body style=\"font-family: system-ui, sans-serif; margin: 3rem auto; max-width: 28rem; padding: 0 1rem\">"
        f"<h1 style=\"font-size: 1.25rem\">{html.escape(heading)}</h1>"
        f"<p><a id=\"kdcube-sign-in\" href=\"{html.escape(sign_in, quote=True)}\"{target}>Sign in</a></p>"
        f"{script}</body></html>",
        status_code=200,
        headers=_NO_STORE,
    )


def platform_sign_in_redirect(request: Request) -> Response:
    from kdcube_ai_app.auth.bundle.login_lane import sign_in_bounce_path

    # The person has just signed out (the signed-out route adds the marker):
    # sending them to sign-in now would let an identity provider that is
    # still signed in upstream sign them straight back in (W260). Show that
    # they are signed out, with a Sign in link that carries no marker.
    signed_out = request.query_params.get("signed_out") == "1"
    return_to = request.url.path
    if signed_out:
        params = [(key, value) for key, value in request.query_params.multi_items() if key != "signed_out"]
        if params:
            return_to = f"{return_to}?{urlencode(params)}"
    elif request.url.query:
        return_to = f"{return_to}?{request.url.query}"
    sign_in = f"{sign_in_bounce_path()}?next={quote(return_to, safe='')}"
    if is_framed_html_navigation(request):
        # W847: never send a frame to the identity provider (its frame policy can blank it, and a sign-in
        # trapped in a pane signs in only the pane). Show the prompt; Sign in opens at the top level.
        return _signed_out_page(sign_in, heading="You are signed out." if signed_out else "Sign in to continue.",
                                framed=True)
    if signed_out:
        return _signed_out_page(sign_in, heading="You are signed out.", framed=False)
    return RedirectResponse(url=sign_in, status_code=302, headers=_NO_STORE)


def enforce_platform_session_user(
    request: Request,
    session: UserSession | None,
) -> Response | None:
    """Require a verified platform user and continue browser documents to login."""

    user_type = getattr(session, "user_type", UserType.ANONYMOUS)
    user_type_value = str(getattr(user_type, "value", user_type))
    if session is not None and user_type_value.lower() != UserType.ANONYMOUS.value:
        return None
    if is_browser_html_navigation(request):
        return platform_sign_in_redirect(request)
    raise HTTPException(status_code=401, detail="User is required.")


__all__ = [
    "enforce_platform_session_user",
    "is_browser_html_navigation",
    "is_framed_html_navigation",
    "is_top_level_html_navigation",
    "platform_sign_in_redirect",
]
