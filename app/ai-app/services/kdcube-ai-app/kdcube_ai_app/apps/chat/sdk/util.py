# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
from __future__ import annotations


# chat/sdk/util.py
import time, orjson, hashlib, re, json, unicodedata
from typing import Any, List, Dict, Optional, Union, Tuple
from datetime import datetime, timezone
import datetime as dt
import pathlib
from decimal import Decimal
from enum import Enum
from uuid import UUID
import dataclasses
import base64

from pydantic import BaseModel


# ---------- small general helpers ----------

def now_ms() -> int:
    return int(time.time() * 1000)

def json_dumps(data: Any) -> str:
    return orjson.dumps(
        data,
        option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY
    ).decode()

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def estimate_b64_size(data_b64: str) -> Optional[int]:
    if not isinstance(data_b64, str) or not data_b64:
        return None
    padding = data_b64.count("=")
    return max(0, (len(data_b64) * 3 // 4) - padding)

def slug(s: str) -> str:
    # fold accents → ascii, then normalize
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)   # turn any run of non-alnum into a single dash
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s

def _safe_schema_name(name: str) -> str:
    """
    Same semantics as ops/deployment SQL tool to ensure we land in the same schema.
    """
    sanitized = re.sub(r'[^A-Za-z0-9_]', '_', name)
    sanitized = re.sub(r'_+', '_', sanitized).strip('_').lower()
    if not sanitized or not sanitized[0].isalpha():
        sanitized = f"_{sanitized}" if sanitized else "_schema"
    return sanitized[:63]

def _make_project_schema(tenant: str, project: str) -> str:
    return f"kdcube_{tenant}_{_safe_schema_name(project)}"

# ---------- follow-up parsing (kept) ----------

def _strip_code_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()

def _parse_followups_tail(raw: str) -> List[str]:
    """
    Accepts:
      - JSON object with key 'followup' or 'followups'
      - or a raw JSON array of strings
      - allows content to be wrapped in ```json fences
      - ignores any text before the final JSON block
    """
    if not raw:
        return []
    candidate = _strip_code_fences(raw)
    m_obj = re.search(r"\{[\s\S]*\}\s*$", candidate)
    if m_obj:
        try:
            obj = json.loads(m_obj.group(0))
            vals = obj.get("followup") or obj.get("followups") or []
            if isinstance(vals, list):
                return [str(x).strip() for x in vals if str(x).strip()]
        except Exception:
            pass
    m_arr = re.search(r"\[[\s\S]*\]\s*$", candidate)
    if m_arr:
        try:
            arr = json.loads(m_arr.group(0))
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            pass
    return []

def _wrap_lines(text: str, indent: str = "   ", width: int = 77) -> List[str]:
    """Wrap text to fit within width, applying indent to each line."""
    if not text:
        return []

    # Clean up whitespace
    text = " ".join(text.split())

    lines = []
    available_width = width - len(indent)

    while text:
        if len(text) <= available_width:
            lines.append(f"{indent}{text}")
            break

        # Find last space within available width
        break_point = text.rfind(" ", 0, available_width)
        if break_point == -1:
            # No space found, force break at width
            break_point = available_width

        lines.append(f"{indent}{text[:break_point]}")
        text = text[break_point:].lstrip()

    return lines

# def _extract_json_block(text: str) -> Optional[str]:
#     """Strip ```json fences and return the innermost {...} block."""
#     if not text:
#         return None
#     t = text.strip()
#     if t.startswith("```"):
#         t = re.sub(r"^```[ \t]*([jJ][sS][oO][nN])?[ \t]*\n?", "", t)
#         t = re.sub(r"\n?```$", "", t).strip()
#     t = t.replace("“", '"').replace("”", '"').replace("’", "'").replace("„", '"')
#     start = t.find("{")
#     end = t.rfind("}")
#     if start != -1 and end != -1 and end > start:
#         return t[start:end + 1]
#     return None
#
# def _json_loads_loose(text: str):
#     """Best-effort JSON loader that tolerates code fences and chatter."""
#     try:
#         return json.loads(text)
#     except Exception:
#         pass
#     block = _extract_json_block(text)
#     if block:
#         try:
#             return json.loads(block)
#         except Exception:
#             block_nc = re.sub(r",(\s*[}\]])", r"\1", block)
#             try:
#                 return json.loads(block_nc)
#             except Exception:
#                 return None
#     return None

def _fix_json_quotes(text: str) -> str:
    """Fix JSON by escaping internal quotes and replacing Unicode delimiters."""
    result = []
    in_string = False
    i = 0

    while i < len(text):
        c = text[i]
        prev_char = text[i-1] if i > 0 else ''

        if c == '"' and prev_char != '\\':
            if not in_string:
                # Opening quote
                in_string = True
                result.append(c)
            else:
                # Could be closing quote or content - look ahead
                next_chars = text[i+1:i+10].lstrip()
                if next_chars and next_chars[0] in ',}]:':
                    # Followed by delimiter - it's closing
                    in_string = False
                    result.append(c)
                else:
                    # It's content - escape it
                    result.append('\\' + c)
        elif c in '\u201C\u201D\u201E':
            # Unicode quotes - replace with ASCII
            if not in_string:
                result.append('"')
                in_string = True
            else:
                # Check if closing
                next_chars = text[i+1:i+10].lstrip()
                if next_chars and next_chars[0] in ',}]:':
                    result.append('"')
                    in_string = False
                else:
                    # Keep as content
                    result.append(c)
        else:
            result.append(c)

        i += 1

    return ''.join(result)

def _extract_json_block(text: str) -> Optional[str]:
    """Strip ```json fences and return the innermost {...} block."""
    if not text:
        return None
    t = text.strip()

    # Remove code fences
    if t.startswith("```"):
        t = re.sub(r"^```[ \t]*([jJ][sS][oO][nN])?[ \t]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()

    # Find JSON block
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    block = t[start:end + 1]
    block = _fix_json_quotes(block)

    return block

def _json_loads_loose(text: str):
    """Best-effort JSON loader that tolerates code fences and chatter."""
    # Try parsing as-is first
    try:
        return json.loads(text)
    except Exception:
        pass

    # Extract JSON block
    block = _extract_json_block(text)
    if not block:
        print("DEBUG: No block extracted")
        return None

    # Try parsing the block
    try:
        return json.loads(block)
    except Exception as e:
        print(f"JSON parse error: {e}")
        print(f"Block length: {len(block)}")

        start = 50
        end = 85
        snippet = block[start:end]
        print(f"\nCharacters {start}-{end}:")
        print(repr(snippet))
        print("Hex codes:")
        print([f"{ord(c):04x} ({c!r})" for c in snippet])

        # Show characters around position 131
        start = max(0, 125)
        end = min(len(block), 140)
        snippet = block[start:end]
        print(f"\nCharacters {start}-{end}:")
        print(repr(snippet))
        print("Hex codes:")
        print([f"{ord(c):04x} ({c!r})" for c in snippet])

        # Try removing trailing commas
        block_nc = re.sub(r",(\s*[}\]])", r"\1", block)
        try:
            return json.loads(block_nc)
        except Exception as e2:
            print(f"Still failed after comma removal: {e2}")
            return None


def _json_loads_loose_with_err(text: str) -> Tuple[Optional[Any], Optional[str]]:
    """
    Best-effort JSON loader that tolerates code fences and chatter.
    Returns: (parsed_json, error_log_string)
    """
    try:
        return json.loads(text), None
    except Exception:
        pass

    # Extract JSON block
    block = _extract_json_block(text)

    if not block:
        return None, "DEBUG: No JSON block extracted from text."

    # Try parsing the block
    try:
        return json.loads(block), None
    except Exception as e:
        log_lines = []
        log_lines.append(f"JSON parse error: {e}")
        log_lines.append(f"Block length: {len(block)}")

        start = 50
        end = 85
        snippet = block[start:end]
        log_lines.append(f"\nCharacters {start}-{end}:")
        log_lines.append(repr(snippet))
        hex_codes = [f"{ord(c):04x} ({c!r})" for c in snippet]
        log_lines.append(f"Hex codes: {hex_codes}")

        start = max(0, 125)
        end = min(len(block), 140)
        snippet = block[start:end]
        log_lines.append(f"\nCharacters {start}-{end}:")
        log_lines.append(repr(snippet))
        hex_codes = [f"{ord(c):04x} ({c!r})" for c in snippet]
        log_lines.append(f"Hex codes: {hex_codes}")

        # Try removing trailing commas
        block_nc = re.sub(r",(\s*[}\]])", r"\1", block)
        try:
            return json.loads(block_nc), None
        except Exception as e2:
            log_lines.append(f"Still failed after comma removal: {e2}")
            print(log_lines)
            return None, "\n".join(log_lines)

# ---------- simple markdown generation (NO type/stage mapping) ----------

def _truncate(s: str, n: int = 500) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else (s[: n - 1] + "…")

def _ms_to_iso(ms: Optional[int]) -> Optional[str]:
    if not ms and ms != 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()

def _format_elapsed(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    if ms < 1000:
        return f"{ms} ms"
    return f"{ms/1000.0:.2f} s"

def ensure_event_markdown(evt: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure a human-friendly markdown summary is present.

    Works with BOTH shapes:
      1) Flat event dict (legacy producer):
         { title, agent, step, status, message?, data?, timing? }
         -> writes markdown to evt["markdown"]

      2) Chat envelope:
         {
           "type": "...",
           "event": { "title", "agent", "step", "status", "timing"?, "markdown"? },
           "data": ...,
           ...
         }
         -> writes markdown to evt["event"]["markdown"]
    """
    # Detect envelope vs flat
    is_envelope = isinstance(evt.get("event"), dict)
    event_block = evt["event"] if is_envelope else evt

    # If already present, keep it
    if (event_block or {}).get("markdown"):
        return evt

    # Extract fields uniformly
    title  = (event_block or {}).get("title") or (evt.get("type") or "Event").replace(".", " ").title()
    agent  = (event_block or {}).get("agent")
    step   = (event_block or {}).get("step")
    status = (event_block or {}).get("status")
    note   = (event_block or {}).get("message") or evt.get("message")

    # Timing may be in event.timings or evt.timing or inside data
    timing = (event_block or {}).get("timing") or evt.get("timing") or {}
    data   = evt.get("data")

    # If timing missing, try to infer from data scalars
    if not timing and isinstance(data, dict):
        if any(k in data for k in ("elapsed_ms", "started_ms", "ended_ms")):
            timing = {
                "started_ms": data.get("started_ms"),
                "ended_ms":   data.get("ended_ms"),
                "elapsed_ms": data.get("elapsed_ms"),
            }

    lines: List[str] = [f"**Message:** {title}"]
    if agent:  lines.append(f"**Agent:** `{agent}`")
    if step:   lines.append(f"**Step:** `{step}`")
    if status: lines.append(f"**Status:** `{status}`")

    # Render timing if any
    if isinstance(timing, dict) and any(timing.get(k) is not None for k in ("elapsed_ms", "started_ms", "ended_ms")):
        smsi = _ms_to_iso(timing.get("started_ms"))
        emsi = _ms_to_iso(timing.get("ended_ms"))
        elap = _format_elapsed(timing.get("elapsed_ms"))
        tparts = []
        if elap: tparts.append(f"Elapsed: **{elap}**")
        if smsi: tparts.append(f"Started: {smsi}")
        if emsi: tparts.append(f"Ended: {emsi}")
        if tparts:
            lines.append("**Timing:** " + " • ".join(tparts))

    if note:
        lines.append(f"**Note:** {_truncate(str(note), 1200)}")

    # Render data
    if isinstance(data, dict):
        if isinstance(data.get("topics"), list) and data["topics"]:
            lines.append("**Topics:** " + ", ".join(map(str, data["topics"][:6])) + ("" if len(data["topics"]) <= 6 else " …"))

        if isinstance(data.get("queries"), list):
            qs = data["queries"]
            lines.append(f"**Queries:** {len(qs)}")
            for q in qs[:8]:
                item = q.get("query") if isinstance(q, dict) else str(q)
                lines.append(f"* {_truncate(str(item), 200)}")
            if len(qs) > 8:
                lines.append(f"* … +{len(qs) - 8} more")

        if isinstance(data.get("items"), list):
            items = data["items"]
            lines.append(f"**Items:** {len(items)}")
            for m in items[:5]:
                if isinstance(m, dict):
                    label = m.get("title") or m.get("summary") or m.get("heading") or m.get("text") or m.get("content") or str(m)
                else:
                    label = str(m)
                lines.append(f"* {_truncate(label, 160)}")
            if len(items) > 5:
                lines.append(f"* … +{len(items) - 5} more")

        scalars = {k: v for k, v in data.items() if not isinstance(v, (dict, list))}
        if scalars:
            lines.append("**Details:**")
            for k, v in list(scalars.items())[:12]:
                lines.append(f"- {k}: `{_truncate(str(v), 200)}`")

        lines.append("```json")
        lines.append(json.dumps(_to_jsonable(data), ensure_ascii=False, indent=2))
        lines.append("```")

    elif isinstance(data, list):
        lines.append(f"**Items:** {len(data)}")
        for x in data[:10]:
            lines.append(f"* {_truncate(str(x), 160)}")
        if len(data) > 10:
            lines.append(f"* … +{len(data) - 10} more")

    elif data is not None:
        lines.append(f"**Data:** {_truncate(str(data), 1200)}")

    md = "\n".join(lines)

    # Write back in the correct place
    if is_envelope:
        evt.setdefault("event", {})
        evt["event"]["markdown"] = md
    else:
        evt["markdown"] = md

    return evt

# ---------- safe JSON for wire ----------

def _to_json_safe(x):
    import datetime as _dt
    if isinstance(x, dict):
        return {k: _to_json_safe(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_to_json_safe(v) for v in x]
    if isinstance(x, tuple):
        return [_to_json_safe(v) for v in x]
    if isinstance(x, set):
        return [_to_json_safe(v) for v in x]
    if isinstance(x, _dt.datetime):
        return (x.isoformat() if x.tzinfo else x.isoformat() + "Z")
    if isinstance(x, _dt.date):
        return x.isoformat()
    return x

def _jd(obj):
    # dumps with broad JSON-safe coercion (dataclasses, enums, pydantic, etc.)
    return json.dumps(_to_jsonable(obj), ensure_ascii=False)

# -----------------------------
# Utilities
# -----------------------------

from kdcube_ai_app.apps.chat.sdk.protocol import ChatHistoryMessage


def normalize_history(items: Optional[Union[List[Dict[str, Any]], List[ChatHistoryMessage]]]) -> List[ChatHistoryMessage]:
    if not items:
        return []
    out: List[ChatHistoryMessage] = []
    for it in items:
        if isinstance(it, ChatHistoryMessage):
            out.append(it)
            continue
        if isinstance(it, dict):
            role = (it.get("role") or "user").lower()
            content = it.get("content") or ""
            ts = it.get("timestamp")
            out.append(ChatHistoryMessage(role=role, content=content, timestamp=ts))
    return out


def history_as_dicts(items: List[ChatHistoryMessage]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for h in (items or []):
        out.append({
            "role": h.role,
            "content": h.content,
            "timestamp": h.timestamp or datetime.now().isoformat()
        })
    return out

def _json_schema_of(model: type[BaseModel]) -> str:
    """Pretty JSON Schema for a Pydantic model (v2 with v1 fallback)."""
    try:
        schema = model.model_json_schema()   # Pydantic v2
    except Exception:
        schema = model.schema()              # Pydantic v1 fallback
    return json.dumps(schema, indent=2, ensure_ascii=False)

def _today_str() -> str:
    # UTC; if you have user’s tz, inject it in prompts separately
    return datetime.now(timezone.utc).date().isoformat()

def _now_str() -> str:
    # UTC; full timestamp with microseconds
    return datetime.now(timezone.utc).isoformat()

def _now_ms() -> int:
    return int(time.time() * 1000)

def _now_up_to_minutes() -> str:
    # UTC; full timestamp with microseconds
    return datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()

_ISO_MINUTE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})')

def _to_iso_minute(ts: str) -> str:
    if not ts:
        return ts
    s = str(ts).strip()
    m = _ISO_MINUTE_RE.match(s)
    if m:
        # normalize trailing Z consistently
        return m.group(1) + "Z"
    try:
        s2 = s.rstrip('Z').replace(' ', 'T')
        d = _dt.datetime.fromisoformat(s2)
        d = d.replace(second=0, microsecond=0)
        return d.isoformat(timespec='minutes') + "Z"
    except Exception:
        return s

def _utc_now_iso_minute() -> str:
    dt = _dt.datetime.utcnow().replace(second=0, microsecond=0)
    # 'timespec="minutes"' yields 'YYYY-MM-DDTHH:MM'
    return dt.isoformat(timespec='minutes') + "Z"

def _tstart() -> tuple[float, int]:
    """perf counter and wall ms."""
    return time.perf_counter(), _now_ms()

def _tend(t0: float, started_ms: int) -> Dict[str, int]:
    """elapsed + stamps for event payloads."""
    ended_ms = _now_ms()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {"started_ms": started_ms, "ended_ms": ended_ms, "elapsed_ms": elapsed_ms}

def _shorten(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else (s[:n-1] + "…")

def _defence(s: str, none_on_failure: bool = True, format: str = "json"):
    """
    Extract content from the outermost code fence, handling nested fences.
    Works for ```json, ```python, or plain ``` blocks.

    Args:
        format: Content type - if 'mermaid' or 'html', skips JSON brace fallback
    """
    default_ret = None if none_on_failure else s
    if not s:
        return default_ret
    s = s.lstrip()

    fence_start = s.find('```')
    if fence_start == -1:
        # No fences - only extract JSON for actual JSON/code content
        # Don't mangle Mermaid/HTML which may have {} in syntax
        if format not in ("mermaid", "html", "xml", "yaml", "markdown"):
            first_brace = s.find('{')
            last_brace = s.rfind('}')
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                return s[first_brace:last_brace + 1]
        return default_ret

    # Skip past opening fence and language tag
    content_start = s.find('\n', fence_start)
    if content_start == -1:
        return default_ret
    content_start += 1  # Skip the newline

    # Find last closing fence
    last_fence = s.rfind('```')
    if last_fence == -1 or last_fence <= fence_start:
        return default_ret

    # Extract content between first opening and last closing
    content = s[content_start:last_fence].strip()
    return content if content else default_ret


def _turn_id_from_tags_safe(tags: List[str]) -> Optional[str]:
    for t in tags or []:
        if isinstance(t, str) and t.startswith("turn:"):
            return t.split(":", 1)[1]
    return None

def _to_jsonable(obj: Any, *, _seen: set[int] | None = None) -> Any:
    if _seen is None:
        _seen = set()

    # primitives
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    oid = id(obj)
    if oid in _seen:
        # cycle / repeated reference: keep stable, JSONable marker
        return {"$ref": oid}
    _seen.add(oid)

    # common stdlib non-JSON types
    if isinstance(obj, (dt.datetime, dt.date, dt.time)):
        return obj.isoformat()
    if isinstance(obj, pathlib.Path):
        return str(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        # choose: str() preserves precision; float() loses it
        return str(obj)
    if isinstance(obj, Enum):
        # SlotType is often an Enum -> this matters
        return _to_jsonable(obj.value, _seen=_seen)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return {"$bytes_b64": base64.b64encode(bytes(obj)).decode("ascii")}

    # pydantic v2
    try:
        from pydantic import BaseModel  # type: ignore
        if isinstance(obj, BaseModel):
            # mode="json" coerces enums/datetime/path/etc to JSON-friendly values
            return obj.model_dump(mode="json", exclude_none=False)
    except Exception:
        pass

    # dataclass (avoid asdict() deep-copy)
    if dataclasses.is_dataclass(obj):
        out = {}
        for f in dataclasses.fields(obj):
            out[f.name] = _to_jsonable(getattr(obj, f.name), _seen=_seen)
        return out

    # containers
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v, _seen=_seen) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_to_jsonable(v, _seen=_seen) for v in obj]

    # last resort: keep structure (don’t crash websocket)
    return {"$type": obj.__class__.__name__, "$repr": repr(obj)}

import datetime as _dt

def isoz(ts: str | None) -> str:
    if not ts: return ""
    try:
        s = ts.strip()
        if s.endswith("Z"): return s
        if "+" not in s and "Z" not in s:
            # assume naive UTC
            return _dt.datetime.fromisoformat(s).replace(tzinfo=_dt.timezone.utc).isoformat().replace("+00:00","Z")
        return _dt.datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(_dt.timezone.utc).isoformat().replace("+00:00","Z")
    except Exception:
        return ts

def ts_key(ts) -> float:
    if hasattr(ts, "timestamp"):
        return float(ts.timestamp())
    if isinstance(ts, (int, float)):
        return ts / 1000.0 if ts > 1e12 else float(ts)
    if isinstance(ts, str):
        s = ts.strip()
        try:
            if s.endswith("Z"): s = s[:-1] + "+00:00"
            return _dt.datetime.fromisoformat(s).timestamp()
        except Exception:
            try:
                v = float(s)
                return v / 1000.0 if v > 1e12 else v
            except Exception:
                return float("-inf")
    return float("-inf")

import tiktoken

# Get the tokenizer for text-embedding-3-small (uses cl100k_base encoding)
encoding = tiktoken.get_encoding("cl100k_base")

# Maximum tokens for text-embedding-3-small
MAX_TOKENS = 8191

def truncate_text_by_tokens(text, max_tokens=MAX_TOKENS):
    """Truncate text to fit within token limit"""
    tokens = encoding.encode(text)
    if len(tokens) > max_tokens:
        tokens = tokens[:max_tokens]
        text = encoding.decode(tokens)
    return text


def token_count(text: str) -> int:
    """Return token count using cl100k_base encoding."""
    if not text:
        return 0
    try:
        return len(encoding.encode(text))
    except Exception:
        return len((text or "").split())

def strip_lone_surrogates(s: str) -> str:
    # Replace any code points in the surrogate range with U+FFFD
    return ''.join('\uFFFD' if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in s)

def _iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def _enum_to_str(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, Enum):
        return str(v.value)
    return str(v)

def _is_json_primitive(v):
    return v is None or isinstance(v, (bool, int, float, str))

def _sanitize_no_unserializable_data(v):
    if isinstance(v, Enum):
        return v.value
    if _is_json_primitive(v):
        return v
    if isinstance(v, list):
        return [_sanitize_no_unserializable_data(x) for x in v if _sanitize_no_unserializable_data(x) is not None]
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            if not _is_json_primitive(k):
                continue
            sv = _sanitize_no_unserializable_data(val)
            if sv is not None:
                out[str(k)] = sv
        return out
    # Reject everything else (classes, clients, pools, datetimes, etc.)
    return None

def safe_frac(num: float, den: float) -> float:
    if den <= 0:
        return 0.0
    return float(num) / float(den)
