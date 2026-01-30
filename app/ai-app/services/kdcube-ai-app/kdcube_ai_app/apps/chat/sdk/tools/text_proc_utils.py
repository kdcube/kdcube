# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/text_proc_utils.py

import re, yaml, jsonschema, json
from typing import Tuple, Any, Set, Optional

from kdcube_ai_app.apps.chat.sdk.tools.citations import MD_CITE_RE
import kdcube_ai_app.utils.text as text_utils

_ZWSP = "\u200b"
_BOM  = "\ufeff"
CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", re.M)

def _rm_invis(s: str) -> str:
    # strip zero-width spaces and BOM which break token regexes
    # also collapse stray NBSPs around brackets/colon (safe)
    if not s:
        return s
    s = s.replace(_ZWSP, "").replace(_BOM, "")
    s = "".join(ch for ch in s if not (0xD800 <= ord(ch) <= 0xDFFF))
    return s

def _strip_bom_zwsp(s: str) -> str:
    # remove UTF-8 BOM and zero-width spaces that models sometimes prepend
    if not s:
        return s
    s = s.lstrip("\ufeff").replace(_ZWSP, "")
    return s.strip()

def _unwrap_fenced_block(text: str, lang: str | None = None) -> str:
    """
    If text contains a ```<lang> ... ``` or ``` ... ``` block, return the inner.
    Otherwise return text unchanged (stripped). Works with ``` and ~~~ fences.
    """
    if not text:
        return text
    # prefer language-specific fence first
    if lang:
        m = re.search(rf"```{lang}\s*([\s\S]*?)\s*```", text, flags=re.I)
        if not m:
            m = re.search(rf"~~~{lang}\s*([\s\S]*?)\s*~~~", text, flags=re.I)
        if m:
            return m.group(1).strip()

    # any fenced block
    m = re.search(r"```(?:\w+)?\s*([\s\S]*?)\s*```", text, flags=re.I)
    if not m:
        m = re.search(r"~~~(?:\w+)?\s*([\s\S]*?)\s*~~~", text, flags=re.I)
    if m:
        return m.group(1).strip()

    return text.strip()

def _unwrap_fenced_blocks_concat(text: str, lang: str | None = None) -> str:
    """
    Collect ALL fenced blocks (```<lang> ... ``` or ~~~<lang> ... ~~~) and concatenate them.
    If none found, return the original text stripped.
    """
    if not text:
        return ""
    parts: list[str] = []
    if lang:
        # lang-specific first
        for pat in (rf"```{lang}\s*([\s\S]*?)\s*```", rf"~~~{lang}\s*([\s\S]*?)\s*~~~"):
            for m in re.finditer(pat, text, flags=re.I):
                parts.append(m.group(1).strip())
    # if nothing found for explicit lang, try any fenced blocks
    if not parts:
        for pat in (r"```(?:\w+)?\s*([\s\S]*?)\s*```", r"~~~(?:\w+)?\s*([\s\S]*?)\s*~~~"):
            for m in re.finditer(pat, text, flags=re.I):
                parts.append(m.group(1).strip())
    return "\n".join(parts).strip() if parts else text.strip()

def _extract_json_object(text: str) -> str | None:
    """Last-resort: pull the largest {...} region if fencing/extra prose remains."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end+1].strip()
    return None

def _remove_end_marker_everywhere(text: str, marker: str) -> str:
    """
    Remove the exact end marker. Also remove standalone lines that are only angle brackets.
    Does NOT strip whitespace to preserve proper spacing in streamed chunks.
    """
    if not text or not marker:
        return text

    s = text.replace(marker, "")

    # Remove standalone lines containing only angle brackets
    # (These sometimes appear when marker fragments stream on separate lines)
    s = re.sub(r"(?m)^(?:\s*[<>]{2,}\s*)+$", "", s)

    # DON'T strip! Preserving leading/trailing whitespace is critical for streaming
    return s
def _split_safe_marker_prefix(chunk: str, marker: str) -> tuple[str, int]:
    """
    If the end of `chunk` is a dangling prefix of `marker`, withhold it.
    Returns (safe_prefix, dangling_len).
    Example: marker="<<<GENERATION FINISHED>>>"
    chunk ending with "<<<GENER" -> withhold that suffix.
    """
    if not chunk or not marker:
        return chunk, 0
    # longest dangling first
    max_k = min(len(chunk), len(marker) - 1)
    for k in range(max_k, 0, -1):
        if chunk.endswith(marker[:k]):
            return chunk[:-k], k
    return chunk, 0

def _strip_code_fences(text: str, allow: bool) -> str:
    if allow:
        return text
    # Remove outermost triple-fence blocks when requested not to use them
    return CODE_FENCE_RE.sub("", text).strip()

def _remove_marker(text: str, marker: str) -> str:
    return text.replace(marker, "").strip()

def _json_pointer_get(root, ptr: str):
    if not ptr or ptr == "/":
        return root
    cur = root
    for part in ptr.strip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur

def _dfs_string_contains_inline_cite(node) -> bool:
    if isinstance(node, str): # _MD_CITE_RE
        return bool(MD_CITE_RE.search(node))
    if isinstance(node, list):
        return any(_dfs_string_contains_inline_cite(x) for x in node)
    if isinstance(node, dict):
        return any(_dfs_string_contains_inline_cite(v) for v in node.values())
    return False

def _validate_sidecar(payload: Any, path: str, valid_sids: Set[int]) -> Tuple[bool, str]:
    arr = _json_pointer_get(payload, path)
    if not isinstance(arr, list) or not arr:
        return False, "sidecar_missing_or_empty"
    missing_targets = False
    for it in arr:
        if not isinstance(it, dict):
            return False, "sidecar_item_not_object"
        p, s = it.get("path"), it.get("sids")
        if not isinstance(p, str):
            return False, "sidecar_item_path_missing"
        if not (isinstance(s, list) and all(isinstance(x, int) for x in s)):
            return False, "sidecar_item_sids_invalid"
        if not set(s).issubset(valid_sids):
            return False, "sidecar_item_unknown_sid"
        # Optional: ensure pointer resolves to a string (best-effort)
        target = _json_pointer_get(payload, p)
        if target is None and p != "/":
            missing_targets = True
            continue
    return (True, "sidecar_targets_missing") if missing_targets else (True, "sidecar_ok")

def _parse_json(text: str) -> Tuple[Optional[Any], Optional[str]]:
    try:
        return json.loads(text), None
    except Exception as e:
        return None, f"json_parse_error: {e}"

def _parse_yaml(text: str) -> Tuple[Optional[Any], Optional[str]]:
    if yaml is None:
        return None, "yaml_not_available"
    try:
        return yaml.safe_load(text), None
    except Exception as e:
        return None, f"yaml_parse_error: {e}"

def _validate_json_schema(obj: Any, schema_json: str) -> Tuple[bool, str]:
    if not schema_json:
        return True, "no_schema"
    if jsonschema is None:
        return True, "jsonschema_not_available"
    try:
        schema = json.loads(schema_json)
    except Exception as e:
        return False, f"schema_json_invalid: {e}"
    try:
        jsonschema.validate(obj, schema)
        return True, "schema_ok"
    except jsonschema.exceptions.ValidationError as e:
        return False, f"schema_validation_error: {e.message}"

def _basic_html_ok(s: str) -> bool:
    if not s:
        return False
    low = s.strip().lower()
    # accept if looks like an HTML doc
    if "<!doctype html" in low or "<html" in low:
        # also require a plausible end
        return ("</html>" in low) or ("</body>" in low)
    return False

import xml.etree.ElementTree as ET
def _xml_is_wellformed(s: str) -> bool:
    if not s or not s.strip():
        return False
    try:
        ET.fromstring(s)
        return True
    except Exception:
        return False

def _basic_xml_ok(s: str) -> bool:
    return _xml_is_wellformed(s)

def _format_ok(out: str, fmt: str) -> Tuple[bool, str]:

    if fmt == "html":
        ok = _basic_html_ok(out)
        return (ok, "html_ok" if ok else "html_basic_check_failed")
    if fmt in ("markdown", "text"):
        # Always OK structurally; semantic completeness is handled by citations/marker.
        return (len(out.strip()) > 0, "nonempty")
    if fmt == "mermaid":
        # Mermaid is plain text; accept non-empty output.
        return (len(out.strip()) > 0, "nonempty")
    if fmt == "json":
        obj, err = _parse_json(out)
        return ((obj is not None), ("json_ok" if obj is not None else err or "json_parse_error"))
    if fmt == "yaml":
        obj, err = _parse_yaml(out)
        return (obj is not None, err or "yaml_ok")
    if fmt == "xml":
        ok = _basic_xml_ok(out)
        return (ok, "xml_ok" if ok else "xml_basic_check_failed")

    return False, "unknown_format"

def _json_pointer_delete(root: Any, ptr: str) -> Any:
    """
    Return a shallow-copied object with the node at `ptr` removed (best-effort).
    Designed for removing the sidecar path before schema validation.
    Supports dict parents; list indices are ignored safely.
    """
    if not ptr or ptr == "/":
        return root
    cur = root
    parent = None
    key = None
    parts = ptr.strip("/").split("/")
    for raw in parts:
        part = raw.replace("~1", "/").replace("~0", "~")
        parent, key = cur, part
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                idx = int(part)
                cur = cur[idx] if 0 <= idx < len(cur) else None
            except Exception:
                cur = None
        else:
            cur = None
        if cur is None:
            break

    # remove only when parent is a dict and key exists
    if isinstance(parent, dict) and key in parent:
        new_root = json.loads(json.dumps(root, ensure_ascii=False))  # cheap deep copy that’s schema-safe
        p2 = new_root
        for raw in parts[:-1]:
            part = raw.replace("~1", "/").replace("~0", "~")
            p2 = p2.get(part) if isinstance(p2, dict) else None
            if p2 is None:
                return root
        if isinstance(p2, dict):
            p2.pop(parts[-1].replace("~1", "/").replace("~0", "~"), None)
            return new_root
    return root

def truncate_text(content: str, max_length: int) -> str:
    """Truncate content intelligently at sentence boundaries."""
    content = text_utils.strip_surrogates(content)

    if max_length <= 0 or len(content) <= max_length:
        return content

    truncated = content[:max_length]

    # Find last sentence boundary
    for char in ['.', '\n', '!', '?']:
        pos = truncated.rfind(char)
        if pos > max_length * 0.8:
            return truncated[:pos + 1] + "\n\n[... truncated ...]"

    return truncated + "\n\n[... truncated ...]"
