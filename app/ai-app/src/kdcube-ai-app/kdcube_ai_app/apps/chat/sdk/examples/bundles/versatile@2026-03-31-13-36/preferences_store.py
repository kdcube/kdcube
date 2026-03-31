from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_user_id(user_id: str | None) -> str:
    raw = (user_id or "anonymous").strip() or "anonymous"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw)


def ensure_preferences_root(storage_root: Path) -> Path:
    prefs_root = storage_root / "preferences"
    (prefs_root / "users").mkdir(parents=True, exist_ok=True)
    return prefs_root


def _user_root(storage_root: Path, user_id: str | None) -> Path:
    return ensure_preferences_root(storage_root) / "users" / _safe_user_id(user_id)


def _current_path(storage_root: Path, user_id: str | None) -> Path:
    return _user_root(storage_root, user_id) / "current.json"


def _events_path(storage_root: Path, user_id: str | None) -> Path:
    return _user_root(storage_root, user_id) / "events.jsonl"


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_current_preferences(storage_root: Path, user_id: str | None) -> Dict[str, Dict[str, Any]]:
    return _load_json(_current_path(storage_root, user_id), {})


def load_preference_events(storage_root: Path, user_id: str | None) -> List[Dict[str, Any]]:
    path = _events_path(storage_root, user_id)
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            item = json.loads(raw_line)
        except Exception:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def append_preference_event(
    storage_root: Path,
    user_id: str | None,
    *,
    key: str,
    value: Any,
    source: str,
    origin: str,
    evidence: str | None = None,
) -> Dict[str, Any]:
    user_root = _user_root(storage_root, user_id)
    user_root.mkdir(parents=True, exist_ok=True)

    event = {
        "captured_at": _utc_now(),
        "key": str(key).strip(),
        "value": value,
        "source": str(source).strip() or "bundle",
        "origin": str(origin).strip() or "manual",
        "evidence": (evidence or "").strip(),
    }

    events_path = _events_path(storage_root, user_id)
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    current = load_current_preferences(storage_root, user_id)
    current[event["key"]] = {
        "value": value,
        "updated_at": event["captured_at"],
        "source": event["source"],
        "origin": event["origin"],
        "evidence": event["evidence"],
    }
    _current_path(storage_root, user_id).write_text(
        json.dumps(current, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return event


_AUTO_PATTERNS: List[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"\bcall me\s+(?P<value>[^.?!\n]+)", re.IGNORECASE), "preferred_name"),
    (re.compile(r"\bi prefer\s+(?P<value>[^.?!\n]+)", re.IGNORECASE), "preference"),
    (re.compile(r"\bmy favorite (?P<key>[a-zA-Z0-9 _-]{2,40}) is (?P<value>[^.?!\n]+)", re.IGNORECASE), None),
    (re.compile(r"\bi like\s+(?P<value>[^.?!\n]+)", re.IGNORECASE), "likes"),
    (re.compile(r"\bi dislike\s+(?P<value>[^.?!\n]+)", re.IGNORECASE), "dislikes"),
]


def auto_capture_preferences(
    storage_root: Path,
    user_id: str | None,
    *,
    text: str,
    source: str = "chat",
) -> List[Dict[str, Any]]:
    raw_text = (text or "").strip()
    if not raw_text:
        return []

    captured: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for pattern, fixed_key in _AUTO_PATTERNS:
        for match in pattern.finditer(raw_text):
            value = (match.groupdict().get("value") or "").strip(" .,:;")
            if not value:
                continue
            key = fixed_key or (match.groupdict().get("key") or "").strip().lower().replace(" ", "_")
            if not key:
                key = "preference"
            dedupe_key = (key, value.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            captured.append(
                append_preference_event(
                    storage_root,
                    user_id,
                    key=key,
                    value=value,
                    source=source,
                    origin="auto_capture",
                    evidence=raw_text,
                )
            )
    return captured


def _normalize_keywords(kwords: Any) -> List[str]:
    if not kwords:
        return []
    if isinstance(kwords, str):
        parts = re.split(r"[,;\n]+", kwords)
    elif isinstance(kwords, Iterable):
        parts = [str(item) for item in kwords]
    else:
        parts = [str(kwords)]
    return [part.strip().lower() for part in parts if str(part).strip()]


def get_preferences_view(
    storage_root: Path,
    user_id: str | None,
    *,
    recency: int = 10,
    kwords: Any = None,
) -> Dict[str, Any]:
    current = load_current_preferences(storage_root, user_id)
    events = load_preference_events(storage_root, user_id)
    keywords = _normalize_keywords(kwords)

    def _matches(event: Dict[str, Any]) -> bool:
        if not keywords:
            return True
        haystack = " ".join(
            [
                str(event.get("key") or ""),
                str(event.get("value") or ""),
                str(event.get("evidence") or ""),
            ]
        ).lower()
        return all(keyword in haystack for keyword in keywords)

    matched = [event for event in reversed(events) if _matches(event)]
    limited = matched[: max(1, int(recency or 10))]
    current_subset = {
        key: value
        for key, value in current.items()
        if not keywords
        or all(keyword in f"{key} {value.get('value', '')}".lower() for keyword in keywords)
    }
    return {
        "current": current_subset,
        "items": limited,
        "keywords": keywords,
        "matched_count": len(matched),
    }


def build_widget_payload(storage_root: Path, user_id: str | None) -> Dict[str, Any]:
    view = get_preferences_view(storage_root, user_id, recency=15)
    return {
        "user_id": user_id or "anonymous",
        "current": view["current"],
        "recent": view["items"],
        "matched_count": view["matched_count"],
    }
