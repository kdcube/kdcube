from __future__ import annotations

from typing import Annotated, Any, Optional

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

from kdcube_ai_app.apps.chat.sdk.tools.backends.browser_backend import run_browser_action


_SERVICE = None
_INTEGRATIONS = None
_TOOL_SUBSYSTEM = None
TOOL_SUBSYSTEM = None
_COMMUNICATOR = None
COMMUNICATOR = None


def bind_service(svc):
    global _SERVICE
    _SERVICE = svc


def bind_integrations(integrations):
    global _INTEGRATIONS, _TOOL_SUBSYSTEM, TOOL_SUBSYSTEM, _COMMUNICATOR, COMMUNICATOR
    _INTEGRATIONS = integrations or {}
    _TOOL_SUBSYSTEM = _INTEGRATIONS.get("tool_subsystem") if isinstance(_INTEGRATIONS, dict) else None
    TOOL_SUBSYSTEM = _TOOL_SUBSYSTEM
    _COMMUNICATOR = getattr(_TOOL_SUBSYSTEM, "comm", None)
    COMMUNICATOR = _COMMUNICATOR


def _ok(ret: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, "ret": ret}


def _err(*, where: str, exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": type(exc).__name__,
            "message": str(exc),
            "where": where,
            "managed": True,
        },
        "ret": None,
    }


async def _host_internal_files(ret: Any) -> Any:
    if not isinstance(ret, dict):
        return ret
    files = ret.get("files")
    if str(ret.get("artifact_type") or "").strip() != "files" or not isinstance(files, list) or not files:
        return ret
    from kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context import host_files

    hosted = await host_files([row for row in files if isinstance(row, dict)], emit=False)
    if not isinstance(hosted, dict):
        return ret
    hosted_files = hosted.get("files")
    if not isinstance(hosted_files, list) or not hosted_files:
        return ret
    out = dict(ret)
    out["files"] = hosted_files
    out["hosted_count"] = hosted.get("hosted_count", len(hosted_files))
    out["emitted"] = False
    screenshot = out.get("screenshot")
    if isinstance(screenshot, dict):
        original_path = screenshot.get("physical_path") or screenshot.get("path")
        for row in hosted_files:
            if not isinstance(row, dict):
                continue
            row_path = row.get("physical_path") or row.get("path")
            if not original_path or row_path == original_path:
                out["screenshot"] = row
                break
    return out


class BrowserTools:
    @kernel_function(
        name="open_page",
        description=(
            "Open a URL, file:// URL, OUTPUT_DIR-relative path, or fi:<turn>.outputs/... artifact "
            "in a turn-scoped Playwright browser tab. Use this to verify generated HTML/apps in a real browser. "
            "The same turn reuses the same browser session; use different tab_id values for multiple tabs. "
            "Returns page title/url, visible text preview, controls, console warnings/errors, page errors, and request failures. "
            "Screenshots are optional internal image artifacts; request them only when visual state/layout matters because they add multimodal tokens."
        ),
    )
    async def open_page(
        self,
        url_or_path: Annotated[str, "URL, file:// URL, OUTPUT_DIR-relative path, or fi:<turn>.outputs/... logical file path."],
        tab_id: Annotated[str, "Named tab inside the current turn-scoped browser session."] = "main",
        wait_until: Annotated[str, "'commit'|'domcontentloaded'|'load'|'networkidle'."] = "domcontentloaded",
        timeout_ms: Annotated[int, "Navigation timeout in milliseconds."] = 10000,
        settle_ms: Annotated[int, "Extra wait after navigation before inspecting the page."] = 150,
        width: Annotated[int, "Viewport width."] = 1280,
        height: Annotated[int, "Viewport height."] = 900,
        text_limit: Annotated[int, "Visible page text preview size in characters."] = 2000,
        screenshot: Annotated[bool, "If true, write a PNG screenshot under OUTPUT_DIR as an internal file artifact. Use sparingly because screenshots add multimodal tokens."] = False,
        screenshot_full_page: Annotated[bool, "If true, capture full page; otherwise capture viewport."] = True,
        screenshot_path: Annotated[Optional[str], "Optional OUTPUT_DIR-relative screenshot path. Omit for an automatic turn outputs path."] = None,
        session_id: Annotated[Optional[str], "Optional explicit session id. Omit for normal turn-scoped behavior."] = None,
    ) -> Annotated[dict, "Envelope {ok,error,ret}. ret includes page state and browser diagnostics."]:
        try:
            ret = await run_browser_action(
                "open_page",
                {
                    "url_or_path": url_or_path,
                    "tab_id": tab_id,
                    "wait_until": wait_until,
                    "timeout_ms": timeout_ms,
                    "settle_ms": settle_ms,
                    "width": width,
                    "height": height,
                    "text_limit": text_limit,
                    "screenshot": screenshot,
                    "screenshot_full_page": screenshot_full_page,
                    "screenshot_path": screenshot_path,
                    "session_id": session_id,
                },
                bindings_source=globals(),
            )
            ret = await _host_internal_files(ret)
            return _ok(ret)
        except Exception as exc:
            return _err(where="browser_tools.open_page", exc=exc)

    @kernel_function(
        name="click",
        description=(
            "Click a CSS selector in an already-open turn-scoped Playwright tab, then return updated page diagnostics. "
            "Use after browser_tools.open_page when testing whether generated HTML controls actually work. "
            "Screenshots are optional internal image artifacts; request them only when visual state/layout matters because they add multimodal tokens."
        ),
    )
    async def click(
        self,
        selector: Annotated[str, "CSS selector to click, for example '#run' or 'button:nth-of-type(2)'."],
        tab_id: Annotated[str, "Named tab opened by browser_tools.open_page."] = "main",
        timeout_ms: Annotated[int, "Click timeout in milliseconds."] = 5000,
        settle_ms: Annotated[int, "Extra wait after click before inspecting the page."] = 150,
        text_limit: Annotated[int, "Visible page text preview size in characters."] = 2000,
        screenshot: Annotated[bool, "If true, write a PNG screenshot under OUTPUT_DIR after the click as an internal file artifact. Use sparingly because screenshots add multimodal tokens."] = False,
        screenshot_full_page: Annotated[bool, "If true, capture full page; otherwise capture viewport."] = True,
        screenshot_path: Annotated[Optional[str], "Optional OUTPUT_DIR-relative screenshot path. Omit for an automatic turn outputs path."] = None,
        session_id: Annotated[Optional[str], "Optional explicit session id. Omit for normal turn-scoped behavior."] = None,
    ) -> Annotated[dict, "Envelope {ok,error,ret}. ret includes page state and browser diagnostics."]:
        try:
            ret = await run_browser_action(
                "click",
                {
                    "selector": selector,
                    "tab_id": tab_id,
                    "timeout_ms": timeout_ms,
                    "settle_ms": settle_ms,
                    "text_limit": text_limit,
                    "screenshot": screenshot,
                    "screenshot_full_page": screenshot_full_page,
                    "screenshot_path": screenshot_path,
                    "session_id": session_id,
                },
                bindings_source=globals(),
            )
            ret = await _host_internal_files(ret)
            return _ok(ret)
        except Exception as exc:
            return _err(where="browser_tools.click", exc=exc)

    @kernel_function(
        name="fill",
        description=(
            "Fill a CSS selector in an already-open turn-scoped Playwright tab, then return updated page diagnostics. "
            "Screenshots are optional internal image artifacts; request them only when visual state/layout matters because they add multimodal tokens."
        ),
    )
    async def fill(
        self,
        selector: Annotated[str, "CSS selector for an input/textarea/content field."],
        text: Annotated[str, "Text to fill."],
        tab_id: Annotated[str, "Named tab opened by browser_tools.open_page."] = "main",
        timeout_ms: Annotated[int, "Fill timeout in milliseconds."] = 5000,
        settle_ms: Annotated[int, "Extra wait after fill before inspecting the page."] = 150,
        text_limit: Annotated[int, "Visible page text preview size in characters."] = 2000,
        screenshot: Annotated[bool, "If true, write a PNG screenshot under OUTPUT_DIR after the fill as an internal file artifact. Use sparingly because screenshots add multimodal tokens."] = False,
        screenshot_full_page: Annotated[bool, "If true, capture full page; otherwise capture viewport."] = True,
        screenshot_path: Annotated[Optional[str], "Optional OUTPUT_DIR-relative screenshot path. Omit for an automatic turn outputs path."] = None,
        session_id: Annotated[Optional[str], "Optional explicit session id. Omit for normal turn-scoped behavior."] = None,
    ) -> Annotated[dict, "Envelope {ok,error,ret}. ret includes page state and browser diagnostics."]:
        try:
            ret = await run_browser_action(
                "fill",
                {
                    "selector": selector,
                    "text": text,
                    "tab_id": tab_id,
                    "timeout_ms": timeout_ms,
                    "settle_ms": settle_ms,
                    "text_limit": text_limit,
                    "screenshot": screenshot,
                    "screenshot_full_page": screenshot_full_page,
                    "screenshot_path": screenshot_path,
                    "session_id": session_id,
                },
                bindings_source=globals(),
            )
            ret = await _host_internal_files(ret)
            return _ok(ret)
        except Exception as exc:
            return _err(where="browser_tools.fill", exc=exc)

    @kernel_function(
        name="status",
        description=(
            "Inspect an already-open turn-scoped Playwright tab without changing it. "
            "Returns title/url, visible text preview, controls, console warnings/errors, page errors, and request failures. "
            "Screenshots are optional internal image artifacts; request them only when visual state/layout matters because they add multimodal tokens."
        ),
    )
    async def status(
        self,
        tab_id: Annotated[str, "Named tab opened by browser_tools.open_page."] = "main",
        text_limit: Annotated[int, "Visible page text preview size in characters."] = 2000,
        screenshot: Annotated[bool, "If true, write a PNG screenshot under OUTPUT_DIR as an internal file artifact. Use sparingly because screenshots add multimodal tokens."] = False,
        screenshot_full_page: Annotated[bool, "If true, capture full page; otherwise capture viewport."] = True,
        screenshot_path: Annotated[Optional[str], "Optional OUTPUT_DIR-relative screenshot path. Omit for an automatic turn outputs path."] = None,
        session_id: Annotated[Optional[str], "Optional explicit session id. Omit for normal turn-scoped behavior."] = None,
    ) -> Annotated[dict, "Envelope {ok,error,ret}. ret includes page state and browser diagnostics."]:
        try:
            ret = await run_browser_action(
                "status",
                {
                    "tab_id": tab_id,
                    "text_limit": text_limit,
                    "screenshot": screenshot,
                    "screenshot_full_page": screenshot_full_page,
                    "screenshot_path": screenshot_path,
                    "session_id": session_id,
                },
                bindings_source=globals(),
            )
            ret = await _host_internal_files(ret)
            return _ok(ret)
        except Exception as exc:
            return _err(where="browser_tools.status", exc=exc)

    @kernel_function(
        name="close",
        description=(
            "Close a named tab or the whole current turn-scoped Playwright browser session. "
            "Normally optional; use it to reset browser state during debugging."
        ),
    )
    async def close(
        self,
        tab_id: Annotated[Optional[str], "Optional tab id. If omitted, closes the whole current browser session."] = None,
        session_id: Annotated[Optional[str], "Optional explicit session id. Omit for normal turn-scoped behavior."] = None,
    ) -> Annotated[dict, "Envelope {ok,error,ret}. ret describes what was closed."]:
        try:
            ret = await run_browser_action(
                "close",
                {"tab_id": tab_id, "session_id": session_id},
                bindings_source=globals(),
            )
            return _ok(ret)
        except Exception as exc:
            return _err(where="browser_tools.close", exc=exc)


tools = BrowserTools()
