# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── knowledge/index_builder.py ──
# Builds the knowledge space index from docs front matter.
#
# Runs at bundle startup (via pre_run_hook → _ensure_knowledge_space).
# The pipeline:
#   1. prepare_knowledge_space() — create knowledge root, mount docs/src/deploy
#      via symlinks (preferred) or copy, then build the index
#   2. build_knowledge_index() — scan all .md files, parse YAML front-matter,
#      generate index.json (structured) + index.md (human-readable)
#   3. validate_doc_refs() — check that backticked code references in docs
#      (e.g. `kdcube_ai_app/apps/...`) point to existing source files
#
# Front-matter fields parsed:
#   title, summary, tags, keywords, see_also, id
#
# Output files:
#   index.json — {"items": [{path, title, summary, tags, keywords, ...}]}
#   index.md   — Markdown listing with usage instructions for the agent

from __future__ import annotations

import json
import pathlib
import shutil
from typing import Iterable, Dict, Any, List, Optional, Tuple

import re


def _safe_symlink(src: pathlib.Path, dst: pathlib.Path) -> bool:
    """Create a symlink dst → src. Returns True if link exists (or was created)."""
    try:
        if dst.exists() or dst.is_symlink():
            return True
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(src, target_is_directory=src.is_dir())
        return True
    except Exception:
        return False


def _copy_tree(src: pathlib.Path, dst: pathlib.Path) -> bool:
    """Fallback: copy entire directory tree when symlink is not possible."""
    try:
        if dst.exists():
            return True
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


def _load_doc_meta(path: pathlib.Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    return _parse_front_matter(text)


def _iter_docs(root: Optional[pathlib.Path]) -> Iterable[pathlib.Path]:
    if not root or not root.exists():
        return
    for path in root.rglob("*.md"):
        if path.name.startswith("."):
            continue
        yield path


def _build_index_entries(
    knowledge_root: pathlib.Path,
    docs_root: Optional[pathlib.Path],
    deploy_root: Optional[pathlib.Path] = None,
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
                "id": meta_id,
                "kind": kind,
            })

    _append_entries(docs_root, "docs", "doc")
    _append_entries(deploy_root, "deploy", "deploy")
    return entries


def build_knowledge_index(
    *,
    knowledge_root: pathlib.Path,
    docs_root: pathlib.Path,
    src_root: Optional[pathlib.Path] = None,
    deploy_root: Optional[pathlib.Path] = None,
    logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Scan all .md files, extract front-matter metadata, and write:
      - index.json — structured index for search_knowledge()
      - index.md   — human-readable doc listing for the agent
    """
    index_path = knowledge_root / "index.json"
    index_md_path = knowledge_root / "index.md"

    # Index docs (+ deploy docs) using front matter metadata
    entries = _build_index_entries(knowledge_root, docs_root, deploy_root=deploy_root)
    payload = {
        "docs_root": "ks:docs",
        "src_root": "ks:src" if src_root else None,
        "deploy_root": "ks:deploy" if deploy_root else None,
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
    ]
    if src_root:
        md_lines += [
            "- Use `react.read([\"ks:src/<path>\"])` to open source files referenced by docs.",
        ]
    if deploy_root:
        md_lines += [
            "- Use `react.search_knowledge(query=..., root=\"ks:deploy\")` to search deployment docs.",
            "- Use `react.read([\"ks:deploy/<path>\"])` to open deployment files (compose, env, Dockerfiles).",
        ]
    md_lines += [
        "",
        "## Docs",
    ]
    for item in entries:
        if item.get("kind") != "doc":
            continue
        md_lines.append(f"- {item.get('path')} — {item.get('title')}")
    if deploy_root:
        md_lines += [
            "",
            "## Deployment",
        ]
        for item in entries:
            if item.get("kind") != "deploy":
                continue
            md_lines.append(f"- {item.get('path')} — {item.get('title')}")
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
    docs_root: Optional[pathlib.Path] = None,
    src_root: Optional[pathlib.Path] = None,
    deploy_root: Optional[pathlib.Path] = None,
    validate_refs: bool = True,
    logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Main entry point for knowledge space setup.
    Creates the knowledge directory, mounts docs/src/deploy via symlinks,
    builds the index, and optionally validates code references.
    """
    knowledge_root.mkdir(parents=True, exist_ok=True)

    # Auto-discover ai-app root (contains docs/ and services/) if roots not provided
    ai_app_root: Optional[pathlib.Path] = None
    if docs_root is None or src_root is None or deploy_root is None:
        for parent in bundle_root.resolve().parents:
            if (parent / "docs").is_dir() and (parent / "services").is_dir():
                ai_app_root = parent
                break

    if docs_root is None and ai_app_root:
        docs_root = ai_app_root / "docs"
    if src_root is None and ai_app_root:
        src_root = ai_app_root / "services" / "kdcube-ai-app" / "kdcube_ai_app"
    if deploy_root is None and ai_app_root:
        deploy_root = ai_app_root / "deployment"

    # Mount docs into knowledge space (symlink preferred, fallback to copy)
    if docs_root and docs_root.exists():
        target = knowledge_root / "docs"
        if not _safe_symlink(docs_root, target):
            _copy_tree(docs_root, target)
    else:
        # Ensure the directory exists even if docs root is missing
        target = knowledge_root / "docs"
        target.mkdir(parents=True, exist_ok=True)

    # Mount sources (read-only, symlink only — too large to copy)
    if src_root and src_root.exists():
        target = knowledge_root / "src"
        _safe_symlink(src_root, target)

    # Mount deployment assets (compose files, env samples, dockerfiles)
    if deploy_root and deploy_root.exists():
        target = knowledge_root / "deploy"
        if not _safe_symlink(deploy_root, target):
            _copy_tree(deploy_root, target)

    payload = build_knowledge_index(
        knowledge_root=knowledge_root,
        docs_root=knowledge_root / "docs",
        src_root=knowledge_root / "src" if (knowledge_root / "src").exists() else None,
        deploy_root=knowledge_root / "deploy" if (knowledge_root / "deploy").exists() else None,
        logger=logger,
    )

    if validate_refs:
        try:
            validate_doc_refs(
                docs_root=knowledge_root / "docs",
                src_root=knowledge_root / "src" if (knowledge_root / "src").exists() else None,
                logger=logger,
            )
        except Exception as exc:
            if logger:
                logger.log(f"[knowledge.validate] failed: {exc}", level="WARNING")

    return payload


# Regex to find backticked code references like `kdcube_ai_app/apps/chat/...`
_CODE_REF_RE = re.compile(r'`(kdcube_ai_app/[^`\s\)\]]+)`')


def _normalize_ref_path(raw: str) -> str:
    """Strip line anchors, trailing punctuation, and kdcube_ai_app/ prefix from a code ref."""
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
    if ref.startswith('kdcube_ai_app/'):
        ref = ref[len('kdcube_ai_app/'):]
    return ref


def validate_doc_refs(
    *,
    docs_root: pathlib.Path,
    src_root: Optional[pathlib.Path],
    logger: Optional[Any] = None,
    max_log: int = 20,
) -> Tuple[int, int]:
    """
    Scan docs for backticked code references and verify they exist under src_root.
    Returns (total_refs, missing_count). Logs warnings for missing references.
    """
    if not src_root or not src_root.exists():
        if logger:
            logger.log("[knowledge.validate] src root missing; skipping ref validation.", level="WARNING")
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
            if not (src_root / ref).exists():
                missing.append((doc, ref))
    if logger:
        if missing:
            logger.log(f"[knowledge.validate] missing refs: {len(missing)} / {total}", level="WARNING")
            for doc, ref in missing[:max_log]:
                logger.log(f"[knowledge.validate] missing: {ref} (in {doc})", level="WARNING")
        else:
            logger.log(f"[knowledge.validate] all refs resolved ({total})", level="INFO")
    return (total, len(missing))
