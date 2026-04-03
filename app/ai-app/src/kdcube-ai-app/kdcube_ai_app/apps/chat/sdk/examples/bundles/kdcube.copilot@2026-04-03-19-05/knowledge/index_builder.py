# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── knowledge/index_builder.py ──
# Builds the knowledge space index from docs front matter.
#
# Runs at bundle startup (via pre_run_hook → _ensure_knowledge_space).
# The pipeline:
#   1. prepare_knowledge_space() — create knowledge root, materialize the common
#      ai-app root layout (docs/, deployment/, src/, ui/) via symlinks
#      (preferred) or copy, then build the index
#   2. build_knowledge_index() — scan all .md files, parse YAML front-matter,
#      generate index.json (structured) + index.md (human-readable)
#   3. validate_doc_refs() — check that backticked code references in docs
#      (e.g. `src/kdcube-ai-app/...`) point to existing files under the
#      common knowledge root
#
# Front-matter fields parsed:
#   title, summary, tags, keywords, see_also, id
# Markdown body fields extracted:
#   headings
#
# Output files:
#   index.json — {"items": [{path, title, summary, tags, keywords, ...}]}
#   index.md   — Markdown listing with usage instructions for the agent

from __future__ import annotations

import json
import os
import pathlib
import shutil
from typing import Iterable, Dict, Any, List, Optional, Tuple

import re


def _remove_target(dst: pathlib.Path) -> None:
    if dst.is_symlink() or dst.is_file():
        dst.unlink()
        return
    if dst.exists():
        shutil.rmtree(dst)


def _safe_symlink(src: pathlib.Path, dst: pathlib.Path) -> bool:
    """Create or replace a symlink dst → src. Returns True when the link is valid."""
    try:
        src = src.resolve()
        if dst.is_symlink():
            try:
                if dst.resolve() == src:
                    return True
            except Exception:
                pass
            _remove_target(dst)
        elif dst.exists():
            try:
                if dst.resolve() == src:
                    return True
            except Exception:
                pass
            _remove_target(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            link_target = pathlib.Path(os.path.relpath(str(src), start=str(dst.parent.resolve())))
        except Exception:
            link_target = src
        dst.symlink_to(link_target, target_is_directory=src.is_dir())
        return dst.exists()
    except Exception:
        return False


def _copy_tree(src: pathlib.Path, dst: pathlib.Path) -> bool:
    """Fallback: replace target with a copied directory tree when symlink is not possible."""
    try:
        if dst.exists() or dst.is_symlink():
            _remove_target(dst)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return True
    except Exception:
        return False


def _parse_front_matter(text: str) -> Dict[str, Any]:
    """
    Parse YAML-like front matter (--- delimited) from a markdown file.
    Handles scalar fields and list fields (tags, keywords, see_also).
    List fields support both inline JSON ([...]) and YAML-style (- item) syntax.
    """
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: Dict[str, Any] = {}
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            break
        if not line.strip():
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in {"see_also", "tags", "keywords"}:
            items: List[str] = []
            # Support inline JSON-style lists (e.g. tags: ["a", "b"])
            if value:
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        items = [str(x) for x in parsed if str(x).strip()]
                        data[key] = items
                        i += 1
                        continue
                except Exception:
                    pass
            j = i + 1
            while j < len(lines):
                l2 = lines[j]
                if l2.strip() == "---":
                    break
                if l2.strip().startswith("-"):
                    items.append(l2.strip().lstrip("-").strip())
                elif l2.strip().startswith("  -"):
                    items.append(l2.strip().lstrip("-").strip())
                elif l2.strip() and not l2.startswith(" "):
                    break
                j += 1
            data[key] = items
            i = j
            continue
        if value:
            # JSON-friendly fields (we emit JSON strings/lists)
            try:
                data[key] = json.loads(value)
            except Exception:
                data[key] = value.strip('"\'' )
        i += 1
    return data


def _strip_front_matter(text: str) -> str:
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return text
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[idx + 1 :])
    return text


def _extract_markdown_headings(text: str, *, skip_texts: Optional[set[str]] = None) -> List[Dict[str, Any]]:
    headings: List[Dict[str, Any]] = []
    body = _strip_front_matter(text)
    in_fence = False
    skip_norm = {s.strip().lower() for s in (skip_texts or set()) if str(s).strip()}
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if not match:
            continue
        text_value = re.sub(r"\s+#+\s*$", "", match.group(2).strip())
        if not text_value:
            continue
        if text_value.strip().lower() in skip_norm:
            continue
        headings.append({
            "level": len(match.group(1)),
            "text": text_value,
        })
    return headings


def _load_doc_meta(path: pathlib.Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    meta = _parse_front_matter(text)
    title = str(meta.get("title") or "").strip()
    meta["headings"] = _extract_markdown_headings(
        text,
        skip_texts={title} if title else None,
    )
    return meta


def _iter_docs(root: Optional[pathlib.Path]) -> Iterable[pathlib.Path]:
    if not root or not root.exists():
        return
    for path in root.rglob("*.md"):
        if path.name.startswith("."):
            continue
        yield path


_MATERIALIZED_TOP_LEVEL_DIRS = ("docs", "deployment", "src", "ui")


def _materialize_top_level_dir(
    *,
    source_root: pathlib.Path,
    knowledge_root: pathlib.Path,
    name: str,
) -> None:
    src = source_root / name
    if not src.exists() or not src.is_dir():
        return
    target = knowledge_root / name
    if not _safe_symlink(src, target):
        _copy_tree(src, target)


def _build_index_entries(
    knowledge_root: pathlib.Path,
    docs_root: Optional[pathlib.Path],
    deployment_root: Optional[pathlib.Path] = None,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    def _append_entries(root: Optional[pathlib.Path], rel_prefix: str, kind: str) -> None:
        for path in _iter_docs(root):
            try:
                rel = path.resolve().relative_to(knowledge_root.resolve())
            except Exception:
                # If root is symlinked, still try to compute relative path via root
                try:
                    rel = pathlib.Path(rel_prefix) / path.relative_to(root)  # type: ignore[arg-type]
                except Exception:
                    continue
            meta = _load_doc_meta(path)
            title = meta.get("title") or path.name
            meta_id = meta.get("id") or ""
            logical_path = meta_id if isinstance(meta_id, str) and meta_id.startswith("ks:") else f"ks:{rel.as_posix()}"
            entries.append({
                "path": logical_path,
                "title": title,
                "summary": meta.get("summary") or "",
                "tags": meta.get("tags") or [],
                "keywords": meta.get("keywords") or [],
                "see_also": meta.get("see_also") or [],
                "headings": meta.get("headings") or [],
                "id": meta_id,
                "kind": kind,
            })

    _append_entries(docs_root, "docs", "doc")
    _append_entries(deployment_root, "deployment", "deployment")
    return entries


def build_knowledge_index(
    *,
    knowledge_root: pathlib.Path,
    docs_root: pathlib.Path,
    deployment_root: Optional[pathlib.Path] = None,
    logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Scan all .md files, extract front-matter metadata, and write:
      - index.json — structured index for search_knowledge()
      - index.md   — human-readable doc listing for the agent
    """
    index_path = knowledge_root / "index.json"
    index_md_path = knowledge_root / "index.md"

    # Index docs (+ deployment docs) using front matter metadata.
    entries = _build_index_entries(knowledge_root, docs_root, deployment_root=deployment_root)
    advertised_roots = []
    for name in _MATERIALIZED_TOP_LEVEL_DIRS:
        if (knowledge_root / name).exists():
            advertised_roots.append(f"ks:{name}")
    payload = {
        "knowledge_root": "ks:",
        "advertised_roots": advertised_roots,
        "items": entries,
    }
    try:
        index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        if logger:
            logger.log(f"[knowledge.index] failed to write index.json: {exc}", level="WARNING")

    md_lines = [
        "# Knowledge Space Index",
        "",
        "This bundle exposes a read‑only knowledge space you can search and read.",
        "",
        "## How to use",
        "- Use `react.search_knowledge(query=..., root=\"ks:docs\")` to search docs.",
        "- Use `react.read([\"ks:docs/<path>\"])` to open a doc.",
        "- Use exact common-root-relative paths under `ks:` for source, deployment, test, or UI files, for example `ks:src/kdcube-ai-app/...`.",
    ]
    if (knowledge_root / "src").exists():
        md_lines += [
            "- Knowledge-space browsing in code should start from a real subtree such as `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk`.",
        ]
    if deployment_root:
        md_lines += [
            "- Use `react.search_knowledge(query=..., root=\"ks:deployment\")` to search deployment docs.",
            "- Use `react.read([\"ks:deployment/<path>\"])` to open deployment files (compose, env, Dockerfiles).",
        ]
    advertised_examples: list[tuple[str, str, str]] = []
    sdk_root = knowledge_root / "src" / "kdcube-ai-app" / "kdcube_ai_app" / "apps" / "chat" / "sdk"
    infra_root = knowledge_root / "src" / "kdcube-ai-app" / "kdcube_ai_app" / "apps" / "infra"
    tests_root = sdk_root / "tests" / "bundle"
    if (knowledge_root / "docs").exists():
        advertised_examples.append((
            "`ks:docs`",
            "platform docs",
            "searchable, exact-readable, and browseable in exec via `bundle_data.resolve_namespace(...)`",
        ))
    if (knowledge_root / "deployment").exists():
        advertised_examples.append((
            "`ks:deployment`",
            "deployment files and deployment markdown",
            "deployment markdown is searchable; exact file reads and exec browsing use exact `ks:` paths",
        ))
    if sdk_root.exists():
        advertised_examples.append((
            "`ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk`",
            "SDK source",
            "not indexed for search; exact-readable when path is known; browseable in exec",
        ))
    if infra_root.exists():
        advertised_examples.append((
            "`ks:src/kdcube-ai-app/kdcube_ai_app/apps/infra`",
            "infrastructure source",
            "not indexed for search; exact-readable when path is known; browseable in exec",
        ))
    if tests_root.exists():
        advertised_examples.append((
            "`ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`",
            "bundle pytest suite",
            "not indexed for search; exact-readable when path is known; browseable in exec",
        ))
    if advertised_examples:
        md_lines += [
            "",
            "## Advertised roots",
        ]
        for logical_root, meaning, access in advertised_examples:
            md_lines.append(f"- {logical_root} — {meaning}; {access}.")
    md_lines += [
        "",
        "## Docs",
    ]
    for item in entries:
        if item.get("kind") != "doc":
            continue
        md_lines.append(f"- {item.get('path')} — {item.get('title')}")
        if item.get("summary"):
            md_lines.append(f"  summary: {item.get('summary')}")
        if item.get("tags"):
            md_lines.append(f"  tags: {', '.join(str(t) for t in item.get('tags') or [])}")
        if item.get("keywords"):
            md_lines.append(f"  keywords: {', '.join(str(t) for t in item.get('keywords') or [])}")
        if item.get("see_also"):
            md_lines.append(f"  see also: {', '.join(str(t) for t in item.get('see_also') or [])}")
        headings = [str(h.get("text") or "").strip() for h in (item.get("headings") or []) if isinstance(h, dict)]
        if headings:
            md_lines.append(f"  sections: {'; '.join(headings)}")
    if deployment_root:
        md_lines += [
            "",
            "## Deployment",
        ]
        for item in entries:
            if item.get("kind") != "deployment":
                continue
            md_lines.append(f"- {item.get('path')} — {item.get('title')}")
            if item.get("summary"):
                md_lines.append(f"  summary: {item.get('summary')}")
            if item.get("tags"):
                md_lines.append(f"  tags: {', '.join(str(t) for t in item.get('tags') or [])}")
            if item.get("keywords"):
                md_lines.append(f"  keywords: {', '.join(str(t) for t in item.get('keywords') or [])}")
            if item.get("see_also"):
                md_lines.append(f"  see also: {', '.join(str(t) for t in item.get('see_also') or [])}")
            headings = [str(h.get("text") or "").strip() for h in (item.get("headings") or []) if isinstance(h, dict)]
            if headings:
                md_lines.append(f"  sections: {'; '.join(headings)}")
    try:
        index_md_path.write_text("\n".join(md_lines).strip() + "\n", encoding="utf-8")
    except Exception as exc:
        if logger:
            logger.log(f"[knowledge.index] failed to write index.md: {exc}", level="WARNING")

    return payload


def prepare_knowledge_space(
    *,
    bundle_root: pathlib.Path,
    knowledge_root: pathlib.Path,
    source_root: Optional[pathlib.Path] = None,
    validate_refs: bool = True,
    logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Main entry point for knowledge space setup.
    Creates the knowledge directory, materializes the common ai-app root layout,
    builds the index, and optionally validates code references.
    """
    knowledge_root.mkdir(parents=True, exist_ok=True)

    # Auto-discover ai-app root (contains docs/ and src/) if source root is not provided.
    ai_app_root: Optional[pathlib.Path] = None
    if source_root is None:
        for parent in bundle_root.resolve().parents:
            if (parent / "docs").is_dir() and (parent / "src").is_dir():
                ai_app_root = parent
                break

    if source_root is None and ai_app_root:
        source_root = ai_app_root

    if source_root and source_root.exists():
        for name in _MATERIALIZED_TOP_LEVEL_DIRS:
            _materialize_top_level_dir(
                source_root=source_root,
                knowledge_root=knowledge_root,
                name=name,
            )
    else:
        (knowledge_root / "docs").mkdir(parents=True, exist_ok=True)

    payload = build_knowledge_index(
        knowledge_root=knowledge_root,
        docs_root=knowledge_root / "docs",
        deployment_root=knowledge_root / "deployment" if (knowledge_root / "deployment").exists() else None,
        logger=logger,
    )

    if validate_refs:
        try:
            validate_doc_refs(
                docs_root=knowledge_root / "docs",
                knowledge_root=knowledge_root,
                logger=logger,
            )
        except Exception as exc:
            if logger:
                logger.log(f"[knowledge.validate] failed: {exc}", level="WARNING")

    return payload


# Regex to find backticked common-root-relative references such as
# `src/kdcube-ai-app/...`, `deployment/...`, `docs/...`, or `ui/...`.
_CODE_REF_RE = re.compile(
    r'`((?:app/ai-app/)?(?:docs|deployment|src|ui)/[^`\s\)\]]+)`'
)


def _normalize_ref_path(raw: str) -> str:
    """Strip line anchors, trailing punctuation, and the optional app/ai-app/ prefix."""
    ref = raw.strip().rstrip(').,;')
    # strip line/anchor hints
    if '#L' in ref:
        ref = ref.split('#L', 1)[0]
    if '::' in ref:
        ref = ref.split('::', 1)[0]
    if ':' in ref and ref.endswith('.py') is False:
        # tolerate "file.py:123"
        if '.py:' in ref:
            ref = ref.split('.py:', 1)[0] + '.py'
    ref = ref.lstrip('/')
    if ref.startswith('app/ai-app/'):
        ref = ref[len('app/ai-app/'):]
    return ref


def validate_doc_refs(
    *,
    docs_root: pathlib.Path,
    knowledge_root: Optional[pathlib.Path],
    logger: Optional[Any] = None,
    max_log: int = 20,
) -> Tuple[int, int]:
    """
    Scan docs for backticked common-root-relative references and verify they exist
    under the prepared knowledge root.
    Returns (total_refs, missing_count). Logs warnings for missing references.
    """
    if not knowledge_root or not knowledge_root.exists():
        if logger:
            logger.log("[knowledge.validate] knowledge root missing; skipping ref validation.", level="WARNING")
        return (0, 0)
    total = 0
    missing = []
    for doc in docs_root.rglob("*.md"):
        try:
            text = doc.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for match in _CODE_REF_RE.finditer(text):
            total += 1
            ref = _normalize_ref_path(match.group(1))
            if not (knowledge_root / ref).exists():
                missing.append((doc, ref))
    if logger:
        if missing:
            logger.log(f"[knowledge.validate] missing refs: {len(missing)} / {total}", level="WARNING")
            for doc, ref in missing[:max_log]:
                logger.log(f"[knowledge.validate] missing: {ref} (in {doc})", level="WARNING")
        else:
            logger.log(f"[knowledge.validate] all refs resolved ({total})", level="INFO")
    return (total, len(missing))
