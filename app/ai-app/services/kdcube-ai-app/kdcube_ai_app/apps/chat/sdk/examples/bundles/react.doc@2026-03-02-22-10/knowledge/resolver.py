# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── knowledge/resolver.py ──
# Runtime search and read interface for the knowledge space.
#
# This module provides two main functions used by the ReAct agent:
#   - search_knowledge() — weighted lexical search over index.json metadata
#   - read_knowledge()   — resolve a ks:<path> to a physical file and return contents
#
# How search scoring works (metadata-level, NOT semantic/embedding-based):
#   Full phrase in title:   +3.0    Per-term in title:       +1.0
#   Full phrase in summary: +1.5    Per-term in tags/keywords: +0.8
#   Full phrase in path:    +0.7    Per-term in summary:     +0.4
#                                   Per-term in path:        +0.2
#
# KNOWLEDGE_ROOT is a module-level global set by prepare_knowledge_space().
# This module is loaded via importlib with a shared name so that entrypoint.py
# and tools/react_tools.py both access the same state.

from __future__ import annotations

import json
import pathlib
import importlib.util
import os
import sys
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

try:
    from .index_builder import prepare_knowledge_space as _prepare
except Exception:
    # When this module is loaded via spec_from_file_location (no package),
    # relative imports won't work. Load index_builder by file path instead.
    _module_name = "_kdcube_react_doc_index_builder"
    if _module_name in sys.modules:
        _mod = sys.modules[_module_name]
    else:
        _path = pathlib.Path(__file__).resolve().parent / "index_builder.py"
        _spec = importlib.util.spec_from_file_location(_module_name, str(_path))
        if not _spec or not _spec.loader:
            raise ImportError(f"Cannot load index_builder: {_path}")
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_module_name] = _mod
        _spec.loader.exec_module(_mod)  # type: ignore
    _prepare = getattr(_mod, "prepare_knowledge_space")

# Set by prepare_knowledge_space(); shared across entrypoint + tools
KNOWLEDGE_ROOT: Optional[pathlib.Path] = None


def _safe_knowledge_relpath(rel: str) -> bool:
    if rel is None:
        return False
    raw = str(rel).replace("\\", "/").strip()
    if not raw:
        return True
    p = PurePosixPath(raw)
    if p.is_absolute():
        return False
    return all(part not in {"..", ""} for part in p.parts)


def _ensure_knowledge_root() -> Optional[pathlib.Path]:
    global KNOWLEDGE_ROOT
    if KNOWLEDGE_ROOT:
        return KNOWLEDGE_ROOT
    raw = (os.environ.get("BUNDLE_STORAGE_DIR") or "").strip()
    if not raw:
        return None
    candidate = pathlib.Path(raw).expanduser().resolve()
    if not candidate.exists():
        return None
    KNOWLEDGE_ROOT = candidate
    return KNOWLEDGE_ROOT


def prepare_knowledge_space(
    *,
    bundle_root: pathlib.Path,
    knowledge_root: pathlib.Path,
    docs_root: Optional[pathlib.Path] = None,
    src_root: Optional[pathlib.Path] = None,
    deploy_root: Optional[pathlib.Path] = None,
    tests_root: Optional[pathlib.Path] = None,
    validate_refs: bool = True,
    logger: Optional[Any] = None,
) -> Dict[str, Any]:
    global KNOWLEDGE_ROOT
    KNOWLEDGE_ROOT = knowledge_root
    return _prepare(
        bundle_root=bundle_root,
        knowledge_root=knowledge_root,
        docs_root=docs_root,
        src_root=src_root,
        deploy_root=deploy_root,
        tests_root=tests_root,
        validate_refs=validate_refs,
        logger=logger,
    )


def _load_index(root: pathlib.Path) -> Dict[str, Any]:
    index_path = root / "index.json"
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve_exec_namespace(*, logical_ref: str, **kwargs: Any) -> Dict[str, Any]:
    """
    Resolve a react.doc knowledge-space selector or path to an exec-visible physical path.

    This bundle currently exposes ks: against KNOWLEDGE_ROOT / BUNDLE_STORAGE_DIR.
    The returned physical_path is valid only inside isolated exec.
    If generated code browses descendants under that path, it should use the
    input logical_ref as the logical base when emitting follow-up refs for later
    react.read(...) calls.
    """
    raw = str(logical_ref or "").strip()
    unavailable = {
        "physical_path": None,
        "access": "r",
        "browseable": False,
    }
    if not raw:
        return unavailable
    if not raw.startswith("ks:"):
        return unavailable
    if not _ensure_knowledge_root():
        return unavailable

    rel = raw[len("ks:"):].lstrip("/")
    if not _safe_knowledge_relpath(rel):
        return unavailable

    root = pathlib.Path(KNOWLEDGE_ROOT).resolve()
    target = (root / rel).resolve() if rel else root
    try:
        target.relative_to(root)
    except Exception:
        return unavailable

    if not target.exists():
        return unavailable

    return {
        "physical_path": str(target),
        "access": "r",
        "browseable": bool(target.is_dir()),
    }


def _tokenize(text: str) -> List[str]:
    parts = []
    for raw in text.lower().strip().split():
        raw = raw.strip(".,;:!?()[]{}<>\"'`")
        if raw:
            parts.append(raw)
    return parts


def search_knowledge(
    *,
    query: str,
    root: str = "",
    max_hits: int = 20,
    keywords: Optional[List[str]] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """
    Weighted lexical search over index.json metadata.
    Returns a list of hits sorted by score (highest first), up to max_hits.
    """
    if not query:
        return []
    if not _ensure_knowledge_root():
        return []
    index = _load_index(KNOWLEDGE_ROOT)
    items = list(index.get("items") or [])
    if not items:
        return []

    # Compute root filter — restricts search to a subtree (ks:docs, ks:src, ks:deploy)
    root_rel = ""
    if root:
        root = root.strip()
        if root.startswith("ks:"):
            root_rel = root[3:].lstrip("/").rstrip("/")
        elif KNOWLEDGE_ROOT:
            try:
                base = pathlib.Path(root).resolve()
                root_rel = base.relative_to(KNOWLEDGE_ROOT.resolve()).as_posix().rstrip("/")
            except Exception:
                root_rel = root.strip("/").rstrip("/")
    elif KNOWLEDGE_ROOT:
        try:
            root_rel = ""
        except Exception:
            root_rel = ""

    q = query.lower().strip()
    terms = _tokenize(q)
    extra_terms: List[str] = []
    if keywords:
        joined = " ".join([str(k) for k in keywords if str(k).strip()])
        extra_terms = _tokenize(joined)
    scored: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if root_rel and not path.startswith(f"ks:{root_rel}"):
            continue
        title = str(item.get("title") or "")
        summary = str(item.get("summary") or "")
        tags = [str(t).lower() for t in (item.get("tags") or []) if str(t).strip()]
        keywords = [str(t).lower() for t in (item.get("keywords") or []) if str(t).strip()]

        title_l = title.lower()
        summary_l = summary.lower()
        path_l = path.lower()
        tag_set = set(tags + keywords)

        # ── Phrase-level scoring (full query as substring) ──
        score = 0.0
        matched = False
        if q and q in title_l:
            score += 3.0
            matched = True
        if q and q in summary_l:
            score += 1.5
            matched = True
        if q and q in path_l:
            score += 0.7
            matched = True

        # ── Term-level scoring (individual words) ──
        for t in terms + extra_terms:
            if t in title_l:
                score += 1.0
                matched = True
            if t in tag_set:
                score += 0.8
                matched = True
            if t in summary_l:
                score += 0.4
                matched = True
            if t in path_l:
                score += 0.2
                matched = True

        if not matched:
            continue

        scored.append({
            "path": path,
            "title": title,
            "score": score,
        })

    scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored[: max_hits or 20]


def read_knowledge(*, path: str, **kwargs: Any) -> Dict[str, Any]:
    """
    Resolve and read a knowledge-space path (ks:<relpath>).
    Returns dict with keys: text, base64, mime, physical_path, missing.
    """
    if not path or not isinstance(path, str):
        return {"missing": True}
    if not _ensure_knowledge_root():
        return {"missing": True}
    raw = path.strip()
    if raw.startswith("ks:"):
        rel = raw[len("ks:"):].lstrip("/")
    else:
        rel = raw.lstrip("/")
    if not rel:
        return {"missing": True}
    try:
        from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import (
            _safe_relpath,
            _guess_mime_from_path,
            _read_local_file,
        )
    except Exception:
        return {"missing": True}
    if not _safe_relpath(rel):
        return {"missing": True}
    abs_path = (KNOWLEDGE_ROOT / rel).resolve()
    if not abs_path.exists() or not abs_path.is_file():
        return {"missing": True}
    mime = _guess_mime_from_path(str(abs_path))
    text, base64 = _read_local_file(abs_path, mime)
    return {
        "text": text,
        "base64": base64,
        "mime": mime,
        "physical_path": str(abs_path),
    }
