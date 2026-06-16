# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""HybridIndex — generic SQLite + vector hybrid search for any per-scope collection.

Internalizes: SQLite document store, FTS5 lexical (bm25), embed-on-write vector
cache, vector-store (re)build with a version signature, and RRF fusion of
lexical + semantic + recency. Callers hand it `Document`s and a query string.

Example:
    idx = HybridIndex(IndexConfig(
        db_path=Path(".../pins.index.sqlite"),
        embed_fn=model_service.embed_texts,   # async batch embedder
        dim=1536,
        vector_store=BruteForceVectorStore(),  # or LocalFaissStore(path)
    ))
    await idx.upsert([Document(id, text, metadata, timestamp)])
    hits = await idx.search("query", top_k=20, filters={"board": board_id})
"""
from __future__ import annotations

import inspect
import json
import re
import sqlite3
import time
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Sequence

from .fusion import recency_score, rrf_fuse
from .schema import SCHEMA_SQL
from .types import Document, IndexConfig, SearchHit

SearchMode = Literal["hybrid", "lexical", "semantic"]


class HybridIndex:
    def __init__(self, config: IndexConfig) -> None:
        self.cfg = config
        self.db_path = Path(config.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            # A volatile (in-memory) vector store doesn't survive a new instance, so
            # the persisted build signature can't be trusted — force a rebuild from
            # the cached vectors on first use. File/cross-process stores keep theirs.
            if getattr(config.vector_store, "volatile", True):
                self._set_meta(conn, "built_version", -1)
        self._qcache: "OrderedDict[str, List[float]]" = OrderedDict()

    # ---- connection ----
    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- meta / versioning ----
    @staticmethod
    def _get_int(conn: sqlite3.Connection, key: str, default: int) -> int:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return int(row["value"]) if row and row["value"] is not None else default

    @staticmethod
    def _set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def _bump_version(self, conn: sqlite3.Connection) -> None:
        self._set_meta(conn, "data_version", self._get_int(conn, "data_version", 0) + 1)

    # ---- writes ----
    async def upsert(self, docs: Iterable[Document]) -> None:
        docs = list(docs)
        if not docs:
            return
        # Embed only NEW or text-changed docs (embed-on-write): metadata/timestamp
        # edits don't re-embed and don't trigger a vector rebuild.
        ids = [d.id for d in docs]
        with self._conn() as conn:
            placeholders = ",".join("?" * len(ids))
            existing = {
                r["id"]: r["text"]
                for r in conn.execute(f"SELECT id, text FROM docs WHERE id IN ({placeholders})", ids).fetchall()
            }
        to_embed = [d for d in docs if existing.get(d.id) != d.text]
        vec_by_id: Dict[str, List[float]] = {}
        if to_embed:
            vectors = await self.cfg.embed_fn([d.text for d in to_embed])
            vec_by_id = {d.id: v for d, v in zip(to_embed, vectors)}

        now = time.time()
        vectors_changed = False
        with self._conn() as conn:
            for doc in docs:
                ts = float(doc.timestamp) if doc.timestamp is not None else now
                conn.execute(
                    "INSERT INTO docs(id,text,metadata_json,ts) VALUES(?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET text=excluded.text, "
                    "metadata_json=excluded.metadata_json, ts=excluded.ts",
                    (doc.id, doc.text, json.dumps(doc.metadata or {}), ts),
                )
                rid = conn.execute("SELECT rowid FROM docs WHERE id=?", (doc.id,)).fetchone()["rowid"]
                if doc.id in vec_by_id:  # new or text changed → refresh FTS + vector
                    conn.execute("DELETE FROM docs_fts WHERE rowid=?", (rid,))
                    conn.execute("INSERT INTO docs_fts(rowid,text) VALUES(?,?)", (rid, doc.text))
                    conn.execute(
                        "INSERT INTO vectors(rowid,vec) VALUES(?,?) "
                        "ON CONFLICT(rowid) DO UPDATE SET vec=excluded.vec",
                        (rid, json.dumps([float(x) for x in vec_by_id[doc.id]])),
                    )
                    vectors_changed = True
            if vectors_changed:
                self._bump_version(conn)

    async def delete(self, ids: Iterable[str]) -> None:
        ids = [str(i) for i in ids]
        if not ids:
            return
        with self._conn() as conn:
            for doc_id in ids:
                row = conn.execute("SELECT rowid FROM docs WHERE id=?", (doc_id,)).fetchone()
                if not row:
                    continue
                rid = row["rowid"]
                conn.execute("DELETE FROM docs_fts WHERE rowid=?", (rid,))
                conn.execute("DELETE FROM vectors WHERE rowid=?", (rid,))
                conn.execute("DELETE FROM docs WHERE rowid=?", (rid,))
            self._bump_version(conn)

    def count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) AS c FROM docs").fetchone()["c"])

    def ids(self, filters: Dict[str, Any] | None = None) -> List[str]:
        """All indexed ids (optionally metadata-filtered). Lets a caller diff the
        index against a live collection to find removals to delete()."""
        fclause, fparams = self._filter_sql(filters, "docs")
        sql = "SELECT id FROM docs" + (f" WHERE 1=1{fclause}" if fclause else "")
        with self._conn() as conn:
            return [r["id"] for r in conn.execute(sql, fparams).fetchall()]

    # ---- (re)build the vector store iff data changed since last build ----
    async def ensure_built(self) -> None:
        with self._conn() as conn:
            data_version = self._get_int(conn, "data_version", 0)
            built_version = self._get_int(conn, "built_version", -1)
            if built_version == data_version:
                return
            rows = conn.execute("SELECT rowid AS rid, vec FROM vectors").fetchall()
        items = [(int(r["rid"]), json.loads(r["vec"])) for r in rows]
        self.cfg.vector_store.rebuild(items, self.cfg.dim)
        with self._conn() as conn:
            self._set_meta(conn, "built_version", data_version)

    async def rebuild(self) -> None:
        with self._conn() as conn:
            self._set_meta(conn, "built_version", -1)
        await self.ensure_built()

    # ---- search ----
    async def search(
        self,
        query: str,
        *,
        top_k: int = 20,
        filters: Dict[str, Any] | None = None,
        mode: SearchMode = "hybrid",
    ) -> List[SearchHit]:
        limit = max(1, top_k) * max(1, self.cfg.overfetch)
        rankings: Dict[str, List[str]] = {}

        do_semantic = mode in ("hybrid", "semantic") and await self._semantic_allowed(query)
        # If semantic was requested but the economical guard denied it, degrade to
        # lexical so the query still returns results (and costs no embed call).
        do_lexical = mode in ("hybrid", "lexical") or (mode == "semantic" and not do_semantic)

        if do_lexical:
            with self._conn() as conn:
                rankings["lexical"] = self._lexical(conn, query, limit, filters)
        if do_semantic:
            rankings["semantic"] = await self._semantic(query, limit, filters)

        with self._conn() as conn:
            recency = None
            if mode == "hybrid":
                candidates = set().union(*[set(v) for v in rankings.values()]) if rankings else set()
                recency = self._recency(conn, candidates)
            contrib = rrf_fuse(rankings, weights=self.cfg.weights, k=self.cfg.rrf_k, recency=recency)
            ranked = sorted(contrib.items(), key=lambda kv: kv[1]["score"], reverse=True)[: max(1, top_k)]
            return self._hydrate(conn, ranked)

    # ---- internals ----
    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9]+", str(query or "").lower())
        return " OR ".join(f"{t}*" for t in tokens)

    @staticmethod
    def _filter_sql(filters: Dict[str, Any] | None, alias: str) -> tuple[str, list]:
        if not filters:
            return "", []
        clauses, params = [], []
        for key, value in filters.items():
            safe = re.sub(r"[^A-Za-z0-9_]", "", str(key))
            if not safe:
                continue
            clauses.append(f"json_extract({alias}.metadata_json, '$.{safe}') = ?")
            params.append(value)
        return ((" AND " + " AND ".join(clauses)) if clauses else ""), params

    def _lexical(self, conn, query, limit, filters) -> List[str]:
        match = self._fts_query(query)
        if not match:
            return []
        fclause, fparams = self._filter_sql(filters, "d")
        sql = (
            "SELECT d.id AS id, bm25(docs_fts) AS rank "
            "FROM docs_fts JOIN docs d ON d.rowid = docs_fts.rowid "
            f"WHERE docs_fts MATCH ?{fclause} ORDER BY rank LIMIT ?"
        )
        rows = conn.execute(sql, [match, *fparams, limit]).fetchall()
        return [r["id"] for r in rows]

    async def _semantic_allowed(self, query: str) -> bool:
        """Economical guard: only spend the embedder call when it's worth it.
        The guard may be sync or async (e.g. an economics `economic_preflight`)."""
        if not self.cfg.semantic_enabled:
            return False
        if len(str(query or "").strip()) < self.cfg.semantic_min_chars:
            return False
        guard = self.cfg.semantic_guard
        if guard is not None:
            try:
                result = guard(query)
                if inspect.isawaitable(result):
                    result = await result
                return bool(result)
            except Exception:
                return False  # fail closed: never pay for a query the guard couldn't clear
        return True

    async def _embed_query(self, query: str) -> List[float]:
        """Embed a query once; cache it (LRU) so repeats — pagination, debounced
        typeahead, the same term across boards — don't re-pay the embedder."""
        cache = self._qcache
        if query in cache:
            cache.move_to_end(query)
            return cache[query]
        vec = (await self.cfg.embed_fn([query]))[0]
        cache[query] = vec
        if len(cache) > max(1, self.cfg.query_cache_size):
            cache.popitem(last=False)
        return vec

    async def _semantic(self, query, limit, filters) -> List[str]:
        await self.ensure_built()
        qvec = await self._embed_query(query)
        hits = self.cfg.vector_store.search(qvec, limit)
        floor = self.cfg.min_semantic_score
        order = [rid for rid, score in hits if score > floor]
        if not order:
            return []
        with self._conn() as conn:
            placeholders = ",".join("?" * len(order))
            fclause, fparams = self._filter_sql(filters, "d")
            rows = conn.execute(
                f"SELECT d.rowid AS rid, d.id AS id FROM docs d WHERE d.rowid IN ({placeholders}){fclause}",
                [*order, *fparams],
            ).fetchall()
        by_rid = {int(r["rid"]): r["id"] for r in rows}
        return [by_rid[rid] for rid in order if rid in by_rid]

    def _recency(self, conn, ids: Sequence[str]) -> Dict[str, float]:
        ids = list(ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(f"SELECT id, ts FROM docs WHERE id IN ({placeholders})", ids).fetchall()
        return {
            r["id"]: recency_score(r["ts"], half_life_days=self.cfg.recency_half_life_days)
            for r in rows
        }

    def _hydrate(self, conn, ranked: List[tuple[str, dict]]) -> List[SearchHit]:
        if not ranked:
            return []
        ids = [doc_id for doc_id, _ in ranked]
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, metadata_json FROM docs WHERE id IN ({placeholders})", ids
        ).fetchall()
        meta = {r["id"]: json.loads(r["metadata_json"] or "{}") for r in rows}
        out: List[SearchHit] = []
        for doc_id, entry in ranked:
            if doc_id not in meta:
                continue
            out.append(SearchHit(id=doc_id, score=entry["score"], metadata=meta[doc_id], sub=entry["sub"]))
        return out
