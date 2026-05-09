from __future__ import annotations

import asyncio
import hashlib
import os
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_bundle_call_context,
    get_current_bundle_id,
    get_current_request_context,
)
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.runtime.tool_module_bindings import get_bound_context, get_shared_browser_service


_SESSIONS: dict[str, "BrowserSession"] = {}
_SESSIONS_LOCK = asyncio.Lock()
_MAX_EVENT_LOG = 200
_SESSION_TTL_SECONDS = 30 * 60
_JANITOR_INTERVAL_SECONDS = 60
_MAX_SESSIONS = 64
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")
_JANITOR_TASK: Optional[asyncio.Task] = None


@dataclass
class BrowserPageState:
    page: Any
    events_bound: bool = False


@dataclass
class BrowserSession:
    key: str
    label: str
    context: Any
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    pages: dict[str, BrowserPageState] = field(default_factory=dict)
    console: list[dict[str, Any]] = field(default_factory=list)
    page_errors: list[dict[str, Any]] = field(default_factory=list)
    request_failures: list[dict[str, Any]] = field(default_factory=list)

    def touch(self) -> None:
        self.last_used_at = time.time()

    def append_event(self, bucket: str, event: dict[str, Any]) -> None:
        items = getattr(self, bucket)
        items.append(event)
        if len(items) > _MAX_EVENT_LOG:
            del items[: len(items) - _MAX_EVENT_LOG]


def _scrub_id(value: str) -> str:
    return _SAFE_ID_RE.sub("_", value.strip())[:120] or "main"


def _get_nested(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
    return cur


def _maybe(value: Any) -> str:
    text = str(value or "").strip()
    return text if text and text.lower() != "none" else ""


def _ensure_janitor_task() -> None:
    global _JANITOR_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _JANITOR_TASK is not None and not _JANITOR_TASK.done():
        try:
            if _JANITOR_TASK.get_loop() is loop:
                return
        except Exception:
            return
    _JANITOR_TASK = loop.create_task(_session_janitor(), name="kdcube-browser-session-janitor")


async def _session_janitor() -> None:
    global _JANITOR_TASK
    try:
        while True:
            await asyncio.sleep(_JANITOR_INTERVAL_SECONDS)
            async with _SESSIONS_LOCK:
                await BrowserBackend()._cleanup_sessions_locked(except_key="")
                if not _SESSIONS:
                    return
    except asyncio.CancelledError:
        raise
    finally:
        try:
            if _JANITOR_TASK is asyncio.current_task():
                _JANITOR_TASK = None
        except Exception:
            pass


def derive_session_identity(explicit_session_id: Optional[str] = None, *, bound_context: Any = None) -> tuple[str, str]:
    """
    Build a stable, non-secret Playwright session key for the current caller.

    The preferred scope is tenant/project/user/conversation/turn. This keeps all
    browser tabs in one ReAct turn together while preventing cross-user leakage.
    """
    if _maybe(explicit_session_id):
        raw = f"explicit:{explicit_session_id}"
        label = f"explicit:{_scrub_id(str(explicit_session_id))}"
    else:
        parts: list[tuple[str, str]] = []
        call_ctx = get_current_bundle_call_context()
        for key in (
            "tenant",
            "project",
            "user_id",
            "conversation_id",
            "turn_id",
            "request_id",
            "bundle_id",
        ):
            value = _maybe(call_ctx.get(key))
            if value:
                parts.append((key, value))

        req = get_current_request_context()
        for name, path in (
            ("tenant", "routing.tenant"),
            ("project", "routing.project"),
            ("bundle_id", "routing.bundle_id"),
            ("conversation_id", "conversation_id"),
            ("turn_id", "turn_id"),
            ("request_id", "request_id"),
            ("user_id", "user_id"),
        ):
            value = _maybe(_get_nested(req, path))
            if value and not any(k == name for k, _ in parts):
                parts.append((name, value))

        bundle_id = _maybe(get_current_bundle_id())
        if bundle_id and not any(k == "bundle_id" for k, _ in parts):
            parts.append(("bundle_id", bundle_id))

        comm = getattr(bound_context, "communicator", None) if bound_context is not None else None
        if comm is not None:
            comm_conversation = getattr(comm, "conversation", None) or {}
            comm_service = getattr(comm, "service", None) or {}
            for name, value in (
                ("tenant", getattr(comm, "tenant", None)),
                ("project", getattr(comm, "project", None)),
                ("user_id", getattr(comm, "user_id", None)),
                ("conversation_id", getattr(comm, "conversation_id", None) or (comm_conversation.get("conversation_id") if isinstance(comm_conversation, dict) else None)),
                ("turn_id", getattr(comm, "turn_id", None) or (comm_conversation.get("turn_id") if isinstance(comm_conversation, dict) else None)),
                ("session_id", comm_conversation.get("session_id") if isinstance(comm_conversation, dict) else None),
                ("request_id", comm_service.get("request_id") if isinstance(comm_service, dict) else None),
            ):
                text = _maybe(value)
                if text and not any(k == name for k, _ in parts):
                    parts.append((name, text))

        if not any(k == "turn_id" for k, _ in parts):
            # Last-resort fallback for ad hoc direct tool calls. It avoids
            # accidental sharing but reports the fallback in the label.
            task = asyncio.current_task()
            parts.append(("task", str(id(task) if task is not None else os.getpid())))

        raw = "|".join(f"{k}={v}" for k, v in parts)
        label = "|".join(f"{k}={_scrub_id(v)}" for k, v in parts)

    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"browser:{digest}", label


def _logical_fi_to_relative(path: str) -> Optional[pathlib.Path]:
    if not path.startswith("fi:"):
        return None
    logical = path[3:]
    for marker in (".outputs/", ".files/", ".attachments/"):
        if marker in logical:
            turn_id, rest = logical.split(marker, 1)
            return pathlib.Path(turn_id) / marker.strip("./") / rest
    return None


def _candidate_roots() -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    for raw in (OUTDIR_CV.get(""), WORKDIR_CV.get(""), os.getcwd()):
        if not raw:
            continue
        try:
            path = pathlib.Path(raw).expanduser().resolve()
        except Exception:
            continue
        if path not in roots:
            roots.append(path)
    return roots


def _current_turn_id(bound_context: Any = None) -> str:
    call_ctx = get_current_bundle_call_context()
    value = _maybe(call_ctx.get("turn_id"))
    if value:
        return _scrub_id(value)
    req = get_current_request_context()
    value = _maybe(_get_nested(req, "turn_id"))
    if value:
        return _scrub_id(value)
    comm = getattr(bound_context, "communicator", None) if bound_context is not None else None
    if comm is not None:
        conv = getattr(comm, "conversation", None) or {}
        if isinstance(conv, dict):
            value = _maybe(conv.get("turn_id"))
            if value:
                return _scrub_id(value)
        value = _maybe(getattr(comm, "turn_id", None))
        if value:
            return _scrub_id(value)
    return "browser"


def _is_relative_to(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except Exception:
        return False


def _resolve_local_file_path(url_or_path: str) -> pathlib.Path:
    value = str(url_or_path or "").strip()
    if not value:
        raise ValueError("url_or_path is required")

    logical_rel = _logical_fi_to_relative(value)
    roots = _candidate_roots()
    candidates: list[pathlib.Path] = []
    if logical_rel is not None:
        candidates.extend(root / logical_rel for root in roots)
    else:
        raw_path = pathlib.Path(value.replace("file://", "", 1)).expanduser()
        if raw_path.is_absolute():
            candidates.append(raw_path)
        else:
            candidates.extend(root / raw_path for root in roots)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if not resolved.exists():
            continue
        if any(_is_relative_to(resolved, root) for root in roots):
            return resolved
        raise PermissionError(
            f"Local browser file access is limited to runtime output/work roots; refused {resolved}"
        )

    raise FileNotFoundError(f"Could not resolve local file for browser: {url_or_path}")


def resolve_browser_url(url_or_path: str) -> tuple[str, dict[str, Any]]:
    value = str(url_or_path or "").strip()
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "data:")):
        return value, {"kind": "url"}
    if lowered.startswith("file://"):
        path = _resolve_local_file_path(value)
        return path.as_uri(), {"kind": "file", "path": str(path), "size_bytes": path.stat().st_size}
    path = _resolve_local_file_path(value)
    return path.as_uri(), {"kind": "file", "path": str(path), "size_bytes": path.stat().st_size}


class BrowserBackend:
    def __init__(self, *, bound_context: Any = None):
        self.bound_context = bound_context

    async def _get_session(
        self,
        *,
        session_id: Optional[str],
        width: int,
        height: int,
    ) -> BrowserSession:
        key, label = derive_session_identity(session_id, bound_context=self.bound_context)
        async with _SESSIONS_LOCK:
            await self._cleanup_sessions_locked(except_key=key)
            existing = _SESSIONS.get(key)
            if existing is not None:
                existing.touch()
                _ensure_janitor_task()
                return existing

            service = await get_shared_browser_service()
            browser = await service.get_browser()
            context = await browser.new_context(
                viewport={"width": max(int(width or 1280), 320), "height": max(int(height or 900), 240)},
                ignore_https_errors=True,
            )
            session = BrowserSession(key=key, label=label, context=context)
            _SESSIONS[key] = session
            _ensure_janitor_task()
            return session

    async def _cleanup_sessions_locked(self, *, except_key: str) -> None:
        now = time.time()
        stale = [
            key for key, session in _SESSIONS.items()
            if key != except_key and now - session.last_used_at > _SESSION_TTL_SECONDS
        ]
        if len(_SESSIONS) - len(stale) > _MAX_SESSIONS:
            ordered = sorted(
                (
                    (session.last_used_at, key)
                    for key, session in _SESSIONS.items()
                    if key != except_key and key not in stale
                )
            )
            overflow = len(_SESSIONS) - len(stale) - _MAX_SESSIONS
            stale.extend(key for _, key in ordered[: max(overflow, 0)])
        for key in stale:
            session = _SESSIONS.pop(key, None)
            if session is None:
                continue
            try:
                await session.context.close()
            except Exception:
                pass

    async def _get_page(
        self,
        session: BrowserSession,
        *,
        tab_id: str,
        create: bool = True,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> Any:
        safe_tab = _scrub_id(tab_id or "main")
        existing = session.pages.get(safe_tab)
        if existing is not None:
            session.touch()
            page = existing.page
            if width and height:
                await page.set_viewport_size({"width": max(int(width), 320), "height": max(int(height), 240)})
            return page
        if not create:
            raise KeyError(f"No open browser tab named {safe_tab!r}")

        page = await session.context.new_page()
        if width and height:
            await page.set_viewport_size({"width": max(int(width), 320), "height": max(int(height), 240)})
        state = BrowserPageState(page=page)
        session.pages[safe_tab] = state
        self._bind_page_events(session, safe_tab, state)
        session.touch()
        return page

    def _bind_page_events(self, session: BrowserSession, tab_id: str, state: BrowserPageState) -> None:
        if state.events_bound:
            return

        def on_console(msg: Any) -> None:
            try:
                loc = msg.location or {}
            except Exception:
                loc = {}
            session.append_event(
                "console",
                {
                    "tab_id": tab_id,
                    "type": getattr(msg, "type", None),
                    "text": getattr(msg, "text", None),
                    "location": loc,
                    "ts": time.time(),
                },
            )

        def on_page_error(exc: Exception) -> None:
            session.append_event(
                "page_errors",
                {"tab_id": tab_id, "message": str(exc), "ts": time.time()},
            )

        def on_request_failed(request: Any) -> None:
            try:
                failure = request.failure or {}
            except Exception:
                failure = {}
            session.append_event(
                "request_failures",
                {
                    "tab_id": tab_id,
                    "url": getattr(request, "url", None),
                    "method": getattr(request, "method", None),
                    "failure": failure,
                    "ts": time.time(),
                },
            )

        state.page.on("console", on_console)
        state.page.on("pageerror", on_page_error)
        state.page.on("requestfailed", on_request_failed)
        state.events_bound = True

    async def open_page(
        self,
        *,
        url_or_path: str,
        tab_id: str = "main",
        session_id: Optional[str] = None,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = 10000,
        settle_ms: int = 150,
        width: int = 1280,
        height: int = 900,
        text_limit: int = 2000,
        screenshot: bool = False,
        screenshot_full_page: bool = True,
        screenshot_path: Optional[str] = None,
    ) -> dict[str, Any]:
        session = await self._get_session(session_id=session_id, width=width, height=height)
        page = await self._get_page(session, tab_id=tab_id, width=width, height=height)
        target_url, resolved = resolve_browser_url(url_or_path)
        if wait_until not in {"commit", "domcontentloaded", "load", "networkidle"}:
            wait_until = "domcontentloaded"
        await page.goto(target_url, wait_until=wait_until, timeout=max(int(timeout_ms or 10000), 1000))
        if settle_ms and settle_ms > 0:
            await page.wait_for_timeout(min(int(settle_ms), 5000))
        return await self._state(
            session=session,
            page=page,
            tab_id=tab_id,
            text_limit=text_limit,
            resolved=resolved,
            screenshot=screenshot,
            screenshot_full_page=screenshot_full_page,
            screenshot_path=screenshot_path,
        )

    async def click(
        self,
        *,
        selector: str,
        tab_id: str = "main",
        session_id: Optional[str] = None,
        timeout_ms: int = 5000,
        settle_ms: int = 150,
        text_limit: int = 2000,
        screenshot: bool = False,
        screenshot_full_page: bool = True,
        screenshot_path: Optional[str] = None,
    ) -> dict[str, Any]:
        session = await self._get_session(session_id=session_id, width=1280, height=900)
        page = await self._get_page(session, tab_id=tab_id, create=False)
        await page.click(selector, timeout=max(int(timeout_ms or 5000), 500))
        if settle_ms and settle_ms > 0:
            await page.wait_for_timeout(min(int(settle_ms), 5000))
        return await self._state(
            session=session,
            page=page,
            tab_id=tab_id,
            text_limit=text_limit,
            screenshot=screenshot,
            screenshot_full_page=screenshot_full_page,
            screenshot_path=screenshot_path,
        )

    async def fill(
        self,
        *,
        selector: str,
        text: str,
        tab_id: str = "main",
        session_id: Optional[str] = None,
        timeout_ms: int = 5000,
        settle_ms: int = 150,
        text_limit: int = 2000,
        screenshot: bool = False,
        screenshot_full_page: bool = True,
        screenshot_path: Optional[str] = None,
    ) -> dict[str, Any]:
        session = await self._get_session(session_id=session_id, width=1280, height=900)
        page = await self._get_page(session, tab_id=tab_id, create=False)
        await page.fill(selector, text or "", timeout=max(int(timeout_ms or 5000), 500))
        if settle_ms and settle_ms > 0:
            await page.wait_for_timeout(min(int(settle_ms), 5000))
        return await self._state(
            session=session,
            page=page,
            tab_id=tab_id,
            text_limit=text_limit,
            screenshot=screenshot,
            screenshot_full_page=screenshot_full_page,
            screenshot_path=screenshot_path,
        )

    async def status(
        self,
        *,
        tab_id: str = "main",
        session_id: Optional[str] = None,
        text_limit: int = 2000,
        screenshot: bool = False,
        screenshot_full_page: bool = True,
        screenshot_path: Optional[str] = None,
    ) -> dict[str, Any]:
        session = await self._get_session(session_id=session_id, width=1280, height=900)
        page = await self._get_page(session, tab_id=tab_id, create=False)
        return await self._state(
            session=session,
            page=page,
            tab_id=tab_id,
            text_limit=text_limit,
            screenshot=screenshot,
            screenshot_full_page=screenshot_full_page,
            screenshot_path=screenshot_path,
        )

    async def close(
        self,
        *,
        tab_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        key, label = derive_session_identity(session_id, bound_context=self.bound_context)
        async with _SESSIONS_LOCK:
            session = _SESSIONS.get(key)
            if session is None:
                return {"session_key": key, "session_label": label, "closed": False, "reason": "not_found"}
            if tab_id:
                safe_tab = _scrub_id(tab_id)
                state = session.pages.pop(safe_tab, None)
                if state is not None:
                    await state.page.close()
                return {
                    "session_key": key,
                    "session_label": session.label,
                    "closed": state is not None,
                    "tab_id": safe_tab,
                    "remaining_tabs": sorted(session.pages.keys()),
                }

            _SESSIONS.pop(key, None)
            await session.context.close()
            return {
                "session_key": key,
                "session_label": session.label,
                "closed": True,
                "closed_tabs": sorted(session.pages.keys()),
            }

    async def _state(
        self,
        *,
        session: BrowserSession,
        page: Any,
        tab_id: str,
        text_limit: int,
        resolved: Optional[dict[str, Any]] = None,
        screenshot: bool = False,
        screenshot_full_page: bool = True,
        screenshot_path: Optional[str] = None,
    ) -> dict[str, Any]:
        session.touch()
        safe_tab = _scrub_id(tab_id or "main")
        title = ""
        ready_state = ""
        text = ""
        controls: list[dict[str, Any]] = []
        try:
            title = await page.title()
        except Exception:
            pass
        try:
            ready_state = await page.evaluate("() => document.readyState")
        except Exception:
            pass
        try:
            text = await page.locator("body").inner_text(timeout=1000)
        except Exception:
            text = ""
        try:
            controls = await page.evaluate(
                """
                () => Array.from(document.querySelectorAll(
                  'button,a,input,textarea,select,[role="button"],[onclick]'
                )).slice(0, 60).map((el, i) => {
                  const rect = el.getBoundingClientRect();
                  return {
                    index: i,
                    tag: el.tagName.toLowerCase(),
                    id: el.id || '',
                    classes: String(el.className || '').slice(0, 120),
                    text: (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').trim().slice(0, 160),
                    type: el.getAttribute('type') || '',
                    href: el.getAttribute('href') || '',
                    visible: !!(rect.width || rect.height),
                    selector_hint: el.id ? `#${CSS.escape(el.id)}` : ''
                  };
                })
                """
            )
        except Exception:
            controls = []

        limited_text = text[: max(int(text_limit or 0), 0)] if text_limit else ""
        screenshot_ret = None
        if screenshot:
            screenshot_ret = await self._capture_screenshot(
                page=page,
                tab_id=safe_tab,
                full_page=screenshot_full_page,
                requested_path=screenshot_path,
            )

        state = {
            "session_key": session.key,
            "session_label": session.label,
            "tab_id": safe_tab,
            "open_tabs": sorted(session.pages.keys()),
            "url": page.url,
            "title": title,
            "ready_state": ready_state,
            "resolved": resolved or None,
            "text_preview": limited_text,
            "text_symbols": len(text),
            "text_truncated": bool(text_limit and len(text) > int(text_limit)),
            "screenshot": screenshot_ret,
            "controls": controls,
            "console_errors": [
                item for item in session.console
                if item.get("tab_id") == safe_tab and item.get("type") in {"error", "warning"}
            ][-30:],
            "page_errors": [
                item for item in session.page_errors
                if item.get("tab_id") == safe_tab
            ][-30:],
            "request_failures": [
                item for item in session.request_failures
                if item.get("tab_id") == safe_tab
            ][-30:],
        }
        if screenshot_ret:
            state["artifact_type"] = "files"
            state["files"] = [screenshot_ret]
        return state

    async def _capture_screenshot(
        self,
        *,
        page: Any,
        tab_id: str,
        full_page: bool,
        requested_path: Optional[str],
    ) -> dict[str, Any]:
        outdir_raw = OUTDIR_CV.get("")
        if not outdir_raw:
            raise RuntimeError("OUTDIR_CV not set; cannot write browser screenshot artifact")
        outdir = pathlib.Path(outdir_raw).resolve()
        outdir.mkdir(parents=True, exist_ok=True)

        turn_id = _current_turn_id(self.bound_context)
        if requested_path:
            rel = pathlib.Path(str(requested_path).strip()).expanduser()
            if rel.is_absolute():
                raise PermissionError("screenshot_path must be OUTPUT_DIR-relative")
        else:
            stamp = int(time.time() * 1000)
            rel = pathlib.Path(turn_id) / "outputs" / "browser_screenshots" / f"{stamp}_{_scrub_id(tab_id)}.png"

        target = (outdir / rel).resolve()
        if not _is_relative_to(target, outdir):
            raise PermissionError("screenshot_path must stay under OUTPUT_DIR")
        target.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(target), full_page=bool(full_page))

        logical = None
        rel_posix = rel.as_posix()
        marker = f"{turn_id}/outputs/"
        if rel_posix.startswith(marker):
            logical = f"fi:{turn_id}.outputs/{rel_posix[len(marker):]}"
        return {
            "path": logical,
            "logical_path": logical,
            "artifact_path": logical,
            "physical_path": rel_posix,
            "filename": rel.name,
            "mime": "image/png",
            "kind": "file",
            "visibility": "internal",
            "size_bytes": target.stat().st_size,
            "full_page": bool(full_page),
            "description": "Browser screenshot captured for visual verification.",
        }


async def run_browser_action(action: str, params: dict[str, Any], *, bindings_source: Any = None) -> dict[str, Any]:
    # Force bound context resolution before the browser starts. This keeps the
    # tool aligned with normal SDK binding even though this backend currently
    # only needs contextvars and the shared browser service.
    bound_context = get_bound_context(bindings_source) if bindings_source is not None else None

    backend = BrowserBackend(bound_context=bound_context)
    if action == "open_page":
        return await backend.open_page(**params)
    if action == "click":
        return await backend.click(**params)
    if action == "fill":
        return await backend.fill(**params)
    if action == "status":
        return await backend.status(**params)
    if action == "close":
        return await backend.close(**params)
    raise ValueError(f"Unknown browser action: {action}")


def data_url_for_html(html: str) -> str:
    return "data:text/html;charset=utf-8," + quote(html or "")
