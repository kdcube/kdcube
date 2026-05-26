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
#      generate index.json, SQLite FTS retrieval, and compact index.md
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
#   index.md   — compact navigation note for the agent

from __future__ import annotations

import json
import os
import pathlib
import shutil
from typing import Iterable, Dict, Any, List, Optional, Tuple

import re

try:
    from .sqlite_index import build_sqlite_search_index
except Exception:
    import importlib.util
    import sys

    _module_name = "_kdcube_copilot_sqlite_index"
    if _module_name in sys.modules:
        _sqlite_mod = sys.modules[_module_name]
    else:
        _path = pathlib.Path(__file__).resolve().parent / "sqlite_index.py"
        _spec = importlib.util.spec_from_file_location(_module_name, str(_path))
        if not _spec or not _spec.loader:
            raise ImportError(f"Cannot load sqlite_index: {_path}")
        _sqlite_mod = importlib.util.module_from_spec(_spec)
        sys.modules[_module_name] = _sqlite_mod
        _spec.loader.exec_module(_sqlite_mod)  # type: ignore
    build_sqlite_search_index = getattr(_sqlite_mod, "build_sqlite_search_index")


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
      - index.md   — compact navigation note for the agent
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
    try:
        sqlite_path = build_sqlite_search_index(
            knowledge_root=knowledge_root,
            entries=entries,
        )
        if logger:
            logger.log(
                f"[knowledge.index] sqlite search index ready: {sqlite_path} items={len(entries)}",
                level="INFO",
            )
    except Exception as exc:
        if logger:
            logger.log(f"[knowledge.index] failed to write sqlite search index: {exc}", level="WARNING")

    md_lines = [
        "# Knowledge Space Index",
        "",
        "This bundle exposes a read‑only knowledge space for platform docs, deployment material, source, UI, and tests.",
        "",
        "## How to use",
        "- Use `react.search_knowledge(query=..., root=\"ks:docs\")` for documentation retrieval.",
        "- Use `react.search_knowledge(query=..., root=\"ks:deployment\")` for deployment markdown.",
        "- Open exact search hits with `react.read([\"ks:...\"])`.",
        "- Use exact common-root-relative paths under `ks:` for source, deployment, test, or UI files.",
    ]
    if (knowledge_root / "src").exists():
        md_lines += [
            "- Knowledge-space browsing in code should start from a real subtree such as `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk`.",
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
    by_path = {str(item.get("path") or ""): item for item in entries if isinstance(item, dict)}
    curated_sections: list[tuple[str, list[str]]] = [
        (
            "Start here for bundle builders",
            [
                "ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md",
                "ks:docs/sdk/bundle/build/how-to-write-bundle-README.md",
                "ks:docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md",
                "ks:docs/sdk/bundle/bundle-index-README.md",
                "ks:docs/sdk/bundle/versatile-reference-bundle-README.md",
                "ks:docs/what-you-can-do-with-kdcube-README.md",
            ],
        ),
        (
            "Run, configure, test, and release",
            [
                "ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md",
                "ks:docs/sdk/bundle/build/how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md",
                "ks:docs/sdk/bundle/build/how-to-test-bundle-README.md",
                "ks:docs/sdk/bundle/build/how-to-release-bundle-content-README.md",
                "ks:docs/service/cicd/cli-README.md",
            ],
        ),
        (
            "Client UI, widgets, and streaming",
            [
                "ks:docs/sdk/bundle/bundle-client-ui-README.md",
                "ks:docs/sdk/bundle/bundle-widget-integration-README.md",
                "ks:docs/sdk/bundle/ui-components-lifecycle-README.md",
                "ks:docs/sdk/bundle/bundle-client-communication-README.md",
                "ks:docs/sdk/bundle/bundle-chat-stream-events-README.md",
                "ks:docs/sdk/streaming/streaming-widget-README.md",
                "ks:docs/sdk/streaming/llm-streaming-README.md",
            ],
        ),
        (
            "Storage, isolation, and execution",
            [
                "ks:docs/sdk/bundle/bundle-storage-and-cache-README.md",
                "ks:docs/hosting/files-storage-system-README.md",
                "ks:docs/exec/README-iso-runtime.md",
                "ks:docs/exec/README-runtime-modes-builtin-tools.md",
            ],
        ),
        (
            "Agents, tools, skills, and Claude Code",
            [
                "ks:docs/sdk/bundle/bundle-agent-integration-README.md",
                "ks:docs/sdk/agents/react/react-context-README.md",
                "ks:docs/sdk/agents/react/react-tools-README.md",
                "ks:docs/sdk/agents/react/flow-README.md",
                "ks:docs/sdk/tools/custom-tools-README.md",
                "ks:docs/sdk/tools/tool-subsystem-README.md",
                "ks:docs/sdk/tools/mcp-README.md",
                "ks:docs/sdk/skills/skills-README.md",
                "ks:docs/sdk/skills/custom-skills-README.md",
                "ks:docs/sdk/agents/claude/claude-code-README.md",
            ],
        ),
        (
            "Configuration, secrets, and properties",
            [
                "ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md",
                "ks:docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md",
                "ks:docs/sdk/bundle/bundle-reserved-platform-properties-README.md",
                "ks:docs/configuration/service-runtime-configuration-mapping-README.md",
                "ks:docs/configuration/bundles-descriptor-README.md",
                "ks:docs/configuration/bundles-secrets-descriptor-README.md",
                "ks:docs/configuration/secrets-descriptor-README.md",
                "ks:docs/configuration/assembly-descriptor-README.md",
            ],
        ),
        (
            "Jobs, transports, and architecture",
            [
                "ks:docs/sdk/bundle/bundle-scheduled-jobs-README.md",
                "ks:docs/sdk/bundle/bundle-transports-README.md",
                "ks:docs/arch/architecture-short.md",
            ],
        ),
    ]
    curated_any = any(any(path in by_path for path in paths) for _, paths in curated_sections)
    if curated_any:
        md_lines += [
            "",
            "## Builder map",
        ]
        for title, paths in curated_sections:
            items = [by_path[path] for path in paths if path in by_path]
            if not items:
                continue
            md_lines += ["", f"### {title}"]
            for item in items:
                summary = str(item.get("summary") or "").strip()
                line = f"- {item.get('path')} — {item.get('title')}"
                if summary:
                    line += f": {summary}"
                md_lines.append(line)
    md_lines += [
        "",
        "## Retrieval index",
        f"- `index.json` contains {len(entries)} metadata rows for compatibility.",
        "- `.cache/knowledge_search.sqlite` is the retrieval index used by `react.search_knowledge` and the doc-reader MCP.",
        "- Search results return compact hits; read exact hits instead of loading the whole catalog.",
    ]
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
