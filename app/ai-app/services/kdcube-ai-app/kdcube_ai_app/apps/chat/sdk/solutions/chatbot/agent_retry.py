from typing import Any, Callable, Dict, List, Optional, Awaitable

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.browser import ContextBrowser
from kdcube_ai_app.infra.service_hub.errors import ServiceException, is_context_limit_error

def _system_text_for_compaction(
        system_message_token_count_fn: Optional[Callable[[], int]],
        fallback_text: str = "",
) -> str:
    if system_message_token_count_fn is None:
        return fallback_text or ""
    try:
        tokens = int(system_message_token_count_fn() or 0)
    except Exception:
        tokens = 0
    if tokens <= 0:
        return fallback_text or ""
    # Timeline compaction estimates tokens ~ len(text)/4
    return "x" * max(1, tokens * 4)

def _ensure_model_blocks(ctx_browser: ContextBrowser, blocks: List[dict]) -> List[dict]:
    allowed = {"text", "image", "document"}
    if not blocks:
        return blocks
    # If already model blocks, return as-is to preserve cache markers.
    try:
        if all(isinstance(b, dict) and b.get("type") in allowed for b in blocks):
            # Heuristic: timeline blocks usually carry turn_id/author/path/meta
            if not any(isinstance(b, dict) and ("turn_id" in b or "author" in b or "path" in b or "meta" in b) for b in blocks):
                return blocks
    except Exception:
        pass
    try:
        # Always normalize through timeline to guarantee supported block types.
        return ctx_browser.timeline._blocks_to_message_blocks(blocks)
    except Exception:
        return [b for b in blocks if isinstance(b, dict) and b.get("type") in allowed]

def _debug_cache_points(ctx_browser: ContextBrowser, blocks: List[dict], label: str) -> None:
    try:
        trace = ctx_browser.timeline._build_cache_trace(blocks)
        cache_idx = trace.get("cache_idx") or []
        sigs = trace.get("sigs") or []
        points = []
        for idx in cache_idx:
            sig = sigs[idx] if idx < len(sigs) else {}
            points.append({"idx": idx, "type": sig.get("type"), "path": sig.get("path")})
        print(f"[cache_points:{label}] count={trace.get('count')} points={points}")
    except Exception:
        pass

async def retry_with_compaction(
        *,
        ctx_browser: ContextBrowser,
        system_text_fn: Optional[Callable[[], str]],
        system_message_token_count_fn: Optional[Callable[[], int]] = None,
        render_params: Optional[Dict[str, Any]] = None,
        agent_fn: Callable[..., Awaitable[Any]],
        emit_status: Optional[Callable[[List[str]], Any]] = None,
        sanitize_on_fail: bool = True,
        **kwargs,
) -> Any:
    if not ctx_browser:
        raise ValueError("ctx_browser is required to render timeline blocks")
    params = dict(render_params or {})
    if "cache_last" not in params:
        params["cache_last"] = True
    system_text = ""
    if system_text_fn is not None:
        try:
            system_text = system_text_fn() or ""
        except Exception:
            system_text = ""
    if not system_text and system_message_token_count_fn is not None:
        system_text = _system_text_for_compaction(system_message_token_count_fn, "")
    blocks = await ctx_browser.timeline.render(
        system_text=system_text,
        **params,
    )
    blocks = _ensure_model_blocks(ctx_browser, blocks)
    _debug_cache_points(ctx_browser, blocks, "attempt")
    try:
        return await agent_fn(blocks=blocks, **kwargs)
    except ServiceException as exc:
        if not sanitize_on_fail or not is_context_limit_error(exc.err):
            raise
        if emit_status:
            await emit_status(["compacting", "organizing the thread"])
        blocks = await ctx_browser.timeline.render(
            force_sanitize=True,
            system_text=system_text,
            **params,
        )
        blocks = _ensure_model_blocks(ctx_browser, blocks)
        _debug_cache_points(ctx_browser, blocks, "retry")
        return await agent_fn(blocks=blocks, **kwargs)
