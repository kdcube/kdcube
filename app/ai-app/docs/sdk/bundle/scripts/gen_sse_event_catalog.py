#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable, List, Set

MARKER_START = "<!-- AUTO-GENERATED: SSE_EVENT_CATALOG_START -->"
MARKER_END = "<!-- AUTO-GENERATED: SSE_EVENT_CATALOG_END -->"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _collect_py_files(root: Path) -> List[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def _scan_event_types(text: str) -> Set[str]:
    out: Set[str] = set()
    for m in re.finditer(r"\btype\s*[:=]\s*['\"]([a-zA-Z0-9_.-]+)['\"]", text):
        out.add(m.group(1))
    for m in re.finditer(r"\betype\s*=\s*['\"]([a-zA-Z0-9_.-]+)['\"]", text):
        out.add(m.group(1))
    return out


def _scan_markers(text: str) -> Set[str]:
    out: Set[str] = set()
    for m in re.finditer(r"\bmarker\s*[:=]\s*['\"]([a-zA-Z0-9_.-]+)['\"]", text):
        out.add(m.group(1))
    return out


def _scan_sub_types(text: str) -> Set[str]:
    out: Set[str] = set()
    for m in re.finditer(r"\bsub_type\s*[:=]\s*['\"]([a-zA-Z0-9_.-]+)['\"]", text):
        out.add(m.group(1))
    return out


def _scan_event_map(text: str) -> Set[str]:
    routes: Set[str] = set()
    in_map = False
    for line in text.splitlines():
        if "_EVENT_MAP" in line and "{" in line:
            in_map = True
        if in_map:
            for m in re.finditer(r"['\"]([a-zA-Z0-9_.-]+)['\"]\s*:\s*['\"]([a-zA-Z0-9_.-]+)['\"]", line):
                routes.add(m.group(2))
            if "}" in line:
                break
    return routes


def _fmt_list(items: Iterable[str]) -> str:
    return "\n".join([f"- `{x}`" for x in sorted(items)])


def _build_catalog(root: Path) -> str:
    chat_root = root / "src" / "kdcube-ai-app" / "kdcube_ai_app" / "apps" / "chat"
    py_files = _collect_py_files(chat_root)

    event_types: Set[str] = set()
    markers: Set[str] = set()
    sub_types: Set[str] = set()
    routes: Set[str] = set()

    for p in py_files:
        text = _read_text(p)
        event_types |= _scan_event_types(text)
        markers |= _scan_markers(text)
        sub_types |= _scan_sub_types(text)
        if p.name == "emitters.py":
            routes |= _scan_event_map(text)

    routes |= {"conv_status", "ready", "server_shutdown"}

    event_types = {t for t in event_types if t and not t.startswith("http")}

    parts: List[str] = []
    parts.append("**Generated Event Catalog (static scan)**")
    parts.append("")
    parts.append("SSE route names:")
    parts.append(_fmt_list(routes) or "- (none)")
    parts.append("")
    parts.append("Event `type` values:")
    parts.append(_fmt_list(event_types) or "- (none)")
    parts.append("")
    parts.append("Delta markers:")
    parts.append(_fmt_list(markers) or "- (none)")
    parts.append("")
    parts.append("Subsystem `sub_type` values:")
    parts.append(_fmt_list(sub_types) or "- (none)")

    return "\n".join(parts).rstrip() + "\n"


def _replace_section(doc: str, new_section: str) -> str:
    if MARKER_START not in doc or MARKER_END not in doc:
        raise RuntimeError("Markers not found in document")
    before, rest = doc.split(MARKER_START, 1)
    _old, after = rest.split(MARKER_END, 1)
    return f"{before}{MARKER_START}\n{new_section}{MARKER_END}{after}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=str, default=None, help="Path to app/ai-app root")
    parser.add_argument("--doc", type=str, default=None, help="Path to bundle-sse-events-README.md")
    parser.add_argument("--write", action="store_true", help="Write back to doc")
    args = parser.parse_args()

    script_path = Path(__file__).resolve()
    default_root = script_path.parents[3]
    root = Path(args.root).resolve() if args.root else default_root

    doc_path = Path(args.doc).resolve() if args.doc else (root / "docs" / "sdk" / "bundle" / "bundle-sse-events-README.md")

    section = _build_catalog(root)

    if not args.write:
        print(section)
        return 0

    doc_text = _read_text(doc_path)
    updated = _replace_section(doc_text, section)
    doc_path.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
