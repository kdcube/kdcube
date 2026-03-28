"""
Coding-Core MCP Server
======================
Code knowledge graph pipeline for any codebase.
Extracts OOP structure via LSP, stores in Neo4j, exposes exploration tools.

Tools: ping, index_codebase, find_entry_points, show_architecture,
       trace_call_chain, find_references, find_siblings, show_contract,
       class_footprint, code_search, impact_analysis, find_docs_for_code
"""

import json
import logging
import os
import sys
import time as _time
from pathlib import Path

# Ensure the package root is on sys.path for graph.* imports
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

# Lazy imports to avoid slow startup
GraphDatabase = None
SentenceTransformer = None


def _import_neo4j():
    global GraphDatabase
    if GraphDatabase is None:
        log.info("[Coding-Core] Loading Neo4j driver...")
        from neo4j import GraphDatabase as _GD
        GraphDatabase = _GD
        log.info("[Coding-Core] Neo4j driver ready.")


def _import_embeddings():
    global SentenceTransformer
    if SentenceTransformer is None:
        import warnings
        warnings.filterwarnings('ignore')
        import logging as _logging
        _logging.getLogger('transformers').setLevel(_logging.ERROR)
        _logging.getLogger('sentence_transformers').setLevel(_logging.ERROR)
        log.info("[Coding-Core] Loading sentence-transformers...")
        from sentence_transformers import SentenceTransformer as _ST
        SentenceTransformer = _ST
        log.info("[Coding-Core] sentence-transformers loaded.")


# ---------------------------------------------------------------------------
# Config: ENV vars > CLI args > config.json
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.json"

try:
    with open(CONFIG_PATH) as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    CONFIG = {}

import argparse
_parser = argparse.ArgumentParser()
_parser.add_argument("--db-uri", default=None)
_parser.add_argument("--db-user", default=None)
_parser.add_argument("--db-password", default=None)
_parser.add_argument("--db-name", default=None)
_parser.add_argument("--project-root", default=None)
_args, _ = _parser.parse_known_args()

DB = CONFIG.get("database", {})
DB.setdefault("uri", "bolt://127.0.0.1:7687")
DB.setdefault("user", "neo4j")
DB.setdefault("password", "")
DB.setdefault("name", "coding-core")

for key, env_var in [("uri", "DB_URI"), ("user", "DB_USER"),
                     ("password", "DB_PASSWORD"), ("name", "DB_NAME")]:
    env_val = os.getenv(env_var)
    if env_val:
        DB[key] = env_val
    else:
        cli_val = getattr(_args, f"db_{key}", None)
        if cli_val:
            DB[key] = cli_val

EMB_CFG = CONFIG.get("embedding", {"model": "all-MiniLM-L6-v2", "dimensions": 384})

PROJECT_ROOT = (os.getenv("PROJECT_ROOT")
                or _args.project_root
                or CONFIG.get("target", {}).get("project_root", ""))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coding-core-mcp")

# Suppress noisy loggers in stdio mode
for logger_name in ["neo4j", "neo4j.bolt", "neo4j.io", "tensorflow", "transformers"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'


# ---------------------------------------------------------------------------
# Globals (initialized lazily)
# ---------------------------------------------------------------------------

_driver = None
_model = None


def get_driver():
    global _driver
    if _driver is None:
        _import_neo4j()
        _driver = GraphDatabase.driver(
            DB["uri"],
            auth=(DB["user"], DB["password"]),
            connection_timeout=5.0,
            max_connection_lifetime=3600,
            connection_acquisition_timeout=10.0,
        )
        log.info("[Coding-Core] Neo4j driver initialized")
    return _driver


def get_model():
    global _model
    if _model is None:
        _import_embeddings()
        log.info("[Coding-Core] Loading embedding model '%s'...", EMB_CFG["model"])
        t0 = _time.time()
        _model = SentenceTransformer(EMB_CFG["model"])
        log.info("[Coding-Core] Embedding model ready (%.1fs)", _time.time() - t0)
    return _model


def _session():
    return get_driver().session(database=DB["name"])


def _embed(texts: list[str]) -> list[list[float]]:
    model = get_model()
    embeddings = model.encode(texts, show_progress_bar=False)
    return [e.tolist() for e in embeddings]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _ensure_schema():
    """Create constraints, indexes, and vector indexes for code graph."""
    from graph.schema import CONSTRAINTS, INDEXES, VECTOR_INDEXES, FULLTEXT_INDEXES

    with _session() as s:
        for cypher in CONSTRAINTS:
            try:
                s.run(cypher)
            except Exception:
                pass

        for cypher in INDEXES:
            try:
                s.run(cypher)
            except Exception:
                pass

        for cypher in VECTOR_INDEXES:
            try:
                s.run(cypher, dims=EMB_CFG["dimensions"])
            except Exception:
                pass

        for cypher in FULLTEXT_INDEXES:
            try:
                s.run(cypher)
            except Exception:
                pass

    log.info("[Coding-Core] Schema ensured")


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "coding-core",
    instructions=(
        "Code knowledge graph for codebase exploration. "
        "Call ping first to check Neo4j status. "
        "Use index_codebase to populate the graph from source code. "
        "Use class_footprint, trace_call_chain, find_references, "
        "show_architecture, code_search for exploration."
    ),
)


@mcp.tool()
def ping() -> str:
    """
    Check if Neo4j database is reachable and Coding-Core is ready.

    **WHEN TO USE:** Call this FIRST at session start, before any other tool.

    **RETURNS:**
    - status: "ok" -> Neo4j up, embedding model loaded, all tools ready
    - status: "warming" -> Neo4j up, embedding model still loading
    - status: "down" -> Neo4j unavailable
    """
    try:
        with _session() as s:
            result = s.run("RETURN 1 AS n").single()
            if not (result and result["n"] == 1):
                return json.dumps({"status": "down", "reason": "Query failed"})

        model_ready = _model is not None
        if model_ready:
            return json.dumps({
                "status": "ok",
                "database": DB["name"],
                "uri": DB["uri"],
                "embedding_model": "ready",
            })
        return json.dumps({
            "status": "warming",
            "database": DB["name"],
            "uri": DB["uri"],
            "embedding_model": "loading",
            "note": "code_search (vector/hybrid) may be slow until model loads.",
        })

    except Exception as e:
        error_msg = str(e)
        if "refused" in error_msg.lower():
            error_msg = "Connection refused - is Neo4j running?"
        elif "timeout" in error_msg.lower():
            error_msg = "Connection timeout - check Neo4j address"
        elif "authentication" in error_msg.lower():
            error_msg = "Authentication failed - check credentials"
        log.error("Neo4j ping failed: %s", error_msg)
        return json.dumps({"status": "down", "reason": error_msg})


@mcp.tool()
def show_architecture(package_filter: str = "", depth: int = 3) -> str:
    """
    Show the codebase structure: packages -> modules -> classes.

    **WHEN TO USE:** To understand the major pieces of a codebase (Step 2 of exploration).

    **ARGS:**
    - package_filter: Only show packages matching this prefix (e.g., "myapp.auth")
    - depth: How many levels deep to show (default: 3)

    **RETURNS:** Package/module/class tree with counts.
    """
    where = "WHERE pkg.qualified_name STARTS WITH $filter" if package_filter else ""
    query = f"""
        MATCH (pkg:Package)
        {where}
        WITH pkg
        MATCH (pkg)-[:CONTAINS_MODULE]->(mod:Module)
        OPTIONAL MATCH (mod)-[:CONTAINS_CLASS]->(cls:Class)
        WITH pkg, mod, collect(DISTINCT cls.name) AS classes
        RETURN pkg.qualified_name AS package,
               mod.name AS module,
               classes,
               size(classes) AS class_count
        ORDER BY pkg.qualified_name, mod.name
        LIMIT 100
    """
    try:
        with _session() as s:
            records = s.run(query, filter=package_filter).data()
        return json.dumps({"packages": records, "total_modules": len(records)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def class_footprint(qualified_name: str) -> str:
    """
    Get the complete context for a class: inheritance, interfaces, methods,
    callers, callees, documentation, tests, and decorators.

    **WHEN TO USE:** To fully understand a class before working with it.
    This is the most powerful exploration tool — combines all 7 inference steps.

    **ARGS:**
    - qualified_name: Fully qualified class name (e.g., "myapp.auth.AuthManager")

    **RETURNS:** Complete class footprint with all relationships.
    """
    from graph.queries import CLASS_FOOTPRINT
    try:
        with _session() as s:
            result = s.run(CLASS_FOOTPRINT, qname=qualified_name).data()
        if not result:
            return json.dumps({"error": f"Class not found: {qualified_name}"})
        return json.dumps({"footprint": result})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def trace_call_chain(qualified_name: str, max_depth: int = 5) -> str:
    """
    Follow a method's outgoing calls to a specified depth.

    **WHEN TO USE:** To understand what happens when a method is invoked (Step 3).

    **ARGS:**
    - qualified_name: Fully qualified method name
    - max_depth: How deep to trace (default: 5)

    **RETURNS:** Call chains with depth.
    """
    from graph.queries import CALL_CHAIN_TRACE
    try:
        with _session() as s:
            records = s.run(CALL_CHAIN_TRACE, qname=qualified_name,
                            depth=max_depth).data()
        return json.dumps({"chains": records, "count": len(records)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def find_references(qualified_name: str) -> str:
    """
    Find all references to a symbol, classified by type.

    **WHEN TO USE:** To understand who uses a class/method (Step 4).

    **ARGS:**
    - qualified_name: Fully qualified name of the symbol

    **RETURNS:** Classified references: callers, subclasses, implementors, overrides, tests.
    """
    from graph.queries import FIND_REFERENCES
    try:
        with _session() as s:
            result = s.run(FIND_REFERENCES, qname=qualified_name).data()
        if not result:
            return json.dumps({"error": f"Symbol not found: {qualified_name}"})
        return json.dumps({"references": result[0]})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def find_siblings(class_name: str) -> str:
    """
    Find all classes sharing a common parent or interface.

    **WHEN TO USE:** To find similar patterns or alternative implementations (Step 5).

    **ARGS:**
    - class_name: Name or qualified_name of the class

    **RETURNS:** Sibling classes grouped by shared parent.
    """
    from graph.queries import FIND_SIBLINGS
    try:
        with _session() as s:
            records = s.run(FIND_SIBLINGS, name=class_name).data()
        return json.dumps({"siblings": records})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def show_contract(qualified_name: str) -> str:
    """
    Show the interface contract for a class: abstract methods that must be implemented.

    **WHEN TO USE:** To understand what's required to extend a class (Step 6).

    **ARGS:**
    - qualified_name: Fully qualified class name

    **RETURNS:** Interface/protocol methods, abstract methods, required implementations.
    """
    from graph.queries import SHOW_CONTRACT
    try:
        with _session() as s:
            result = s.run(SHOW_CONTRACT, qname=qualified_name).data()
        return json.dumps({"contract": result})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def find_entry_points() -> str:
    """
    Find all entry points: HTTP routes, CLI mains, event handlers.

    **WHEN TO USE:** To understand where the system starts (Step 1 of exploration).

    **RETURNS:** Routes with their handler methods.
    """
    from graph.queries import FIND_ENTRY_POINTS
    try:
        with _session() as s:
            records = s.run(FIND_ENTRY_POINTS).data()
        return json.dumps({"entry_points": records, "count": len(records)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def code_search(query: str, search_type: str = "hybrid", limit: int = 10) -> str:
    """
    Search the code graph using vector, fulltext, or hybrid search.

    **WHEN TO USE:** For conceptual questions like "how does authentication work?"

    **ARGS:**
    - query: Natural language or symbol name
    - search_type: "vector" | "fulltext" | "hybrid" (default: "hybrid")
    - limit: Max results (default: 10)

    **RETURNS:** Matching code entities with scores.
    """
    try:
        results = []

        if search_type in ("fulltext", "hybrid"):
            with _session() as s:
                ft_records = s.run("""
                    CALL db.index.fulltext.queryNodes('code_names', $query)
                    YIELD node, score
                    RETURN node.name AS name,
                           node.qualified_name AS qualified_name,
                           labels(node)[0] AS type,
                           node.docstring AS docstring,
                           score
                    ORDER BY score DESC LIMIT $limit
                """, query=query, limit=limit).data()
                results.extend([{**r, "source": "fulltext"} for r in ft_records])

        if search_type in ("vector", "hybrid"):
            embedding = _embed([query])[0]
            with _session() as s:
                for index_name in ["class_embedding", "method_embedding",
                                   "function_embedding"]:
                    try:
                        vec_records = s.run(f"""
                            CALL db.index.vector.queryNodes('{index_name}', $limit, $embedding)
                            YIELD node, score
                            RETURN node.name AS name,
                                   node.qualified_name AS qualified_name,
                                   labels(node)[0] AS type,
                                   node.docstring AS docstring,
                                   score
                        """, limit=limit, embedding=embedding).data()
                        results.extend([{**r, "source": "vector"} for r in vec_records])
                    except Exception:
                        pass  # Index may not exist yet

        # Deduplicate by qualified_name, keep highest score
        seen = {}
        for r in results:
            qn = r.get("qualified_name", "")
            if qn not in seen or r.get("score", 0) > seen[qn].get("score", 0):
                seen[qn] = r
        deduped = sorted(seen.values(), key=lambda x: x.get("score", 0), reverse=True)

        return json.dumps({"results": deduped[:limit], "count": len(deduped)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def impact_analysis(qualified_name: str) -> str:
    """
    Analyze the impact of changing a symbol: who calls it, who inherits it,
    what tests cover it.

    **WHEN TO USE:** Before renaming, deleting, or modifying a public symbol.

    **ARGS:**
    - qualified_name: Fully qualified symbol name

    **RETURNS:** Direct callers, subclasses, overrides, tests.
    """
    from graph.queries import IMPACT_ANALYSIS
    try:
        with _session() as s:
            result = s.run(IMPACT_ANALYSIS, qname=qualified_name).data()
        if not result:
            return json.dumps({"error": f"Symbol not found: {qualified_name}"})
        return json.dumps({"impact": result[0]})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def find_docs_for_code(qualified_name: str) -> str:
    """
    Find documentation sections linked to a code entity.

    **WHEN TO USE:** When explaining code — include official docs for the "why."

    **ARGS:**
    - qualified_name: Fully qualified class or method name

    **RETURNS:** Linked documentation sections with relevance scores.
    """
    query = """
        MATCH (code {qualified_name: $qname})-[:DOCUMENTED_BY]->(doc:DocSection)
        RETURN doc.title AS title,
               doc.file_path AS file_path,
               doc.section_path AS section_path,
               doc.text_preview AS preview
        ORDER BY doc.title
    """
    try:
        with _session() as s:
            records = s.run(query, qname=qualified_name).data()
        return json.dumps({"docs": records, "count": len(records)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Indexing tool
# ---------------------------------------------------------------------------

@mcp.tool()
def index_codebase(project_path: str = "", force_reindex: bool = False,
                   skip_embeddings: bool = True) -> str:
    """
    Extract all code entities from the target codebase and populate the Neo4j graph.

    **WHEN TO USE:** First time setup, or after significant code changes.

    **ARGS:**
    - project_path: Path to project root (default: from config)
    - force_reindex: Clear graph and re-extract everything (default: false)
    - skip_embeddings: Skip embedding generation for faster indexing (default: true)

    **RETURNS:** Extraction stats (classes, methods, functions, relationships).
    """
    import time as _t
    from extraction.python_extractor import extract_python_project
    from extraction.doc_linker import extract_doc_sections, link_docs_to_code
    from extraction.test_linker import extract_tests, link_tests_to_code
    from graph.writers import (
        write_packages, write_modules, write_classes, write_interfaces,
        write_methods, write_functions, write_properties, write_decorators,
        write_tests, write_doc_sections, write_containment, write_inherits,
        write_calls, write_imports, write_decorated_by, write_documented_by,
        write_tests_edges, write_embeddings, clear_graph,
    )

    t0 = _t.time()

    root = project_path or PROJECT_ROOT
    if not root:
        return json.dumps({"error": "No project_path provided and PROJECT_ROOT not configured"})

    root = str(Path(root).resolve())
    target_cfg = CONFIG.get("target", {})
    source_roots = target_cfg.get("source_roots", ["src"])
    docs_root_rel = target_cfg.get("docs_root", "docs")
    test_patterns = target_cfg.get("test_patterns", ["test_*.py"])

    _ensure_schema()

    if force_reindex:
        with _session() as s:
            clear_graph(s)

    # Phase 1: Extract Python code
    log.info("[Index] Starting Python extraction from %s", root)
    data = extract_python_project(root, source_roots)

    # Phase 2: Write nodes
    log.info("[Index] Writing %d packages, %d modules, %d classes, %d methods, %d functions",
             len(data["packages"]), len(data["modules"]), len(data["classes"]),
             len(data["methods"]), len(data["functions"]))

    with _session() as s:
        write_packages(s, data["packages"])
        write_modules(s, data["modules"])
        write_classes(s, data["classes"])
        write_methods(s, data["methods"])
        write_functions(s, data["functions"])
        write_properties(s, data["properties"])
        write_decorators(s, data["decorators"])

        # Mark interfaces (Protocol/ABC classes)
        interfaces = [
            {"qualified_name": c["qualified_name"],
             "is_protocol": c["is_protocol"],
             "is_abc": c["is_abstract"]}
            for c in data["classes"]
            if c["is_protocol"] or c["is_abstract"]
        ]
        if interfaces:
            write_interfaces(s, interfaces)

    # Phase 3: Write relationships
    log.info("[Index] Writing relationships: %d containment, %d inherits, %d imports",
             len(data["containment"]), len(data["inherits"]), len(data["imports"]))

    with _session() as s:
        write_containment(s, data["containment"])
        write_inherits(s, data["inherits"])
        write_imports(s, data["imports"])
        write_decorated_by(s, data["decorated_by"])

    # Phase 4: Documentation linking
    doc_edges = []
    test_edges = []
    docs_root = str(Path(root) / docs_root_rel)
    doc_sections = extract_doc_sections(docs_root, root)
    if doc_sections:
        with _session() as s:
            write_doc_sections(s, doc_sections)

        doc_edges = link_docs_to_code(doc_sections, data["classes"])
        if doc_edges:
            with _session() as s:
                write_documented_by(s, doc_edges)

    # Phase 5: Test linking
    tests = extract_tests(root, source_roots, test_patterns)
    if tests:
        with _session() as s:
            write_tests(s, tests)

        test_edges = link_tests_to_code(tests, data["classes"], root)
        if test_edges:
            with _session() as s:
                write_tests_edges(s, test_edges)

    # Phase 6: Generate embeddings (optional — slow for large codebases)
    embed_items = []
    if not skip_embeddings:
        log.info("[Index] Generating embeddings...")
        for cls in data["classes"]:
            text = f"{cls['name']} {cls.get('docstring', '')}"
            embed_items.append({"qualified_name": cls["qualified_name"], "text": text})
        for meth in data["methods"]:
            text = f"{meth.get('class_name', '')}.{meth['name']} {meth.get('signature', '')} {meth.get('docstring', '')}"
            embed_items.append({"qualified_name": meth["qualified_name"], "text": text})
        for func in data["functions"]:
            text = f"{func['name']} {func.get('signature', '')} {func.get('docstring', '')}"
            embed_items.append({"qualified_name": func["qualified_name"], "text": text})

        if embed_items:
            texts = [item["text"] for item in embed_items]
            embeddings = _embed(texts)
            embed_data = [
                {"qualified_name": item["qualified_name"], "embedding": emb}
                for item, emb in zip(embed_items, embeddings)
            ]
            with _session() as s:
                write_embeddings(s, embed_data)
    else:
        log.info("[Index] Skipping embeddings (skip_embeddings=True)")

    elapsed = _t.time() - t0
    doc_edge_count = len(doc_edges) if doc_sections and doc_edges else 0
    test_edge_count = len(test_edges) if tests and test_edges else 0
    stats = data["stats"]
    stats["doc_sections"] = len(doc_sections)
    stats["doc_edges"] = doc_edge_count
    stats["tests"] = len(tests)
    stats["test_edges"] = test_edge_count
    stats["embeddings"] = len(embed_items)
    stats["elapsed_seconds"] = round(elapsed, 1)

    log.info("[Index] Indexing complete in %.1fs: %s", elapsed, stats)
    return json.dumps({"status": "ok", "stats": stats})


@mcp.tool()
def index_calls() -> str:
    """
    Extract CALLS edges using Pyright LSP (Phase 2).
    Requires the graph to be populated first via index_codebase.

    **WHEN TO USE:** After index_codebase, to add call graph relationships.
    This starts Pyright, queries call hierarchy for each method/function,
    and writes CALLS edges to Neo4j.

    **RETURNS:** Number of CALLS edges created and stats.
    """
    import time as _t
    from extraction.lsp_extractor import extract_calls_via_lsp
    from graph.writers import write_calls

    t0 = _t.time()

    root = PROJECT_ROOT
    if not root:
        return json.dumps({"error": "PROJECT_ROOT not configured"})

    root = str(Path(root).resolve())
    target_cfg = CONFIG.get("target", {})
    source_roots = target_cfg.get("source_roots", ["src"])
    lsp_cfg = CONFIG.get("lsp", {}).get("servers", {}).get("python", {
        "command": "pyright-langserver", "args": ["--stdio"]
    })

    # Read existing methods and functions from Neo4j
    log.info("[IndexCalls] Reading symbols from graph...")
    methods = []
    functions = []

    with _session() as s:
        for rec in s.run("""
            MATCH (c:Class)-[:CONTAINS_METHOD]->(m:Method)
            WHERE c.file_path IS NOT NULL AND m.line_start IS NOT NULL
            RETURN m.qualified_name AS qualified_name,
                   m.name AS name,
                   c.file_path AS file_path,
                   m.line_start AS line_start
        """).data():
            methods.append(rec)

        for rec in s.run("""
            MATCH (f:Function)
            WHERE f.file_path IS NOT NULL AND f.line_start IS NOT NULL
            RETURN f.qualified_name AS qualified_name,
                   f.name AS name,
                   f.file_path AS file_path,
                   f.line_start AS line_start
        """).data():
            functions.append(rec)

    log.info("[IndexCalls] Found %d methods + %d functions in graph",
             len(methods), len(functions))

    # Extract calls via LSP
    result = extract_calls_via_lsp(
        project_root=root,
        source_roots=source_roots,
        methods=methods,
        functions=functions,
        lsp_config=lsp_cfg,
    )

    # Write CALLS edges
    calls = result.get("calls", [])
    if calls:
        log.info("[IndexCalls] Writing %d CALLS edges to Neo4j", len(calls))
        with _session() as s:
            write_calls(s, calls)

    elapsed = _t.time() - t0
    stats = result.get("stats", {})
    stats["calls_written"] = len(calls)
    stats["elapsed_seconds"] = round(elapsed, 1)

    log.info("[IndexCalls] Complete in %.1fs: %s", elapsed, stats)
    return json.dumps({"status": "ok", "stats": stats})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Pre-initialize Neo4j driver (fast — just connection, no model loading)
    try:
        get_driver()
        _ensure_schema()
    except Exception as e:
        log.warning("[Coding-Core] Could not pre-initialize Neo4j: %s", e)

    # Warm embedding model in background — DO NOT block, start MCP immediately
    import threading

    def warmup_model():
        try:
            get_model()
            log.info("[Coding-Core] Embedding model ready")
        except Exception as e:
            log.warning("[Coding-Core] Model warmup failed: %s", e)

    threading.Thread(target=warmup_model, daemon=True).start()

    mcp.run(transport="stdio")
