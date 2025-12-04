# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/streaming/streaming.py

from __future__ import annotations

import logging
import re, json, time
from typing import Dict, Any, Optional, Tuple, List

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger
from kdcube_ai_app.apps.chat.sdk.util import _json_loads_loose

logger = logging.getLogger(__name__)

# Accept 2..6 chevrons like <<, <<<, <<<<, and 2..6 closers >>, >>>, >>>>>
BEGIN_INT_RE  = re.compile(r"(?:<){2,6}\s*BEGIN\s+INTERNAL\s+THINKING\s*(?:>){2,6}", re.I)
BEGIN_USER_RE = re.compile(r"(?:<){2,6}\s*BEGIN\s+USER[-–—]?\s*FACING\s+THINKING\s*(?:>){2,6}", re.I)
BEGIN_JSON_RE = re.compile(r"(?:<){2,6}\s*BEGIN\s+STRUCTURED\s+JSON\s*(?:>){2,6}", re.I)
# tolerant marker for the 2-section "thinking channel"
BEGIN_THINKING_RE = re.compile(r"(?:<){2,6}\s*BEGIN\s+THINKING\s*(?:>){2,6}", re.I)

END_INT_RE    = re.compile(r"(?:<){2,6}\s*END\s+INTERNAL\s+THINKING\s*(?:>){2,6}", re.I)
END_USER_RE   = re.compile(r"(?:<){2,6}\s*END\s+USER[-–—]?\s*FACING\s+THINKING\s*(?:>){2,6}", re.I)
END_JSON_RE   = re.compile(r"(?:<){2,6}\s*END\s+STRUCTURED\s+JSON\s*(?:>){2,6}", re.I)
END_THINKING_RE   = re.compile(r"(?:<){2,6}\s*END\s+THINKING\s*(?:>){2,6}", re.I)

# =============================================================================
# Paranoid markers (ONLY these are supported)
# =============================================================================

PARA_INT_OPEN  = r"<<<\s*BEGIN\s+INTERNAL\s+THINKING\s*>>>"
PARA_USER_OPEN = r"<<<\s*BEGIN\s+USER[-\u2013\u2014]?\s*FACING\s+THINKING\s*>>>"
PARA_JSON_OPEN = r"<<<\s*BEGIN\s+STRUCTURED\s+JSON\s*>>>"
PARA_THINKING_OPEN = r"<<<\s*BEGIN\s+THINKING\s*>>>"

PARA_INT_CLOSE  = r"<<<\s*END\s+INTERNAL\s+THINKING\s*>>>"
PARA_USER_CLOSE = r"<<<\s*END\s+USER[-\u2013\u2014]?\s*FACING\s+THINKING\s*>>>"
PARA_JSON_CLOSE = r"<<<\s*END\s+STRUCTURED\s+JSON\s*>>>"
PARA_THINKING_CLOSE = r"<<<\s*END\s+THINKING\s*>>>"

INT_RE  = re.compile(PARA_INT_OPEN, re.I)
USER_RE = re.compile(PARA_USER_OPEN, re.I)
JSON_RE = re.compile(PARA_JSON_OPEN, re.I)
THINKING_RE = re.compile(PARA_THINKING_OPEN, re.I)

# We never *require* closers, but if a model emits them, strip from user-facing output.
# USER_CLOSER_STRIPPERS = [
#     re.compile(PARA_USER_CLOSE, re.I),
#     re.compile(PARA_INT_CLOSE, re.I),
#     re.compile(PARA_JSON_CLOSE, re.I),
#     # also safeguard: if any open markers appear inside the user section, strip them
#     USER_RE, INT_RE, JSON_RE,
# ]

USER_CLOSER_STRIPPERS = [
    END_USER_RE, END_INT_RE, END_JSON_RE, END_THINKING_RE,
    USER_RE, INT_RE, JSON_RE, THINKING_RE,                 # strict <<< ... >>>
    BEGIN_USER_RE, BEGIN_INT_RE, BEGIN_JSON_RE, BEGIN_THINKING_RE,  # tolerant << ... <<<<
]

# Rolling safety window so we never cut a marker across chunk boundaries
MAX_MARKER_LEN = max(
    len("<<< BEGIN INTERNAL THINKING >>>"),
    len("<<< BEGIN USER-FACING THINKING >>>"),
    len("<<< BEGIN STRUCTURED JSON >>>"),
    len("<<< BEGIN THINKING >>>"),
    len("<<< END INTERNAL THINKING >>>"),
    len("<<< END USER-FACING THINKING >>>"),
    len("<<< END STRUCTURED JSON >>>"),
    len("<<< END THINKING >>>"),
)

# Hold back generously so that no marker (even with some extra whitespace)
# can be split across the emitted/buffered boundary.
HOLDBACK = max(MAX_MARKER_LEN + 32, 256)

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
        "You MUST produce EXACTLY these two markers in order:\n\n"
        
        "FIRST - Write this marker, then your thinking:\n"
        "<<< BEGIN INTERNAL THINKING >>>\n"
        "[Your brief working notes here in plain text or Markdown]\n\n"
        
        "SECOND - Write this marker, then the JSON:\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "```json\n"
        f"{json_shape_hint}\n"
        "```\n\n"
        
        "CRITICAL RULES:\n"
        "• Use BOTH markers exactly as shown above\n"
        "• Do NOT write <<< END INTERNAL THINKING >>> (no closing tags!)\n"
        "• Do NOT write <<< END STRUCTURED JSON >>> (no closing tags!)\n"
        "• Do NOT skip the <<< BEGIN STRUCTURED JSON >>> marker\n"
        "• The JSON MUST come after <<< BEGIN STRUCTURED JSON >>> marker\n\n"
        
        "CORRECT example:\n"
        "<<< BEGIN INTERNAL THINKING >>>\n"
        "User wants X, I will do Y\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "```json\n"
        '{"field": "value"}\n'
        "```\n\n"
        
        "WRONG examples (DO NOT DO THIS):\n"
        "❌ <<< BEGIN INTERNAL THINKING >>> ... <<< END INTERNAL THINKING >>> ```json ...  (has END tag, missing BEGIN JSON marker)\n"
        "❌ <<< BEGIN INTERNAL THINKING >>> ... ```json ... (missing BEGIN JSON marker)\n"
        "❌ Just ```json ... (missing both markers)\n"
    )
def _get_2section_protocol(json_shape_hint: str) -> str:
    """
    Strict 2-part protocol:
      1) INTERNAL THINKING (streamed to user, with optional redaction)
      2) STRUCTURED JSON (buffered, validated)
    """
    return (
        "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "CRITICAL OUTPUT PROTOCOL — FOLLOW EXACTLY\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "YOU MUST PRODUCE EXACTLY TWO SECTIONS:\n\n"

        "SECTION 1 — INTERNAL THINKING:\n"
        "<<< BEGIN INTERNAL THINKING >>>\n"
        "[Your brief analysis/reasoning here — plain text or Markdown]\n\n"

        "SECTION 2 — STRUCTURED JSON (CRITICAL RULES BELOW):\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "```json\n"
        f"{json_shape_hint}\n"
        "```\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "CRITICAL RULES FOR SECTION 2:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "1. Section 2 MUST contain ONLY the JSON object inside ```json fences\n"
        "2. DO NOT write any closing markers like <<< END STRUCTURED JSON >>>\n"
        "3. DO NOT write >>> or any other closing symbols\n"
        "4. DO NOT solve the user's problem in Section 2\n"
        "5. DO NOT include explanations, examples, or additional content after the JSON\n"
        "6. DO NOT add markdown, text, or anything else after the closing ```\n"
        "7. DO NOT include markdown code fences (like ```mermaid or ```) inside JSON string values - use plain text descriptions instead\n"  # NEW
        "8. Section 2 is METADATA ONLY — the actual solution comes later from other agents\n\n"

        "❌ WRONG (adds content after JSON):\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "```json\n"
        "{...}\n"
        "```\n"
        ">>>\n"
        "Here's the solution: ...\n\n"

        "✅ CORRECT (JSON only, nothing after):\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "```json\n"
        "{...}\n"
        "```\n\n"

        "REMEMBER: Your job is to fill the structured contract as requested, not solve user problem.\n"
        "Section 2 = PURE JSON, NOTHING ELSE.\n"
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

INT2_RE = BEGIN_INT_RE
THINKING2_RE = BEGIN_THINKING_RE
JSON2_RE = BEGIN_JSON_RE
# INT2_RE  = re.compile(PARA_INT2_OPEN, re.I)
# JSON2_RE = re.compile(PARA_JSON2_OPEN, re.I)
HOLDBACK_2 = 256 # max(len("<<< BEGIN INTERNAL THINKING >>>"), len("<<< BEGIN STRUCTURED JSON >>>")) + 8

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

    @staticmethod
    def _extract_json_block_only(raw: str) -> str:
        """
        SAFEGUARD: Extract ONLY the ```json...``` block from the JSON section.
        Handles cases where ``` appears inside JSON string values by finding
        the actual JSON object boundaries first.
        """
        if not raw or not raw.strip():
            return ""

        # Find the ```json fence (case-insensitive)
        json_fence_match = re.search(r'```\s*json\s*\n', raw, re.IGNORECASE)
        if not json_fence_match:
            # No fence at all - try to extract JSON object directly
            obj_start = raw.find('{')
            if obj_start == -1:
                return raw  # No JSON at all

            # Use the same brace-matching logic to extract the object
            brace_count = 0
            in_string = False
            escape_next = False
            obj_end = -1

            for i in range(obj_start, len(raw)):
                char = raw[i]
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\':
                    escape_next = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            obj_end = i
                            break

            if obj_end != -1:
                return raw[obj_start:obj_end + 1].strip()

            return raw  # Couldn't extract, return as-is

        # Content after opening fence
        remaining = raw[json_fence_match.end():]

        # Find the JSON object by parsing brace boundaries (respecting strings)
        obj_start = remaining.find('{')
        if obj_start == -1:
            # No JSON object found - just remove any trailing fence
            close_fence = re.search(r'\n?\s*```', remaining)
            return remaining[:close_fence.start()].strip() if close_fence else remaining.strip()

        # Find matching closing brace using a simple state machine
        brace_count = 0
        in_string = False
        escape_next = False
        obj_end = -1

        for i in range(obj_start, len(remaining)):
            char = remaining[i]

            if escape_next:
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if char == '"':
                in_string = not in_string
                continue

            if not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        obj_end = i
                        break

        if obj_end == -1:
            # Couldn't find matching brace - fall back to finding closing fence
            close_fence = re.search(r'\n?\s*```', remaining)
            return remaining[:close_fence.start()].strip() if close_fence else remaining.strip()

        # Return only the JSON object content (from { to })
        return remaining[obj_start:obj_end + 1].strip()
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
                # Look for ANY begin marker that can start a section:
                #  - INT2_RE      → legacy "<<< BEGIN INTERNAL THINKING >>>"
                #  - THINKING2_RE → new "<<< BEGIN THINKING >>>" (user-facing thinking channel)
                #  - JSON2_RE     → models that skip thinking and start directly with JSON
                m_int_internal = _search(INT2_RE)
                m_int_thinking = _search(THINKING2_RE)
                m_json         = _search(JSON2_RE)

                candidates = [
                    (m_int_internal, "internal"),
                    (m_int_thinking, "internal"),
                    (m_json,         "json"),
                ]
                present = [(m, kind) for (m, kind) in candidates if m]

                if not present:
                    # no markers yet; wait for more chunks
                    break

                # pick the earliest match among all present markers
                m, kind = min(present, key=lambda t: t[0].start())

                end = self._skip_after_marker(self.buf, m.end())
                self.emit_from = end
                self.mode = kind  # "internal" (both markers) or "json"
                continue

            if self.mode == "internal":
                m_json = _search(JSON2_RE)
                if not m_json:
                    safe_end = max(self.emit_from, len(self.buf) - HOLDBACK_2)
                    if safe_end > self.emit_from:
                        raw = self.buf[self.emit_from:safe_end]
                        cleaned = _redact_stream_text(raw)
                        cleaned = _strip_service_markers_from_user(cleaned)  # scrub <<< ... >>> markers
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
            cleaned = _strip_service_markers_from_user(cleaned)
            if cleaned:
                await self.on_internal(cleaned, completed=False)
                self.internal += cleaned

            # FALLBACK: If no JSON section found, try to extract from internal
            if not self.json.strip():
                # Look for ```json fence in the entire buffer
                json_content = self._extract_json_block_only(self.buf)
                if json_content:
                    self.json = json_content

        elif self.mode == "json":
            self.json += self.buf[self.emit_from:]

            # SAFEGUARD: Extract ONLY the JSON block, ignore any extra content
            # This protects against models that add content after the ```
            self.json = self._extract_json_block_only(self.json)

async def _stream_agent_two_sections_to_json(
        svc: ModelServiceBase,
        *,
        client_name: str,
        client_role: str,
        sys_prompt: str|SystemMessage,
        user_msg: str|HumanMessage,
        schema_model=None,  # ← Make optional
        on_progress_delta=None,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    2-part streaming:
      1) INTERNAL THINKING  -> streamed to user (via on_progress_delta) with redaction
      2) STRUCTURED JSON    -> buffered & validated against schema_model (if provided)

    If schema_model is None, the JSON section is returned as raw text without validation.
    """
    run_logger = AgentLogger("TwoSectionStream", getattr(svc.config, "log_level", "INFO"))
    sys_message_len = len(sys_prompt) if isinstance(sys_prompt, str) else len(sys_prompt.content)
    run_logger.start_operation(
        "two_section_stream",
        client_role=client_role,
        client_name=client_name,
        system_prompt_len=sys_message_len,
        user_msg_len=len(user_msg) if isinstance(user_msg, str) else "block",
        expected_format=getattr(schema_model, "__name__", "raw_json") if schema_model else "raw_json",
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
    user_message = HumanMessage(content=user_msg) if isinstance(user_msg, str) else user_msg
    messages = [system_message, user_message]
    stream_meta = await svc.stream_model_text_tracked(
        client,
        messages,
        on_delta=on_delta,
        on_complete=on_complete,
        temperature=temperature,
        max_tokens=max_tokens,
        client_cfg=cfg,
        role=client_role,
    )
    svc_error: dict | None = None
    if isinstance(stream_meta, dict):
        if stream_meta.get("service_error"):
            # New structured shape
            svc_error = stream_meta["service_error"]

    # ----- parse the JSON section -----
    raw_json = parser.json  # keep original for logs

    # ========== Handle schema_model=None case ==========
    if schema_model is None:
        # No validation - just return raw JSON text
        ok_flag = svc_error is None
        run_logger.finish_operation(
            True,
            "two_section_stream_complete_no_schema",
            result_preview={
                "internal_len": len(parser.internal),
                "json_len": len(parser.json),
                "provider": getattr(cfg, "provider", None),
                "model": getattr(cfg, "model_name", None),
            },
        )

        return {
            "agent_response": raw_json,  # Return raw JSON text
            "log": {
                "error": None,
                "raw_data": raw_json,
                "service_error": svc_error,
                "ok": ok_flag
            },
            "internal_thinking": parser.internal,  # already streamed
        }
    # ========== END NEW ==========

    # Original validation logic when schema_model is provided
    data = None
    err = None
    raw_json_clean = _sanitize_ws_and_invisibles(raw_json)
    # tail = _defence(raw_json_clean) or ""
    tail = raw_json_clean

    if tail:
        try:
            loaded = _json_loads_loose(tail) or {}
            data = schema_model.model_validate(loaded).model_dump()
        except Exception as ex:
            logger.error(f"[_stream_agent_two_sections_to_json] JSON parse failed: {ex}\n.raw_json={raw_json}\nraw_json_clean={raw_json_clean}\ntail={tail}")
            data = None

    if data is None and raw_json.strip():

        fix_input = tail if tail.strip() else raw_json_clean
        fix = await svc.format_fixer.fix_format(
            raw_output=fix_input,
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
    ok_flag = (svc_error is None) and (err is None)
    return {
        "agent_response": data,
        "log": {
            "error": err,
            "raw_data": raw_json,
            "service_error": svc_error,
            "ok": ok_flag
        },
        "internal_thinking": parser.internal,  # already streamed
    }

async def stream_agent_to_json(
        svc: ModelServiceBase,
        *,
        client_name: str,
        client_role: str,
        sys_prompt: str|SystemMessage,
        messages: List[HumanMessage|AIMessage],
        schema_model=None,
        on_progress_delta=None,
        temperature: float = 0.2,
        max_tokens: int = 1200,
) -> Dict[str, Any]:
    """
    Generic streaming function that accepts a conversation history.

    If schema_model is provided:
      - Expects 2-part output: INTERNAL THINKING + STRUCTURED JSON
      - Validates JSON against schema_model

    If schema_model is None:
      - Streams all output as-is via on_progress_delta
      - Returns raw text in agent_response

    Args:
        svc: ModelServiceBase instance
        client_name: Name of the LLM client to use
        client_role: Role identifier for the client
        sys_prompt: System message (string or SystemMessage)
        messages: List of conversation messages (HumanMessage|AIMessage)
        schema_model: Optional Pydantic model for JSON validation
        on_progress_delta: Optional callback for streaming chunks
        temperature: Model temperature (default 0.2)
        max_tokens: Maximum tokens to generate (default 1200)

    Returns:
        Dict with keys:
        - agent_response: Parsed JSON dict (if schema_model) or raw text (if no schema_model)
        - log: Dict with error, raw_data, and timing info
        - internal_thinking: The streamed thinking section (if schema_model)
    """
    t_start = time.perf_counter()

    run_logger = AgentLogger("StreamAgentToJson", getattr(svc.config, "log_level", "INFO"))
    sys_message_len = len(sys_prompt) if isinstance(sys_prompt, str) else len(sys_prompt.content)

    run_logger.start_operation(
        "stream_agent_to_json",
        client_role=client_role,
        client_name=client_name,
        system_prompt_len=sys_message_len,
        messages_count=len(messages),
        has_schema=schema_model is not None,
        expected_format=getattr(schema_model, "__name__", "raw_text") if schema_model else "raw_text",
    )

    # Timing dict to track all durations
    timings = {}

    # Measure message preparation
    t_prep_start = time.perf_counter()
    system_message = SystemMessage(content=sys_prompt) if isinstance(sys_prompt, str) else sys_prompt
    full_messages = [system_message] + messages
    timings["message_prep_ms"] = (time.perf_counter() - t_prep_start) * 1000

    # Measure client retrieval (includes first-time creation overhead)
    t_client_start = time.perf_counter()
    client = svc.get_client(client_name)
    timings["client_get_ms"] = (time.perf_counter() - t_client_start) * 1000

    # Measure config retrieval
    t_cfg_start = time.perf_counter()
    cfg = svc.describe_client(client, role=client_role)
    timings["config_describe_ms"] = (time.perf_counter() - t_cfg_start) * 1000

    # Case 1: No schema model - simple streaming
    if schema_model is None:
        raw_parts: List[str] = []

        t_stream_start = time.perf_counter()
        t_first_token = None

        async def on_delta(piece: str):
            nonlocal t_first_token
            if piece:
                if t_first_token is None:
                    t_first_token = time.perf_counter()
                    timings["time_to_first_token_ms"] = (t_first_token - t_stream_start) * 1000
                raw_parts.append(piece)
                if on_progress_delta is not None:
                    await on_progress_delta(piece, completed=False)

        async def on_complete(_):
            if on_progress_delta is not None:
                await on_progress_delta("", completed=True)
            timings["streaming_ms"] = (time.perf_counter() - t_stream_start) * 1000
            run_logger.log_step("stream_complete", {
                "chars": sum(len(p) for p in raw_parts),
                "streaming_ms": timings["streaming_ms"],
                "ttft_ms": timings.get("time_to_first_token_ms")
            })

        stream_meta = await svc.stream_model_text_tracked(
            client,
            full_messages,
            on_delta=on_delta,
            on_complete=on_complete,
            temperature=temperature,
            max_tokens=max_tokens,
            client_cfg=cfg,
            role=client_role,
        )

        svc_error: dict | None = None
        if isinstance(stream_meta, dict) and stream_meta.get("service_error"):
            svc_error = stream_meta["service_error"]

        ok_flag = svc_error is None

        raw_text = "".join(raw_parts)
        timings["total_ms"] = (time.perf_counter() - t_start) * 1000
        timings["overhead_ms"] = timings["total_ms"] - timings.get("streaming_ms", 0)

        run_logger.finish_operation(
            True,
            "stream_agent_to_json_complete",
            result_preview={
                "chars": len(raw_text),
                "has_schema": False,
                "provider": getattr(cfg, "provider", None),
                "model": getattr(cfg, "model_name", None),
                "timings": timings,
            },
        )

        return {
            "agent_response": raw_text,
            "log": {
                "error": None,
                "raw_data": raw_text,
                "timings": timings,
                "service_error": svc_error,
                "ok": ok_flag
            },
        }

    # Case 2: With schema model - 2-section parsing
    deltas = 0
    t_stream_start = time.perf_counter()
    t_first_token = None

    async def _emit_progress(piece: str, completed: bool = False, **kwargs):
        nonlocal deltas, t_first_token
        if piece and t_first_token is None:
            t_first_token = time.perf_counter()
            timings["time_to_first_token_ms"] = (t_first_token - t_stream_start) * 1000
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
        timings["streaming_ms"] = (time.perf_counter() - t_stream_start) * 1000
        run_logger.log_step("stream_complete", {
            "deltas": deltas,
            "streaming_ms": timings["streaming_ms"],
            "ttft_ms": timings.get("time_to_first_token_ms")
        })

    stream_meta = await svc.stream_model_text_tracked(
        client,
        full_messages,
        on_delta=on_delta,
        on_complete=on_complete,
        temperature=temperature,
        max_tokens=max_tokens,
        client_cfg=cfg,
        role=client_role,
    )
    svc_error: dict | None = None
    if isinstance(stream_meta, dict):
        if stream_meta.get("service_error"):
            svc_error = stream_meta["service_error"]

    # Parse the JSON section
    t_parse_start = time.perf_counter()
    raw_json = parser.json
    data = None
    err = None
    raw_json_clean = _sanitize_ws_and_invisibles(raw_json)
    tail = raw_json_clean
    # tail = _defence(raw_json_clean) or ""

    if tail:
        try:
            loaded = _json_loads_loose(tail) or {}
            data = schema_model.model_validate(loaded).model_dump()
        except Exception as ex:
            logger.error(f"[_stream_agent_to_json] JSON parse failed: {ex}\nraw_json={raw_json}\nraw_json_clean={raw_json_clean}\ntail={tail}")
            data = None

    timings["initial_parse_ms"] = (time.perf_counter() - t_parse_start) * 1000

    # Format fixing if needed
    if data is None and raw_json.strip():
        t_fix_start = time.perf_counter()

        fix_input = tail if tail.strip() else raw_json_clean
        # Build a summary of the conversation for format fixer context
        last_user_msg = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")

        fix = await svc.format_fixer.fix_format(
            raw_output=fix_input,
            expected_format=getattr(schema_model, "__name__", str(schema_model)),
            input_data=last_user_msg,
            system_prompt=sys_prompt,
        )

        timings["format_fix_ms"] = (time.perf_counter() - t_fix_start) * 1000

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

    timings["total_ms"] = (time.perf_counter() - t_start) * 1000
    timings["overhead_ms"] = (timings["total_ms"]
                              - timings.get("streaming_ms", 0)
                              - timings.get("format_fix_ms", 0))

    run_logger.finish_operation(
        True,
        "stream_agent_to_json_complete",
        result_preview={
            "error": err,
            "internal_len": len(parser.internal),
            "json_len": len(parser.json),
            "has_schema": True,
            "provider": getattr(cfg, "provider", None),
            "model": getattr(cfg, "model_name", None),
            "timings": timings,
        },
    )
    ok_flag = (svc_error is None) and (err is None)
    return {
        "agent_response": data,
        "log": {
            "error": err,
            "raw_data": raw_json,
            "timings": timings,
            "service_error": svc_error,
            "ok": ok_flag,
        },
        "internal_thinking": parser.internal,
    }