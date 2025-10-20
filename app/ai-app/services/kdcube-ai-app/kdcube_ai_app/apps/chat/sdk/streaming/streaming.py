# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/streaming/streaming.py

from __future__ import annotations

import logging
import re, json
from typing import Dict, Any, Optional, Callable, Tuple, List

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger
from kdcube_ai_app.apps.chat.sdk.util import _json_loads_loose, _defence

logger = logging.getLogger(__name__)

# Accept 2..6 chevrons like <<, <<<, <<<<, and 2..6 closers >>, >>>, >>>>>
BEGIN_INT_RE  = re.compile(r"(?:<){2,6}\s*BEGIN\s+INTERNAL\s+THINKING\s*(?:>){2,6}", re.I)
BEGIN_USER_RE = re.compile(r"(?:<){2,6}\s*BEGIN\s+USER[-–—]?\s*FACING\s+THINKING\s*(?:>){2,6}", re.I)
BEGIN_JSON_RE = re.compile(r"(?:<){2,6}\s*BEGIN\s+STRUCTURED\s+JSON\s*(?:>){2,6}", re.I)

END_INT_RE    = re.compile(r"(?:<){2,6}\s*END\s+INTERNAL\s+THINKING\s*(?:>){2,6}", re.I)
END_USER_RE   = re.compile(r"(?:<){2,6}\s*END\s+USER[-–—]?\s*FACING\s+THINKING\s*(?:>){2,6}", re.I)
END_JSON_RE   = re.compile(r"(?:<){2,6}\s*END\s+STRUCTURED\s+JSON\s*(?:>){2,6}", re.I)

# =============================================================================
# Paranoid markers (ONLY these are supported)
# =============================================================================

PARA_INT_OPEN  = r"<<<\s*BEGIN\s+INTERNAL\s+THINKING\s*>>>"
PARA_USER_OPEN = r"<<<\s*BEGIN\s+USER[-\u2013\u2014]?\s*FACING\s+THINKING\s*>>>"
PARA_JSON_OPEN = r"<<<\s*BEGIN\s+STRUCTURED\s+JSON\s*>>>"

PARA_INT_CLOSE  = r"<<<\s*END\s+INTERNAL\s+THINKING\s*>>>"
PARA_USER_CLOSE = r"<<<\s*END\s+USER[-\u2013\u2014]?\s*FACING\s+THINKING\s*>>>"
PARA_JSON_CLOSE = r"<<<\s*END\s+STRUCTURED\s+JSON\s*>>>"

INT_RE  = re.compile(PARA_INT_OPEN, re.I)
USER_RE = re.compile(PARA_USER_OPEN, re.I)
JSON_RE = re.compile(PARA_JSON_OPEN, re.I)

# We never *require* closers, but if a model emits them, strip from user-facing output.
# USER_CLOSER_STRIPPERS = [
#     re.compile(PARA_USER_CLOSE, re.I),
#     re.compile(PARA_INT_CLOSE, re.I),
#     re.compile(PARA_JSON_CLOSE, re.I),
#     # also safeguard: if any open markers appear inside the user section, strip them
#     USER_RE, INT_RE, JSON_RE,
# ]

USER_CLOSER_STRIPPERS = [
    END_USER_RE, END_INT_RE, END_JSON_RE,
    USER_RE, INT_RE, JSON_RE,                   # strict <<< ... >>>
    BEGIN_USER_RE, BEGIN_INT_RE, BEGIN_JSON_RE, # tolerant << ... <<<<
]

# Rolling safety window so we never cut a marker across chunk boundaries
MAX_MARKER_LEN = max(
    len("<<< BEGIN INTERNAL THINKING >>>"),
    len("<<< BEGIN USER-FACING THINKING >>>"),
    len("<<< BEGIN STRUCTURED JSON >>>"),
    len("<<< END INTERNAL THINKING >>>"),
    len("<<< END USER-FACING THINKING >>>"),
    len("<<< END STRUCTURED JSON >>>"),
)
HOLDBACK = MAX_MARKER_LEN + 8

def _md_unquote(s: str) -> str:
    # Strip leading markdown blockquote prefixes (`>`, `>>`) safely
    lines = s.splitlines()
    return "\n".join(re.sub(r'^\s{0,3}>\s?', '', ln) for ln in lines)


# =============================================================================
# Public helper: append strict 3-section protocol (paranoid markers only)
# =============================================================================

def _add_3section_protocol(base: str, json_shape_hint: str) -> str:
    """
    Appends a strict output protocol to the system prompt telling the model to use the
    paranoid markers. We explicitly tell it NOT to emit END markers.
    """
    return (
            base.rstrip()
            + "\n\nCRITICAL OUTPUT PROTOCOL — FOLLOW EXACTLY:\n"
              "• Produce THREE sections in this exact order. Use each START marker below exactly once.\n"
              "• Do NOT write any closing tags/markers like <<< END … >>> or </…>.\n"
              "• The third section must be a fenced JSON block and contain ONLY JSON.\n\n"
              "1) <<< BEGIN INTERNAL THINKING >>>\n"
              "   (private notes in Markdown; not shown to user)\n"
              "2) <<< BEGIN USER-FACING THINKING >>>\n"
              "   (short, user-friendly status; Markdown only — no JSON)\n"
              "3) <<< BEGIN STRUCTURED JSON >>>\n"
              f"   ```json\n{json_shape_hint}\n```\n"
              "Return exactly these three sections, in order, once."
    )

def _get_3section_protocol(json_shape_hint: str) -> str:
    """
    Get a strict output protocol to the system prompt telling the model to use the
    paranoid markers. We explicitly tell it NOT to emit END markers.
    """
    return ("\n\nCRITICAL OUTPUT PROTOCOL — FOLLOW EXACTLY:\n"
            "• Produce THREE sections in this exact order. Use each START marker below exactly once.\n"
            "• Do NOT write any closing tags/markers like <<< END … >>> or </…>.\n"
            "• The third section must be a fenced JSON block and contain ONLY JSON.\n\n"
            "1) <<< BEGIN INTERNAL THINKING >>>\n"
            "   (private notes in Markdown; not shown to user)\n"
            "2) <<< BEGIN USER-FACING THINKING >>>\n"
            "   (short, user-friendly status; Markdown only — no JSON)\n"
            "3) <<< BEGIN STRUCTURED JSON >>>\n"
            f"   ```json\n{json_shape_hint}\n```\n"
            "Return exactly these three sections, in order, once."
            )

# chat/sdk/streaming/streaming.py

PARA_INT2_OPEN  = r"<<<\s*BEGIN\s+INTERNAL\s+THINKING\s*>>>"
PARA_JSON2_OPEN = r"<<<\s*BEGIN\s+STRUCTURED\s+JSON\s*>>>"

def _add_2section_protocol(base: str, json_shape_hint: str) -> str:
    """
    Strict 2-part protocol:
      1) INTERNAL THINKING (streamed to user, with optional redaction)
      2) STRUCTURED JSON (buffered, validated)
    """
    return (
            base.rstrip()
            + "\n\nCRITICAL OUTPUT PROTOCOL — FOLLOW EXACTLY:\n"
              "• Produce TWO sections in this exact order. Use each START marker below exactly once.\n"
              "• Do NOT write any closing tags/markers like <<< END... >>> or </…>.\n\n"
              "1) <<< BEGIN INTERNAL THINKING >>>\n"
              "   (brief progress notes in Markdown; avoid credentials & long stack traces)\n"
              "2) <<< BEGIN STRUCTURED JSON >>>\n"
              f"   ```json\n{json_shape_hint}\n```\n"
              "Return exactly these two sections, in order, once."
    )

def _get_2section_protocol(json_shape_hint: str) -> str:
    """
    Strict 2-part protocol:
      1) INTERNAL THINKING (streamed to user, with optional redaction)
      2) STRUCTURED JSON (buffered, validated)
    """
    return (
        "\n\nCRITICAL OUTPUT PROTOCOL — FOLLOW EXACTLY:\n"
        "• Produce TWO sections in this exact order. Use each START marker below exactly once.\n"
        "• Do NOT write any closing tags/markers like <<< END... >>> or </…>.\n\n"
        "1) <<< BEGIN INTERNAL THINKING >>>\n"
        "   (brief progress notes in Markdown; avoid credentials & long stack traces)\n"
        "2) <<< BEGIN STRUCTURED JSON >>>\n"
        f"   ```json\n{json_shape_hint}\n```\n"
        "Return exactly these two sections, in order, once."
    )

_REDACTIONS = [
    # API keys / tokens
    (re.compile(r"(?i)\b(AKIA|ASIA|aws(_|\s*)secret|x-api-key|api[_\- ]?key|token|bearer)\b[^\n]{0,120}", re.I), "[REDACTED]"),
    # URLs with creds
    (re.compile(r"https?://[^/\s]+:[^@\s]+@[^/\s]+", re.I), "https://[REDACTED]@host"),
    # Private file paths
    (re.compile(r"\b(/|[A-Z]:\\)(Users|home)/[^\s]{1,64}", re.I), "[PATH REDACTED]"),
    # Long stack traces (truncate aggressively)
    (re.compile(r"(Traceback \(most recent call last\):[\s\S]{200,4000})", re.I), "Traceback: [TRUNCATED]"),
]

def _redact_stream_text(s: str) -> str:
    out = s
    for pat, repl in _REDACTIONS:
        out = pat.sub(repl, out)
    return out

# =============================================================================
# Internal streaming parser (used by both the runtime and unit tests)
# =============================================================================

def _maybe_unescape_wrapped_string(s: str) -> str:
    """
    If the whole payload is a single quoted JSON string (e.g. "...\n```json\n{...}\n```"),
    decode it once to unescape \n, \" etc. If it isn't such a string, return as-is.
    """
    t = (s or "").strip()
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        try:
            return json.loads(t)
        except Exception:
            pass
    return s

def _sanitize_ws_and_invisibles(s: str) -> str:
    # Remove BOM / zero-width junk that can break regexes
    return (s or "").replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")

def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in (" ", "\t", "\r", "\n"):
        i += 1
    return i


def _strip_service_markers_from_user(text: str) -> str:
    out = text
    for pat in USER_CLOSER_STRIPPERS:
        out = pat.sub("", out)
    return out


def _find_earliest(buf: str, start_hint: int, patterns: List[Tuple[re.Pattern, str]]) -> Tuple[Optional[re.Match], Optional[str]]:
    """
    Search all patterns starting a little earlier to catch split markers.
    Return (match, which_name) for the earliest match; else (None, None).
    """
    start = max(0, start_hint - HOLDBACK)
    found = []
    for pat, name in patterns:
        m = pat.search(buf, start)
        if m:
            found.append((m, name))
    if not found:
        return None, None
    m, name = min(found, key=lambda t: t[0].start())
    return m, name

# Enhanced patterns and buffer management for closing marker detection
CLOSING_MARKER_PATTERNS = [
    re.compile(PARA_USER_CLOSE, re.I),
    re.compile(PARA_INT_CLOSE, re.I),
    re.compile(PARA_JSON_CLOSE, re.I),
    # Also catch any stray opening markers that appear in user content
    re.compile(PARA_INT_OPEN, re.I),
    re.compile(PARA_USER_OPEN, re.I),
    re.compile(PARA_JSON_OPEN, re.I),
]

# Increase holdback to be more conservative with closing markers
CLOSING_MARKER_HOLDBACK = 50  # Hold back more aggressively in user mode

class _SectionsStreamParser:
    """
    Modes:
      pre   -> before any BEGIN markers; we buffer until we see a BEGIN
      internal -> after <<< BEGIN INTERNAL THINKING >>>, captured only
      user  -> after <<< BEGIN USER-FACING THINKING >>>, streamed via callback
      json  -> after <<< BEGIN STRUCTURED JSON >>>, captured only
    """
    import re as _re

    # BEGIN markers (existing)
    # _INT_RE  = _re.compile(r"<<<?\s*BEGIN\s+INTERNAL\s+THINKING\s*>>>?", _re.I)
    # _USER_RE = _re.compile(r"<<<?\s*BEGIN\s+USER[-–—]?\s*FACING\s*THINKING\s*>>>?", _re.I)
    # _JSON_RE = _re.compile(r"<<<?\s*BEGIN\s+STRUCTURED\s+JSON\s*>>>?", _re.I)

    _INT_RE  = BEGIN_INT_RE
    _USER_RE = BEGIN_USER_RE
    _JSON_RE = BEGIN_JSON_RE

    # NEW: things we never want to leak to the UI during USER streaming
    _UNWANTED_USER_MARKERS = [
        _re.compile(r"<<<?\s*END\s+USER[-–—]?\s*FACING\s*THINKING\s*>>>?", _re.I),
        _re.compile(r"<<<?\s*END\s+INTERNAL\s+THINKING\s*>>>?", _re.I),
        _re.compile(r"<<<?\s*END\s+STRUCTURED\s+JSON\s*>>>?", _re.I),
        # If the model echoes a BEGIN back into the user-facing stream, scrub it too:
        _re.compile(r"<<<?\s*BEGIN\s+USER[-–—]?\s*FACING\s*THINKING\s*>>>?", _re.I),
    ]

    def __init__(self, *, on_user):
        self.buf = ""
        self.mode = "pre"
        self.emit_from = 0
        self.on_user = on_user
        self.internal = ""
        self.user = ""
        self.json = ""
        # keep small holdback so we don’t cut markers across chunk boundaries
        self.HOLDBACK = 48

    def _strip_unwanted_user_markers(self, s: str) -> str:
        for pat in self._UNWANTED_USER_MARKERS:
            s = pat.sub("", s)
        return s

    def _skip_ws(self, i: int) -> int:
        while i < len(self.buf) and self.buf[i] in (" ", "\t", "\r", "\n"):
            i += 1
        return i

    async def feed(self, piece: str):
        if not piece:
            return
        prev_len = len(self.buf)
        self.buf += piece

        def _search(pat):
            start = max(0, prev_len - self.HOLDBACK, self.emit_from - self.HOLDBACK)
            return pat.search(self.buf, start)

        while True:
            if self.mode == "pre":
                m = (_SectionsStreamParser._INT_RE.search(self.buf, max(0, prev_len - self.HOLDBACK))
                     or _SectionsStreamParser._USER_RE.search(self.buf, max(0, prev_len - self.HOLDBACK))
                     or _SectionsStreamParser._JSON_RE.search(self.buf, max(0, prev_len - self.HOLDBACK)))
                if not m:
                    # nothing yet; just wait (do not emit anything in pre)
                    break

                tag = m.re
                pre_slice = self.buf[self.emit_from:m.start()]
                if pre_slice:
                    # internal prelude lives here; we don’t show it
                    self.internal += pre_slice
                self.emit_from = self._skip_ws(m.end())

                if tag is _SectionsStreamParser._INT_RE:
                    self.mode = "internal"
                elif tag is _SectionsStreamParser._USER_RE:
                    self.mode = "user"
                else:
                    self.mode = "json"
                continue

            if self.mode == "internal":
                # look for next begin (USER or JSON)
                m_user = _search(_SectionsStreamParser._USER_RE)
                m_json = _search(_SectionsStreamParser._JSON_RE)
                m = min([x for x in (m_user, m_json) if x], key=lambda x: x.start()) if (m_user or m_json) else None
                if not m:
                    # accumulate internal silently
                    # keep small safe end (no need to stream)
                    safe_end = max(self.emit_from, len(self.buf) - self.HOLDBACK)
                    if safe_end > self.emit_from:
                        self.internal += self.buf[self.emit_from:safe_end]
                        self.emit_from = safe_end
                    break

                self.internal += self.buf[self.emit_from:m.start()]
                self.emit_from = self._skip_ws(m.end())
                self.mode = "user" if m.re is _SectionsStreamParser._USER_RE else "json"
                continue

            if self.mode == "user":
                # look for JSON begin (end of user section)
                m_json = _search(_SectionsStreamParser._JSON_RE)
                if not m_json:
                    # stream a safe window (but scrub unwanted markers)
                    safe_end = max(self.emit_from, len(self.buf) - self.HOLDBACK)
                    if safe_end > self.emit_from:
                        raw = self.buf[self.emit_from:safe_end]
                        cleaned = self._strip_unwanted_user_markers(raw)
                        if cleaned:
                            await self.on_user(cleaned, completed=False)
                            self.user += cleaned
                        self.emit_from = safe_end
                    break

                # emit everything up to JSON marker (scrubbed), then switch
                raw = self.buf[self.emit_from:m_json.start()]
                cleaned = self._strip_unwanted_user_markers(raw)
                if cleaned:
                    await self.on_user(cleaned, completed=False)
                    self.user += cleaned
                self.emit_from = self._skip_ws(m_json.end())
                self.mode = "json"
                # json section begins; loop to handle tail capture
                continue

            if self.mode == "json":
                # rest is JSON tail; just accumulate
                self.json += self.buf[self.emit_from:]
                self.emit_from = len(self.buf)
                break

            break  # safety

    async def finalize(self):
        # flush any remaining text per mode
        if self.mode == "internal":
            self.internal += self.buf[self.emit_from:]
        elif self.mode == "user":
            raw = self.buf[self.emit_from:]
            cleaned = self._strip_unwanted_user_markers(raw)
            if cleaned:
                await self.on_user(cleaned, completed=False)
                self.user += cleaned
        elif self.mode == "json":
            self.json += self.buf[self.emit_from:]
        # caller will send the completed=True signal

# =============================================================================
# Public streaming entrypoint
# =============================================================================
async def _stream_agent_sections_to_json(
        svc: ModelServiceBase,
        *,
        client_name: str,
        client_role: str,
        sys_prompt: str|SystemMessage,
        user_msg: str,
        schema_model,
        on_thinking_delta=None,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Stream 3 sections using ONLY paranoid markers:
      1) <<< BEGIN INTERNAL THINKING >>> (capture only)
      2) <<< BEGIN USER-FACING THINKING >>> (stream via on_thinking_delta)
      3) <<< BEGIN STRUCTURED JSON >>> (strict; returned)

    The parser keeps a small rolling HOLDBACK so we never cut a marker across chunk
    boundaries, but otherwise streams promptly. Service markers are never emitted
    to the UI.
    """
    run_logger = AgentLogger("ThreeSectionStream", getattr(svc.config, "log_level", "INFO"))
    sys_message_len = len(sys_prompt) if isinstance(sys_prompt, str) else len(sys_prompt.content)
    run_logger.start_operation(
        "three_section_stream",
        client_role=client_role,
        client_name=client_name,
        system_prompt_len=sys_message_len,
        user_msg_len=len(user_msg),
        expected_format=getattr(schema_model, "__name__", str(schema_model)),
    )

    deltas = 0

    async def _emit_user(piece: str, completed: bool = False, **kwargs):
        nonlocal deltas
        if not piece and not completed:
            return
        if on_thinking_delta is not None:
            await on_thinking_delta(piece or "", completed, **kwargs)
            if piece:
                deltas += 1

    parser = _SectionsStreamParser(on_user=_emit_user)

    async def on_delta(piece: str):
        await parser.feed(piece)

    async def on_complete(ret):
        await parser.finalize()
        # send the "completed" signal to the UI
        await _emit_user("", completed=True)
        run_logger.log_step("stream_complete", {"deltas": deltas})

    # ---- stream with model ----
    client = svc.get_client(client_name)
    cfg = svc.describe_client(client, role=client_role)

    system_message = SystemMessage(content=sys_prompt) if isinstance(sys_prompt, str) else sys_prompt
    messages = [system_message, HumanMessage(content=user_msg)]
    await svc.stream_model_text_tracked(
        client,
        messages,
        on_delta=on_delta,
        on_complete=on_complete,
        temperature=temperature,
        max_tokens=max_tokens,
        client_cfg=cfg,
        role=client_role,
    )

    internal_thinking = parser.internal
    user_thinking = parser.user
    json_tail = parser.json

    # ---- parse JSON (tolerate fenced block) ----
    def _try_parse_json(raw_json: str) -> Optional[Dict[str, Any]]:
        raw = _sanitize_ws_and_invisibles(_maybe_unescape_wrapped_string(raw_json))
        if not raw.strip():
            return None

        # Strip any markdown blockquotes, tolerate fences ANYWHERE, leading newlines, etc.
        raw = _md_unquote(raw).strip()

        # Prefer ```json ... ```; otherwise any ``` ... ```
        m_fenced = re.search(r"```json\s*(.+?)\s*```", raw, re.I | re.S)
        if m_fenced:
            candidate = m_fenced.group(1).strip()
        else:
            m_any = re.search(r"```\s*(.+?)\s*```", raw, re.S)
            candidate = (m_any.group(1).strip() if m_any else raw)

        candidate = candidate.strip().strip("`")

        # Take the last JSON object (handles trailing narration)
        m_obj = re.search(r"\{[\s\S]*\}\s*$", candidate)
        if not m_obj:
            return None

        try:
            loaded = _json_loads_loose(m_obj.group(0)) or {}
            return schema_model.model_validate(loaded).model_dump()
        except Exception:
            return None
    # def _try_parse_json(raw_json: str) -> Optional[Dict[str, Any]]:
    #     raw = (raw_json or "").strip()
    #     # handle fenced block if present
    #     if raw.startswith("```"):
    #         nl = raw.find("\n")
    #         raw = raw[nl + 1 :] if nl >= 0 else ""
    #         end = raw.rfind("```")
    #         if end >= 0:
    #             raw = raw[:end]
    #     raw = raw.strip().strip("`")
    #     if not raw:
    #         return None
    #     m = re.search(r"\{.*\}\s*$", raw, re.S)
    #     if not m:
    #         return None
    #     try:
    #         loaded = _json_loads_loose(m.group(0)) or {}
    #         return schema_model.model_validate(loaded).model_dump()
    #     except Exception:
    #         return None

    data = _try_parse_json(json_tail)
    raw_data = json_tail
    error = None

    if data is None and json_tail.strip():
        # try format fixer once
        fix = await svc.format_fixer.fix_format(
            raw_output=json_tail,
            expected_format=getattr(schema_model, "__name__", str(schema_model)),
            input_data=user_msg,
            system_prompt=sys_prompt,
        )
        if fix.get("success"):
            try:
                data = schema_model.model_validate(fix["data"]).model_dump()
            except Exception as ex:
                error = f"JSON parse after format fix failed: {ex}"
                data = None

    if data is None:
        try:
            data = schema_model.model_validate({}).model_dump()
        except Exception:
            data = {}

    try:
        run_logger.log_step(
            "streaming_with_structured_output",
            {
                "error": error,
                "internal_chars": len(internal_thinking),
                "user_chars": len(user_thinking),
                "json_tail_chars": len(parser.json),
                "provider": getattr(cfg, "provider", None),
                "model": getattr(cfg, "model_name", None),
                "role": client_role,
            },
        )
    except Exception:
        pass

    run_logger.finish_operation(
        True,
        "three_section_stream_complete",
        result_preview={
            "error": error,
            "internal_len": len(internal_thinking),
            "user_len": len(user_thinking),
            "has_agent_response": bool(data),
            "provider": getattr(cfg, "provider", None),
            "model": getattr(cfg, "model_name", None),
        },
    )

    return {
        "agent_response": data,
        "log": {
            "error": error,
            "raw_data": raw_data
        },
        "internal_thinking": internal_thinking,
        "user_thinking": user_thinking,
    }

# =============================================================================
# Simple (non-sectioned) structured-output streaming
# =============================================================================

async def _stream_simple_structured_json(
        svc: ModelServiceBase,
        *,
        client_name: str,
        client_role: str,
        sys_prompt: str|SystemMessage,
        user_msg: str,
        schema_model,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Stream plain text (no 3-part protocol), buffer everything, then parse it as JSON
    matching `schema_model`. If parsing fails, run the service format fixer once and
    validate again. Returns {agent_response, log:{error, raw_data}}.

    Prompting tip: tell the model "Return ONLY a JSON object ..." and (optionally)
    include a fenced `json` schema hint. The parser tolerates code fences in output.
    """
    run_logger = AgentLogger("SimpleStructuredStream", getattr(svc.config, "log_level", "INFO"))

    sys_message_len = len(sys_prompt) if isinstance(sys_prompt, str) else len(sys_prompt.content)
    run_logger.start_operation(
        "simple_structured_stream",
        client_role=client_role,
        client_name=client_name,
        system_prompt_len=sys_message_len,
        user_msg_len=len(user_msg),
        expected_format=getattr(schema_model, "__name__", str(schema_model)),
    )

    raw_text_parts: List[str] = []

    async def on_delta(piece: str):
        if piece:
            raw_text_parts.append(piece)

    async def on_complete(_):
        run_logger.log_step("stream_complete", {"chars": sum(len(p) for p in raw_text_parts)})

    # ---- stream with model ----
    client = svc.get_client(client_name)
    cfg = svc.describe_client(client, role=client_role)
    system_message = SystemMessage(content=sys_prompt) if isinstance(sys_prompt, str) else sys_prompt
    messages = [system_message, HumanMessage(content=user_msg)]

    await svc.stream_model_text_tracked(
        client,
        messages,
        on_delta=on_delta,
        on_complete=on_complete,
        temperature=temperature,
        max_tokens=max_tokens,
        client_cfg=cfg,
        role=client_role,
    )

    raw_text = "".join(raw_text_parts)

    # ---- parse helpers ----
    def _strip_fences(s: str) -> str:
        s = s.strip()
        if s.startswith("```"):
            nl = s.find("\n")
            s = s[nl + 1:] if nl >= 0 else ""
            end = s.rfind("```")
            if end >= 0:
                s = s[:end]
        return s.strip().strip("`")

    def _extract_object_tail(s: str) -> Optional[str]:
        # Normalize: handle JSON-quoted whole-string payloads and invisible chars
        s = _sanitize_ws_and_invisibles(_maybe_unescape_wrapped_string(s or ""))
        if not s.strip():
            return None

        # Strip markdown blockquotes (handles streams that prefix '>' on each line)
        s = _md_unquote(s).strip()

        # Prefer a ```json fenced block ANYWHERE in the string
        m_fenced = re.search(r"```json\s*(.+?)\s*```", s, re.I | re.S)
        if m_fenced:
            s = m_fenced.group(1).strip()
        else:
            # Fallback: any fenced block ANYWHERE
            m_any = re.search(r"```\s*(.+?)\s*```", s, re.S)
            if m_any:
                s = m_any.group(1).strip()

        # Strip stray backticks and whitespace
        s = s.strip().strip("`")

        # Finally, take the *last* JSON object (tolerates trailing narration)
        m = re.search(r"\{[\s\S]*\}\s*$", s)
        return m.group(0) if m else None


    def _try_parse(s: str) -> Optional[Dict[str, Any]]:
        obj = _extract_object_tail(s or "")
        if not obj:
            return None
        try:
            loaded = _json_loads_loose(obj) or {}
            return schema_model.model_validate(loaded).model_dump()
        except Exception:
            return None

    data = _try_parse(raw_text)
    error = None

    if data is None and raw_text.strip():
        # one-shot format fix
        fix = await svc.format_fixer.fix_format(
            raw_output=raw_text,
            expected_format=getattr(schema_model, "__name__", str(schema_model)),
            input_data=user_msg,
            system_prompt=sys_prompt,
        )
        if fix.get("success"):
            try:
                data = schema_model.model_validate(fix["data"]).model_dump()
            except Exception as ex:
                error = f"JSON parse after format fix failed: {ex}"
                data = None

    if data is None:
        # safest default
        try:
            data = schema_model.model_validate({}).model_dump()
        except Exception:
            data = {}

    try:
        run_logger.finish_operation(
            True,
            "simple_structured_stream_complete",
            result_preview={
                "error": error,
                "chars": len(raw_text),
                "has_agent_response": bool(data),
                "provider": getattr(cfg, "provider", None),
                "model": getattr(cfg, "model_name", None),
            },
        )
    except Exception:
        pass

    return {
        "agent_response": data,
        "log": {
            "error": error,
            "raw_data": raw_text,
        },
    }

INT2_RE  = BEGIN_INT_RE
JSON2_RE = BEGIN_JSON_RE
# INT2_RE  = re.compile(PARA_INT2_OPEN, re.I)
# JSON2_RE = re.compile(PARA_JSON2_OPEN, re.I)
HOLDBACK_2 = 96 # max(len("<<< BEGIN INTERNAL THINKING >>>"), len("<<< BEGIN STRUCTURED JSON >>>")) + 8

class _TwoSectionParser:
    """
    Modes:
      pre      : before any BEGIN marker
      internal : after <<< BEGIN INTERNAL THINKING >>> (stream via callback)
      json     : after <<< BEGIN STRUCTURED JSON >>> (buffer only)
    """
    def __init__(self, *, on_internal):
        self.buf = ""
        self.mode = "pre"
        self.emit_from = 0
        self.on_internal = on_internal
        self.internal = ""
        self.json = ""

    @staticmethod
    def _skip_after_marker(buf: str, i: int) -> int:
        """
        After a BEGIN marker, skip:
          - any spaces/tabs
          - up to TWO newline groups (\n or \r\n)
          - trailing spaces/tabs after those newlines
        Exactly what was requested; allows zero newlines too.
        """
        n = len(buf)
        # spaces/tabs
        while i < n and buf[i] in (" ", "\t"):
            i += 1
        # up to two newline groups
        for _ in range(2):
            if i < n and buf[i] == "\r":
                if i + 1 < n and buf[i + 1] == "\n":
                    i += 2
                else:
                    i += 1
                while i < n and buf[i] in (" ", "\t"):
                    i += 1
            elif i < n and buf[i] == "\n":
                i += 1
                while i < n and buf[i] in (" ", "\t"):
                    i += 1
            else:
                break
        return i

    async def feed(self, piece: str):
        if not piece:
            return
        prev_len = len(self.buf)
        self.buf += piece

        def _search(pat):
            start = max(0, prev_len - HOLDBACK_2, self.emit_from - HOLDBACK_2)
            return pat.search(self.buf, start)

        while True:
            if self.mode == "pre":
                m = (_search(INT2_RE) or _search(JSON2_RE))
                if not m:
                    break
                end = self._skip_after_marker(self.buf, m.end())
                self.emit_from = end
                self.mode = "internal" if m.re is INT2_RE else "json"
                continue

            if self.mode == "internal":
                m_json = _search(JSON2_RE)
                if not m_json:
                    safe_end = max(self.emit_from, len(self.buf) - HOLDBACK_2)
                    if safe_end > self.emit_from:
                        raw = self.buf[self.emit_from:safe_end]
                        cleaned = _redact_stream_text(raw)
                        if cleaned:
                            await self.on_internal(cleaned, completed=False)
                            self.internal += cleaned
                        self.emit_from = safe_end
                    break
                # flush up to JSON marker, then switch
                raw = self.buf[self.emit_from:m_json.start()]
                cleaned = _redact_stream_text(raw)
                if cleaned:
                    await self.on_internal(cleaned, completed=False)
                    self.internal += cleaned
                self.emit_from = self._skip_after_marker(self.buf, m_json.end())
                self.mode = "json"
                continue

            if self.mode == "json":
                # buffer tail
                self.json += self.buf[self.emit_from:]
                self.emit_from = len(self.buf)
                break

            break

    async def finalize(self):
        if self.mode == "internal":
            raw = self.buf[self.emit_from:]
            cleaned = _redact_stream_text(raw)
            if cleaned:
                await self.on_internal(cleaned, completed=False)
                self.internal += cleaned
        elif self.mode == "json":
            self.json += self.buf[self.emit_from:]
        # caller sends completed=True

async def _stream_agent_two_sections_to_json(
        svc: ModelServiceBase,
        *,
        client_name: str,
        client_role: str,
        sys_prompt: str|SystemMessage,
        user_msg: str,
        schema_model,
        on_progress_delta=None,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    2-part streaming:
      1) INTERNAL THINKING  -> streamed to user (via on_progress_delta) with redaction
      2) STRUCTURED JSON    -> buffered & validated against schema_model
    """
    run_logger = AgentLogger("TwoSectionStream", getattr(svc.config, "log_level", "INFO"))
    sys_message_len = len(sys_prompt) if isinstance(sys_prompt, str) else len(sys_prompt.content)
    run_logger.start_operation(
        "two_section_stream",
        client_role=client_role,
        client_name=client_name,
        system_prompt_len=sys_message_len,
        user_msg_len=len(user_msg),
        expected_format=getattr(schema_model, "__name__", str(schema_model)),
    )

    deltas = 0
    async def _emit_progress(piece: str, completed: bool = False, **kwargs):
        nonlocal deltas
        if on_progress_delta is not None:
            await on_progress_delta(piece or "", completed, **kwargs)
            if piece:
                deltas += 1

    parser = _TwoSectionParser(on_internal=_emit_progress)

    async def on_delta(piece: str):
        await parser.feed(piece)

    async def on_complete(_):
        await parser.finalize()
        await _emit_progress("", completed=True)
        run_logger.log_step("stream_complete", {"deltas": deltas})

    # model call
    client = svc.get_client(client_name)
    cfg = svc.describe_client(client, role=client_role)
    system_message = SystemMessage(content=sys_prompt) if isinstance(sys_prompt, str) else sys_prompt
    messages = [system_message, HumanMessage(content=user_msg)]
    await svc.stream_model_text_tracked(
        client,
        messages,
        on_delta=on_delta,
        on_complete=on_complete,
        temperature=temperature,
        max_tokens=max_tokens,
        client_cfg=cfg,
        role=client_role,
    )

    # ----- parse the JSON section -----
    raw_json = parser.json  # keep original for logs

    data = None
    err = None
    tail = _defence(raw_json) or ""

    if tail:
        try:
            loaded = _json_loads_loose(tail) or {}
            data = schema_model.model_validate(loaded).model_dump()
        except Exception:
            data = None

    if data is None and raw_json.strip():
        fix = await svc.format_fixer.fix_format(
            raw_output=raw_json,
            expected_format=getattr(schema_model, "__name__", str(schema_model)),
            input_data=user_msg,
            system_prompt=sys_prompt,
        )
        if fix.get("success"):
            try:
                data = schema_model.model_validate(fix["data"]).model_dump()
            except Exception as ex:
                err = f"JSON parse after format fix failed: {ex}"

    if data is None:
        try:
            data = schema_model.model_validate({}).model_dump()
        except Exception:
            data = {}

    run_logger.finish_operation(
        True,
        "two_section_stream_complete",
        result_preview={
            "error": err,
            "internal_len": len(parser.internal),
            "json_len": len(parser.json),
            "provider": getattr(cfg, "provider", None),
            "model": getattr(cfg, "model_name", None),
        },
    )

    return {
        "agent_response": data,
        "log": {"error": err, "raw_data": raw_json},
        "internal_thinking": parser.internal,  # already streamed
    }