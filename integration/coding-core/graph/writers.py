"""
Batch Neo4j write helpers using UNWIND for performance.
All writes are idempotent (MERGE, not CREATE).
"""

import logging

log = logging.getLogger("coding-core-mcp")

BATCH_SIZE = 500


def _run_batched(session, cypher: str, items: list[dict], param_name: str = "batch"):
    """Execute a Cypher statement in batches using UNWIND."""
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        session.run(cypher, **{param_name: batch})


# ---------------------------------------------------------------------------
# Node writers
# ---------------------------------------------------------------------------

def write_packages(session, packages: list[dict]):
    _run_batched(session, """
        UNWIND $batch AS pkg
        MERGE (p:Package {qualified_name: pkg.qualified_name})
        SET p.name = pkg.name, p.path = pkg.path
    """, packages)


def write_modules(session, modules: list[dict]):
    _run_batched(session, """
        UNWIND $batch AS mod
        MERGE (m:Module {qualified_name: mod.qualified_name})
        SET m.name = mod.name, m.file_path = mod.file_path,
            m.language = mod.language, m.line_count = mod.line_count
    """, modules)


def write_classes(session, classes: list[dict]):
    _run_batched(session, """
        UNWIND $batch AS cls
        MERGE (c:Class {qualified_name: cls.qualified_name})
        SET c.name = cls.name, c.module = cls.module,
            c.file_path = cls.file_path, c.docstring = cls.docstring,
            c.line_start = cls.line_start, c.line_end = cls.line_end,
            c.visibility = cls.visibility,
            c.is_abstract = cls.is_abstract,
            c.is_dataclass = cls.is_dataclass,
            c.is_protocol = cls.is_protocol,
            c.language = cls.language
    """, classes)


def write_interfaces(session, interfaces: list[dict]):
    """Add :Interface label to classes that define contracts."""
    _run_batched(session, """
        UNWIND $batch AS iface
        MATCH (c:Class {qualified_name: iface.qualified_name})
        SET c:Interface, c.is_protocol = iface.is_protocol, c.is_abc = iface.is_abc
    """, interfaces)


def write_methods(session, methods: list[dict]):
    _run_batched(session, """
        UNWIND $batch AS meth
        MERGE (m:Method {qualified_name: meth.qualified_name})
        SET m.name = meth.name, m.class_name = meth.class_name,
            m.signature = meth.signature, m.return_type = meth.return_type,
            m.visibility = meth.visibility,
            m.is_abstract = meth.is_abstract,
            m.is_static = meth.is_static,
            m.is_async = meth.is_async,
            m.is_property = meth.is_property,
            m.line_start = meth.line_start, m.line_end = meth.line_end,
            m.docstring = meth.docstring
    """, methods)


def write_functions(session, functions: list[dict]):
    _run_batched(session, """
        UNWIND $batch AS func
        MERGE (f:Function {qualified_name: func.qualified_name})
        SET f.name = func.name, f.module = func.module,
            f.file_path = func.file_path,
            f.signature = func.signature, f.return_type = func.return_type,
            f.is_async = func.is_async,
            f.line_start = func.line_start, f.line_end = func.line_end,
            f.docstring = func.docstring
    """, functions)


def write_properties(session, properties: list[dict]):
    _run_batched(session, """
        UNWIND $batch AS prop
        MERGE (p:Property {qualified_name: prop.qualified_name})
        SET p.name = prop.name, p.class_name = prop.class_name,
            p.type_annotation = prop.type_annotation,
            p.default_value = prop.default_value,
            p.visibility = prop.visibility
    """, properties)


def write_decorators(session, decorators: list[dict]):
    _run_batched(session, """
        UNWIND $batch AS dec
        MERGE (d:Decorator {name: dec.name})
        SET d.qualified_name = dec.qualified_name
    """, decorators)


def write_tests(session, tests: list[dict]):
    _run_batched(session, """
        UNWIND $batch AS t
        MERGE (test:Test {name: t.name, file_path: t.file_path})
        SET test.test_class = t.test_class, test.qualified_name = t.qualified_name
    """, tests)


def write_doc_sections(session, sections: list[dict]):
    _run_batched(session, """
        UNWIND $batch AS sec
        MERGE (d:DocSection {file_path: sec.file_path, section_path: sec.section_path})
        SET d.title = sec.title, d.text_preview = sec.text_preview
    """, sections)


def write_routes(session, routes: list[dict]):
    _run_batched(session, """
        UNWIND $batch AS rt
        MERGE (r:Route {path: rt.path, http_method: rt.http_method})
        SET r.handler_qualified_name = rt.handler_qualified_name,
            r.transport = rt.transport
    """, routes)


# ---------------------------------------------------------------------------
# Relationship writers
# ---------------------------------------------------------------------------

def write_containment(session, edges: list[dict]):
    """Write CONTAINS_* relationships. Each edge: {parent_qname, child_qname, rel_type}"""
    for rel_type in ["CONTAINS_PACKAGE", "CONTAINS_MODULE", "CONTAINS_CLASS",
                     "CONTAINS_FUNCTION", "CONTAINS_METHOD", "CONTAINS_PROPERTY"]:
        batch = [e for e in edges if e["rel_type"] == rel_type]
        if not batch:
            continue
        _run_batched(session, f"""
            UNWIND $batch AS edge
            MATCH (parent {{qualified_name: edge.parent_qname}})
            MATCH (child {{qualified_name: edge.child_qname}})
            MERGE (parent)-[:{rel_type}]->(child)
        """, batch)


def write_inherits(session, edges: list[dict]):
    """Each edge: {child_qname, parent_qname}"""
    _run_batched(session, """
        UNWIND $batch AS edge
        MATCH (child:Class {qualified_name: edge.child_qname})
        MATCH (parent:Class {qualified_name: edge.parent_qname})
        MERGE (child)-[:INHERITS]->(parent)
    """, edges)


def write_implements(session, edges: list[dict]):
    """Each edge: {class_qname, interface_qname}"""
    _run_batched(session, """
        UNWIND $batch AS edge
        MATCH (c:Class {qualified_name: edge.class_qname})
        MATCH (i:Class {qualified_name: edge.interface_qname})
        MERGE (c)-[:IMPLEMENTS]->(i)
    """, edges)


def write_calls(session, edges: list[dict]):
    """Each edge: {caller_qname, callee_qname}"""
    _run_batched(session, """
        UNWIND $batch AS edge
        MATCH (caller {qualified_name: edge.caller_qname})
        MATCH (callee {qualified_name: edge.callee_qname})
        MERGE (caller)-[:CALLS]->(callee)
    """, edges)


def write_references(session, edges: list[dict]):
    """Each edge: {source_qname, target_qname}"""
    _run_batched(session, """
        UNWIND $batch AS edge
        MATCH (source {qualified_name: edge.source_qname})
        MATCH (target {qualified_name: edge.target_qname})
        MERGE (source)-[:REFERENCES]->(target)
    """, edges)


def write_overrides(session, edges: list[dict]):
    """Each edge: {child_method_qname, parent_method_qname}"""
    _run_batched(session, """
        UNWIND $batch AS edge
        MATCH (child:Method {qualified_name: edge.child_method_qname})
        MATCH (parent:Method {qualified_name: edge.parent_method_qname})
        MERGE (child)-[:OVERRIDES]->(parent)
    """, edges)


def write_imports(session, edges: list[dict]):
    """Each edge: {source_module_qname, target_module_qname, symbol}"""
    _run_batched(session, """
        UNWIND $batch AS edge
        MATCH (src:Module {qualified_name: edge.source_module_qname})
        MATCH (tgt:Module {qualified_name: edge.target_module_qname})
        MERGE (src)-[:IMPORTS {symbol: edge.symbol}]->(tgt)
    """, edges)


def write_decorated_by(session, edges: list[dict]):
    """Each edge: {entity_qname, decorator_name}"""
    _run_batched(session, """
        UNWIND $batch AS edge
        MATCH (entity {qualified_name: edge.entity_qname})
        MATCH (dec:Decorator {name: edge.decorator_name})
        MERGE (entity)-[:DECORATED_BY]->(dec)
    """, edges)


def write_documented_by(session, edges: list[dict]):
    """Each edge: {code_qname, doc_file_path, doc_section_path, relevance, match_type}"""
    _run_batched(session, """
        UNWIND $batch AS edge
        MATCH (code {qualified_name: edge.code_qname})
        MATCH (doc:DocSection {file_path: edge.doc_file_path, section_path: edge.doc_section_path})
        MERGE (code)-[r:DOCUMENTED_BY]->(doc)
        SET r.relevance = edge.relevance, r.match_type = edge.match_type
    """, edges)


def write_handled_by(session, edges: list[dict]):
    """Each edge: {route_path, route_method, handler_qname}"""
    _run_batched(session, """
        UNWIND $batch AS edge
        MATCH (r:Route {path: edge.route_path, http_method: edge.route_method})
        MATCH (h {qualified_name: edge.handler_qname})
        MERGE (r)-[:HANDLED_BY]->(h)
    """, edges)


def write_tests_edges(session, edges: list[dict]):
    """Each edge: {test_name, test_file, target_qname}"""
    _run_batched(session, """
        UNWIND $batch AS edge
        MATCH (t:Test {name: edge.test_name, file_path: edge.test_file})
        MATCH (target {qualified_name: edge.target_qname})
        MERGE (t)-[:TESTS]->(target)
    """, edges)


def write_embeddings(session, items: list[dict]):
    """Update embeddings on existing nodes. Each item: {qualified_name, embedding}"""
    _run_batched(session, """
        UNWIND $batch AS item
        MATCH (n {qualified_name: item.qualified_name})
        SET n.embedding = item.embedding
    """, items)


def clear_graph(session):
    """Remove all nodes and relationships. Use with caution."""
    session.run("MATCH (n) DETACH DELETE n")
    log.info("[Coding-Core] Graph cleared")
