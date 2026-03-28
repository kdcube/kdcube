"""
Neo4j schema definitions for the code knowledge graph.
Constraints, indexes, vector indexes, and fulltext indexes.
"""

# Uniqueness constraints — qualified_name is the universal key
CONSTRAINTS = [
    "CREATE CONSTRAINT class_qname IF NOT EXISTS FOR (c:Class) REQUIRE c.qualified_name IS UNIQUE",
    "CREATE CONSTRAINT method_qname IF NOT EXISTS FOR (m:Method) REQUIRE m.qualified_name IS UNIQUE",
    "CREATE CONSTRAINT function_qname IF NOT EXISTS FOR (f:Function) REQUIRE f.qualified_name IS UNIQUE",
    "CREATE CONSTRAINT module_qname IF NOT EXISTS FOR (m:Module) REQUIRE m.qualified_name IS UNIQUE",
    "CREATE CONSTRAINT package_qname IF NOT EXISTS FOR (p:Package) REQUIRE p.qualified_name IS UNIQUE",
    "CREATE CONSTRAINT docsection_path IF NOT EXISTS FOR (d:DocSection) REQUIRE (d.file_path, d.section_path) IS UNIQUE",
]

# Performance indexes for frequent lookups
INDEXES = [
    "CREATE INDEX class_name IF NOT EXISTS FOR (c:Class) ON (c.name)",
    "CREATE INDEX method_name IF NOT EXISTS FOR (m:Method) ON (m.name)",
    "CREATE INDEX function_name IF NOT EXISTS FOR (f:Function) ON (f.name)",
    "CREATE INDEX module_filepath IF NOT EXISTS FOR (m:Module) ON (m.file_path)",
    "CREATE INDEX test_filepath IF NOT EXISTS FOR (t:Test) ON (t.file_path)",
    "CREATE INDEX route_path IF NOT EXISTS FOR (r:Route) ON (r.path)",
]

# Vector indexes for semantic search (dimensions set at runtime from config)
VECTOR_INDEXES = [
    """CREATE VECTOR INDEX class_embedding IF NOT EXISTS FOR (c:Class) ON (c.embedding)
       OPTIONS {indexConfig: {`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}""",
    """CREATE VECTOR INDEX method_embedding IF NOT EXISTS FOR (m:Method) ON (m.embedding)
       OPTIONS {indexConfig: {`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}""",
    """CREATE VECTOR INDEX function_embedding IF NOT EXISTS FOR (f:Function) ON (f.embedding)
       OPTIONS {indexConfig: {`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}""",
    """CREATE VECTOR INDEX docsection_embedding IF NOT EXISTS FOR (d:DocSection) ON (d.embedding)
       OPTIONS {indexConfig: {`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}""",
]

# Fulltext indexes for name/docstring search
FULLTEXT_INDEXES = [
    """CREATE FULLTEXT INDEX code_names IF NOT EXISTS
       FOR (n:Class|Method|Function|Property) ON EACH [n.name, n.qualified_name, n.docstring]""",
]
