# SPDX-License-Identifier: MIT
# chat/sdk/retrieval/code_graph_client.py
"""
Async Neo4j client for the code knowledge graph.
Mirrors the coding-core MCP tools but runs inside the chat-proc process.
Supports fulltext, vector, and hybrid code search.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from neo4j import AsyncGraphDatabase
except ImportError:
    AsyncGraphDatabase = None

# ---------------------------------------------------------------------------
# Lazy-loaded embedding model (sentence-transformers, same as coding-core)
# ---------------------------------------------------------------------------
_embedding_model = None


def _get_embedding_model(model_name: str = "all-MiniLM-L6-v2"):
    """Lazy-load sentence-transformers model. Called once on first vector search."""
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model
    try:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading code embedding model '%s'...", model_name)
        _embedding_model = SentenceTransformer(model_name)
        logger.info("Code embedding model ready")
        return _embedding_model
    except ImportError:
        logger.warning("sentence-transformers not installed; vector search unavailable")
        return None


def _embed(texts: List[str], model_name: str = "all-MiniLM-L6-v2") -> List[List[float]]:
    """Embed texts using the code embedding model."""
    model = _get_embedding_model(model_name)
    if model is None:
        return []
    embeddings = model.encode(texts, show_progress_bar=False)
    return [e.tolist() for e in embeddings]


# ---------------------------------------------------------------------------
# Cypher queries (mirrored from integration/coding-core/graph/queries.py)
# ---------------------------------------------------------------------------

FIND_ENTRY_POINTS = """
    MATCH (r:Route)-[:HANDLED_BY]->(m)
    OPTIONAL MATCH (m)<-[:CONTAINS_METHOD]-(c:Class)
    RETURN r.path AS route, r.http_method AS method, r.transport AS transport,
           m.name AS handler, c.name AS handler_class, m.file_path AS file
    ORDER BY r.path
"""

CALL_CHAIN_TRACE = """
    MATCH path = (entry)-[:CALLS*1..5]->(leaf)
    WHERE entry.qualified_name = $qname
    WITH path, [n in nodes(path) | n.qualified_name] AS chain
    RETURN chain, length(path) AS depth
    ORDER BY depth LIMIT 50
"""

FIND_REFERENCES = """
    MATCH (target {qualified_name: $qname})
    OPTIONAL MATCH (caller)-[:CALLS]->(target)
    OPTIONAL MATCH (child:Class)-[:INHERITS]->(target)
    OPTIONAL MATCH (impl:Class)-[:IMPLEMENTS]->(target)
    OPTIONAL MATCH (override:Method)-[:OVERRIDES]->(target)
    OPTIONAL MATCH (ref)-[:REFERENCES]->(target)
    OPTIONAL MATCH (test:Test)-[:TESTS]->(target)
    RETURN target.name AS name, target.qualified_name AS qualified_name,
           collect(DISTINCT caller.qualified_name) AS callers,
           collect(DISTINCT child.qualified_name) AS subclasses,
           collect(DISTINCT impl.qualified_name) AS implementors,
           collect(DISTINCT override.qualified_name) AS overrides,
           collect(DISTINCT ref.qualified_name) AS references,
           collect(DISTINCT test.qualified_name) AS tests
"""

FIND_SIBLINGS = """
    MATCH (cls:Class {name: $name})-[:INHERITS]->(parent:Class)
    MATCH (sibling:Class)-[:INHERITS]->(parent)
    WHERE sibling <> cls
    RETURN sibling.name AS name,
           sibling.qualified_name AS qualified_name,
           parent.name AS shared_parent,
           sibling.docstring AS docstring
    ORDER BY sibling.name
"""

SHOW_CONTRACT = """
    MATCH (cls {qualified_name: $qname})
    OPTIONAL MATCH (cls)-[:CONTAINS_METHOD]->(m:Method)
    WHERE m.is_abstract = true OR m.name STARTS WITH '__'
    RETURN m.name AS method,
           m.signature AS signature,
           m.docstring AS docstring,
           m.is_abstract AS is_abstract
    ORDER BY m.name
"""

CLASS_FOOTPRINT = """
    MATCH (cls:Class {qualified_name: $qname})
    OPTIONAL MATCH (cls)-[:INHERITS]->(parent:Class)
    OPTIONAL MATCH (child:Class)-[:INHERITS]->(cls)
    OPTIONAL MATCH (cls)-[:CONTAINS_METHOD]->(m:Method)
    OPTIONAL MATCH (cls)-[:CONTAINS_PROPERTY]->(p:Property)
    OPTIONAL MATCH (caller)-[:CALLS]->(m)
    OPTIONAL MATCH (m)-[:CALLS]->(callee)
    OPTIONAL MATCH (cls)-[:DOCUMENTED_BY]->(doc:DocSection)
    OPTIONAL MATCH (test:Test)-[:TESTS]->(cls)
    OPTIONAL MATCH (m)-[:DECORATED_BY]->(dec)
    RETURN cls.name AS name, cls.qualified_name AS qualified_name,
           cls.docstring AS docstring, cls.file_path AS file_path,
           collect(DISTINCT parent.qualified_name) AS ancestors,
           collect(DISTINCT child.qualified_name) AS descendants,
           collect(DISTINCT {name: m.name, signature: m.signature, docstring: m.docstring, is_abstract: m.is_abstract}) AS methods,
           collect(DISTINCT p.name) AS properties,
           collect(DISTINCT caller.qualified_name) AS callers,
           collect(DISTINCT callee.qualified_name) AS callees,
           collect(DISTINCT {title: doc.title, path: doc.section_path}) AS docs,
           collect(DISTINCT test.qualified_name) AS tests,
           collect(DISTINCT dec.name) AS decorators
"""

IMPACT_ANALYSIS = """
    MATCH (target {qualified_name: $qname})
    OPTIONAL MATCH (caller)-[:CALLS]->(target)
    OPTIONAL MATCH (child:Class)-[:INHERITS]->(target)
    OPTIONAL MATCH (override:Method)-[:OVERRIDES]->(target)
    OPTIONAL MATCH (test:Test)-[:TESTS]->(target)
    RETURN target.name AS name, target.qualified_name AS qualified_name,
           collect(DISTINCT caller.qualified_name) AS callers,
           collect(DISTINCT child.qualified_name) AS subclasses,
           collect(DISTINCT override.qualified_name) AS overrides,
           collect(DISTINCT test.qualified_name) AS tests
"""

_SHOW_ARCHITECTURE = """
    MATCH (pkg:Package) {where}
    WITH pkg
    MATCH (pkg)-[:CONTAINS_MODULE]->(mod:Module)
    OPTIONAL MATCH (mod)-[:CONTAINS_CLASS]->(cls:Class)
    WITH pkg, mod, collect(DISTINCT cls.name) AS classes
    RETURN pkg.qualified_name AS package,
           mod.name AS module,
           classes,
           size(classes) AS class_count
    ORDER BY pkg.qualified_name, mod.name
    LIMIT 200
"""

_CODE_SEARCH_FULLTEXT = """
    CALL db.index.fulltext.queryNodes('code_names', $query)
    YIELD node, score
    RETURN node.name AS name,
           node.qualified_name AS qualified_name,
           labels(node)[0] AS type,
           node.docstring AS docstring,
           score
    ORDER BY score DESC LIMIT $limit
"""

_CODE_SEARCH_VECTOR = """
    CALL db.index.vector.queryNodes($index_name, $limit, $embedding)
    YIELD node, score
    RETURN node.name AS name,
           node.qualified_name AS qualified_name,
           labels(node)[0] AS type,
           node.docstring AS docstring,
           score
"""

_FIND_DOCS_FOR_CODE = """
    MATCH (code {qualified_name: $qname})-[:DOCUMENTED_BY]->(doc:DocSection)
    RETURN doc.title AS title,
           doc.file_path AS file_path,
           doc.section_path AS section_path,
           doc.text_preview AS preview
    ORDER BY doc.title
"""

_VECTOR_INDEX_NAMES = ("class_embedding", "method_embedding", "function_embedding")


class NullCodeGraphClient:
    """No-op fallback when Neo4j is disabled."""
    enabled = False

    async def init(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def ping(self) -> Dict[str, Any]:
        return {"status": "disabled", "reason": "APP_GRAPH_ENABLED is false"}

    async def show_architecture(self, package_filter: str = "", depth: int = 3) -> Dict[str, Any]:
        return {"packages": [], "total_modules": 0}

    async def class_footprint(self, qualified_name: str) -> Dict[str, Any]:
        return {"footprint": []}

    async def trace_call_chain(self, qualified_name: str, max_depth: int = 5) -> Dict[str, Any]:
        return {"chains": [], "count": 0}

    async def find_references(self, qualified_name: str) -> Dict[str, Any]:
        return {"references": {}}

    async def code_search(self, search_query: str, search_type: str = "hybrid", limit: int = 10) -> Dict[str, Any]:
        return {"results": [], "count": 0}

    async def impact_analysis(self, qualified_name: str) -> Dict[str, Any]:
        return {"impact": {}}

    async def find_siblings(self, class_name: str) -> Dict[str, Any]:
        return {"siblings": []}

    async def show_contract(self, qualified_name: str) -> Dict[str, Any]:
        return {"contract": []}

    async def find_entry_points(self) -> Dict[str, Any]:
        return {"entry_points": [], "count": 0}

    async def find_docs_for_code(self, qualified_name: str) -> Dict[str, Any]:
        return {"docs": [], "count": 0}


class CodeGraphClient:
    """
    Async Neo4j client for the code knowledge graph (coding-core schema).
    Uses the same Cypher queries as the coding-core MCP server but via async driver.
    Supports fulltext, vector, and hybrid search modes.
    """
    enabled = True

    def __init__(self, settings=None):
        if settings is None:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings
            settings = get_settings()
        self._settings = settings
        self._driver = None
        self._db_name = getattr(settings, "NEO4J_CODE_DB", "neo4j")
        self._embedding_model_name = getattr(settings, "CODE_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    async def init(self) -> None:
        if AsyncGraphDatabase is None:
            raise ImportError("neo4j package not installed")
        self._driver = AsyncGraphDatabase.driver(
            self._settings.NEO4J_URI,
            auth=(self._settings.NEO4J_USER, self._settings.NEO4J_PASSWORD),
            max_connection_lifetime=300,
            connection_acquisition_timeout=10,
        )
        logger.info("CodeGraphClient connected to %s (db=%s)", self._settings.NEO4J_URI, self._db_name)

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None

    def _session(self):
        assert self._driver is not None, "CodeGraphClient not initialized — call init() first"
        return self._driver.session(database=self._db_name)

    async def _run(self, cypher: str, **params) -> List[Dict[str, Any]]:
        async with self._session() as session:
            result = await session.run(cypher, parameters=params)
            return [record.data() async for record in result]

    async def ping(self) -> Dict[str, Any]:
        try:
            records = await self._run("RETURN 1 AS ok")
            return {"status": "ok", "database": self._db_name}
        except Exception as e:
            return {"status": "down", "reason": str(e)}

    async def show_architecture(self, package_filter: str = "", depth: int = 3) -> Dict[str, Any]:
        where = "WHERE pkg.qualified_name STARTS WITH $filter" if package_filter else ""
        query = _SHOW_ARCHITECTURE.replace("{where}", where)
        records = await self._run(query, filter=package_filter)
        return {"packages": records, "total_modules": len(records)}

    async def class_footprint(self, qualified_name: str) -> Dict[str, Any]:
        records = await self._run(CLASS_FOOTPRINT, qname=qualified_name)
        if not records:
            return {"error": f"Class not found: {qualified_name}"}
        return {"footprint": records}

    async def trace_call_chain(self, qualified_name: str, max_depth: int = 5) -> Dict[str, Any]:
        records = await self._run(CALL_CHAIN_TRACE, qname=qualified_name, depth=max_depth)
        return {"chains": records, "count": len(records)}

    async def find_references(self, qualified_name: str) -> Dict[str, Any]:
        records = await self._run(FIND_REFERENCES, qname=qualified_name)
        if not records:
            return {"error": f"Symbol not found: {qualified_name}"}
        return {"references": records[0]}

    async def code_search(self, search_query: str, search_type: str = "hybrid", limit: int = 10) -> Dict[str, Any]:
        """
        Search the code knowledge graph.

        search_type:
            "fulltext" — BM25 fulltext index on code_names
            "vector"   — semantic vector search using sentence-transformers embeddings
            "hybrid"   — combines fulltext + vector, deduplicates by qualified_name
        """
        results: List[Dict[str, Any]] = []

        if search_type in ("fulltext", "hybrid"):
            records = await self._run(_CODE_SEARCH_FULLTEXT, query=search_query, limit=limit)
            results.extend([{**r, "source": "fulltext"} for r in records])

        if search_type in ("vector", "hybrid"):
            embeddings = _embed([search_query], model_name=self._embedding_model_name)
            if embeddings:
                embedding = embeddings[0]
                for index_name in _VECTOR_INDEX_NAMES:
                    try:
                        records = await self._run(
                            _CODE_SEARCH_VECTOR,
                            index_name=index_name,
                            limit=limit,
                            embedding=embedding,
                        )
                        results.extend([{**r, "source": "vector"} for r in records])
                    except Exception:
                        pass  # Index may not exist yet

        # Deduplicate by qualified_name, keep highest score
        seen: Dict[str, Dict[str, Any]] = {}
        for r in results:
            qn = r.get("qualified_name", "")
            if qn not in seen or r.get("score", 0) > seen[qn].get("score", 0):
                seen[qn] = r
        deduped = sorted(seen.values(), key=lambda x: x.get("score", 0), reverse=True)

        return {"results": deduped[:limit], "count": len(deduped)}

    async def impact_analysis(self, qualified_name: str) -> Dict[str, Any]:
        records = await self._run(IMPACT_ANALYSIS, qname=qualified_name)
        if not records:
            return {"error": f"Symbol not found: {qualified_name}"}
        return {"impact": records[0]}

    async def find_siblings(self, class_name: str) -> Dict[str, Any]:
        records = await self._run(FIND_SIBLINGS, name=class_name)
        return {"siblings": records}

    async def show_contract(self, qualified_name: str) -> Dict[str, Any]:
        records = await self._run(SHOW_CONTRACT, qname=qualified_name)
        return {"contract": records}

    async def find_entry_points(self) -> Dict[str, Any]:
        records = await self._run(FIND_ENTRY_POINTS)
        return {"entry_points": records, "count": len(records)}

    async def find_docs_for_code(self, qualified_name: str) -> Dict[str, Any]:
        records = await self._run(_FIND_DOCS_FOR_CODE, qname=qualified_name)
        return {"docs": records, "count": len(records)}


def create_code_graph_client(settings=None) -> CodeGraphClient | NullCodeGraphClient:
    """Factory: returns NullCodeGraphClient if graph is disabled."""
    if settings is None:
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        settings = get_settings()
    if not getattr(settings, "APP_GRAPH_ENABLED", False):
        logger.info("Code graph disabled (APP_GRAPH_ENABLED=false)")
        return NullCodeGraphClient()
    if AsyncGraphDatabase is None:
        logger.warning("neo4j package not installed, using NullCodeGraphClient")
        return NullCodeGraphClient()
    return CodeGraphClient(settings)
