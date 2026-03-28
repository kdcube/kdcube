"""
Links code entities to documentation sections via DOCUMENTED_BY edges.
Uses name matching and path heuristics.
"""

import logging
import re
from pathlib import Path

log = logging.getLogger("coding-core-mcp")


def _normalize_path(p: str) -> str:
    import os
    return p.replace(os.sep, "/").replace("\\", "/")


def extract_doc_sections(docs_root: str, project_root: str) -> list[dict]:
    """Parse markdown files into DocSection nodes (one per header)."""
    root = Path(docs_root)
    proj = Path(project_root)
    sections = []

    if not root.exists():
        log.warning("[DocLinker] Docs root not found: %s", docs_root)
        return sections

    for md_file in sorted(root.rglob("*.md")):
        rel_path = _normalize_path(str(md_file.relative_to(proj)))
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.warning("[DocLinker] Failed to read %s: %s", md_file, e)
            continue

        # Split by headers
        header_re = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
        splits = list(header_re.finditer(text))

        if not splits:
            # No headers — single section
            sections.append({
                "title": md_file.stem,
                "file_path": rel_path,
                "section_path": "#",
                "text_preview": text[:500].strip(),
            })
            continue

        for i, match in enumerate(splits):
            start = match.start()
            end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
            section_text = text[start:end].strip()
            title = match.group(2).strip()

            sections.append({
                "title": title,
                "file_path": rel_path,
                "section_path": f"{'#' * len(match.group(1))} {title}",
                "text_preview": section_text[:500],
            })

    log.info("[DocLinker] Extracted %d doc sections from %s", len(sections), docs_root)
    return sections


def link_docs_to_code(doc_sections: list[dict], classes: list[dict],
                      methods: list[dict] = None) -> list[dict]:
    """
    Create DOCUMENTED_BY edges using name matching.
    Returns list of edge dicts for graph.writers.write_documented_by().
    """
    edges = []

    # Build lookup: class name -> qualified_name
    class_names = {}
    for cls in classes:
        name = cls["name"]
        if len(name) >= 4:  # skip short names to avoid false matches
            class_names[name] = cls["qualified_name"]

    for section in doc_sections:
        text = section.get("text_preview", "")
        title = section.get("title", "")
        combined = f"{title} {text}"

        for class_name, class_qname in class_names.items():
            # Check for exact word match (not substring)
            pattern = r'\b' + re.escape(class_name) + r'\b'
            if re.search(pattern, combined):
                edges.append({
                    "code_qname": class_qname,
                    "doc_file_path": section["file_path"],
                    "doc_section_path": section["section_path"],
                    "relevance": 0.9,
                    "match_type": "name",
                })

    log.info("[DocLinker] Created %d DOCUMENTED_BY edges", len(edges))
    return edges
