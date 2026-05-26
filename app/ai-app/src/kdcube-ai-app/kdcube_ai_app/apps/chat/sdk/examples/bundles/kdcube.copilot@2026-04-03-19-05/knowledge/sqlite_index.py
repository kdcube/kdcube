# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from typing import Any, Iterable


SCHEMA_VERSION = "1"


def default_sqlite_index_path(knowledge_root: pathlib.Path | str) -> pathlib.Path:
    root = pathlib.Path(knowledge_root).expanduser().resolve()
    return root / ".cache" / "knowledge_search.sqlite"


def _open_sqlite(path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _decode_json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _logical_path_to_rel(logical_path: str) -> str:
    raw = str(logical_path or "").strip()
    if raw.startswith("ks:"):
        return raw[3:].lstrip("/")
    return raw.lstrip("/")


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


def _plain_body_text(text: str) -> str:
    body = _strip_front_matter(text)
    lines: list[str] = []
    in_fence = False
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def _excerpt(text: str, *, max_len: int = 420) -> str:
    body = _plain_body_text(text)
    if len(body) <= max_len:
        return body
    return body[:max_len].rstrip() + "..."


def _read_note_text(knowledge_root: pathlib.Path, logical_path: str) -> tuple[str, str]:
    rel = _logical_path_to_rel(logical_path)
    if not rel:
        return "", ""
    path = (knowledge_root / rel).resolve()
    try:
        path.relative_to(knowledge_root.resolve())
    except Exception:
        return "", ""
    if not path.exists() or not path.is_file():
        return "", ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "", ""
    return _plain_body_text(text), _excerpt(text)


def build_sqlite_search_index(
    *,
    knowledge_root: pathlib.Path | str,
    entries: Iterable[dict[str, Any]],
    index_path: pathlib.Path | str | None = None,
) -> pathlib.Path:
    root = pathlib.Path(knowledge_root).expanduser().resolve()
    db_path = (
        pathlib.Path(index_path).expanduser().resolve()
        if index_path is not None
        else default_sqlite_index_path(root)
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    db_path.with_name(db_path.name + "-wal").unlink(missing_ok=True)
    db_path.with_name(db_path.name + "-shm").unlink(missing_ok=True)

    conn = _open_sqlite(db_path)
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        DROP TABLE IF EXISTS meta;
        DROP TABLE IF EXISTS notes;
        DROP TABLE IF EXISTS notes_fts;
        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE notes (
            docid INTEGER PRIMARY KEY,
            logical_path TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            excerpt TEXT,
            tags_json TEXT NOT NULL,
            keywords_json TEXT NOT NULL,
            see_also_json TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE notes_fts USING fts5(
            path,
            title,
            summary,
            headings,
            excerpt,
            body_text,
            tags,
            keywords,
            see_also,
            tokenize = 'unicode61'
        );
        """
    )

    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        logical_path = str(entry.get("path") or "").strip()
        if not logical_path:
            continue
        count += 1
        title = str(entry.get("title") or pathlib.PurePosixPath(_logical_path_to_rel(logical_path)).name)
        summary = str(entry.get("summary") or "")
        tags = [str(item) for item in (entry.get("tags") or []) if str(item).strip()]
        keywords = [str(item) for item in (entry.get("keywords") or []) if str(item).strip()]
        see_also = [str(item) for item in (entry.get("see_also") or []) if str(item).strip()]
        headings = [
            str(item.get("text") or "").strip()
            for item in (entry.get("headings") or [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        body_text, excerpt = _read_note_text(root, logical_path)
        if not excerpt:
            excerpt = summary

        conn.execute(
            """
            INSERT INTO notes (
                docid, logical_path, kind, title, summary, excerpt,
                tags_json, keywords_json, see_also_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                count,
                logical_path,
                str(entry.get("kind") or "doc"),
                title,
                summary,
                excerpt,
                json.dumps(tags),
                json.dumps(keywords),
                json.dumps(see_also),
            ),
        )
        conn.execute(
            """
            INSERT INTO notes_fts (
                rowid, path, title, summary, headings, excerpt, body_text, tags, keywords, see_also
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                count,
                logical_path,
                title,
                summary,
                " ".join(headings),
                excerpt,
                body_text,
                " ".join(tags),
                " ".join(keywords),
                " ".join(see_also),
            ),
        )

    conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    conn.execute("INSERT INTO meta (key, value) VALUES ('item_count', ?)", (str(count),))
    conn.commit()
    conn.close()
    return db_path


def _fts_terms(text: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9]+", str(text or "").lower()):
        term = raw.strip()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _fts_query(query: str, keywords: Iterable[str] | None = None) -> str | None:
    raw_query = str(query or "").strip()
    if not raw_query:
        return None
    token_text = raw_query
    if keywords:
        token_text += " " + " ".join(str(item or "") for item in keywords)
    terms = _fts_terms(token_text)
    if not terms:
        return None
    clauses = [f"{term}*" if len(term) >= 3 else term for term in terms]
    return " OR ".join(clauses)


def search_sqlite_knowledge(
    *,
    knowledge_root: pathlib.Path | str,
    query: str,
    root: str = "ks:docs",
    keywords: list[str] | None = None,
    max_hits: int = 20,
    index_path: pathlib.Path | str | None = None,
) -> list[dict[str, Any]]:
    fts_query = _fts_query(query, keywords)
    if not fts_query:
        return []

    db_path = (
        pathlib.Path(index_path).expanduser().resolve()
        if index_path is not None
        else default_sqlite_index_path(knowledge_root)
    )
    if not db_path.exists():
        return []

    root_prefix = str(root or "ks:docs").strip()
    if root_prefix in {"ks:", "ks"}:
        root_filter = ""
    else:
        root_filter = root_prefix if root_prefix.startswith("ks:") else f"ks:{root_prefix.lstrip('/')}"
        root_filter = root_filter.rstrip("/")

    where = ["notes_fts MATCH ?"]
    params: list[Any] = [fts_query]
    if root_filter:
        where.append("n.logical_path LIKE ?")
        params.append(f"{root_filter}/%")

    limit = max(1, min(int(max_hits or 20), 100))
    params.append(limit)
    sql = f"""
        SELECT
            n.logical_path,
            n.kind,
            n.title,
            n.summary,
            n.excerpt,
            n.tags_json,
            n.keywords_json,
            n.see_also_json,
            bm25(notes_fts, 1.0, 8.0, 4.0, 2.5, 2.0, 1.2, 1.8, 2.0, 1.0) AS rank
        FROM notes_fts
        JOIN notes n ON n.docid = notes_fts.rowid
        WHERE {" AND ".join(where)}
        ORDER BY rank ASC, n.logical_path ASC
        LIMIT ?
    """
    conn = _open_sqlite(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    results: list[dict[str, Any]] = []
    for row in rows:
        rank = float(row["rank"])
        results.append(
            {
                "path": row["logical_path"],
                "title": row["title"],
                "summary": row["summary"] or "",
                "excerpt": row["excerpt"] or "",
                "kind": row["kind"],
                "score": round(-rank, 3),
                "tags": _decode_json_list(row["tags_json"]),
                "keywords": _decode_json_list(row["keywords_json"]),
                "see_also": _decode_json_list(row["see_also_json"]),
            }
        )
    return results
