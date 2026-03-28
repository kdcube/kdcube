# Coding-Core Integration

Drop this `coding-core/` folder into any project to get a Neo4j code knowledge graph with MCP tools for Claude Code.

## Prerequisites

### 1. Neo4j Desktop

Download and install [Neo4j Desktop](https://neo4j.com/download/).

1. Create a new **Project**
2. Click **Add** → **Local DBMS**
3. Set a password (remember it for setup)
4. Start the DBMS
5. Note the bolt port (default `7687` — change in Settings if you have another instance running: uncomment `server.bolt.listen_address=:7690`)
6. Create a database for your project: open Neo4j Browser → run `CREATE DATABASE myproject`

### 2. Python + Pyright

```bash
# Python 3.10+ required
python --version

# Pyright for call graph extraction (optional but recommended)
npm install -g pyright
```

## Quick Start

```bash
# 1. Copy this folder to your project root
cp -r coding-core/ /path/to/your-project/

# 2. Install dependencies
cd /path/to/your-project
pip install -r coding-core/requirements.txt

# 3. Run setup (auto-detects project structure)
python coding-core/setup.py --db-uri bolt://127.0.0.1:7687 --db-password yourpass

# 4. Restart Claude Code in this project
# 5. Ask Claude: "Ping Neo4j, then run index_codebase"
```

Setup will auto-detect source roots, docs, tests and generate all config files.

## What Setup Generates

| File | Purpose |
|------|---------|
| `coding-core/config.json` | Neo4j credentials + project paths |
| `.mcp.json` | Registers MCP server with Claude Code |
| `.claude/settings.local.json` | Auto-allows all coding-core MCP tools |
| `.claude/rules/coding-core-workflow.md` | Tells Claude when/how to use graph tools |
| `.gitignore` update | Excludes files with credentials |

## Usage

After indexing, ask Claude Code questions like:

| Question | Tool Claude Uses |
|----------|-----------------|
| "What are the main packages?" | `show_architecture` |
| "Explain the AuthManager class" | `class_footprint` + `find_docs_for_code` |
| "What calls authenticate()?" | `find_references` |
| "Trace what happens when login is called" | `trace_call_chain` |
| "What would break if I rename UserService?" | `impact_analysis` |
| "How do I extend BaseHandler?" | `show_contract` + `find_siblings` |
| "Where is the caching logic?" | `code_search` |

## Indexing

### Phase 1: Structure (fast, ~2 min)

Claude runs `index_codebase` — extracts classes, methods, functions, imports, inheritance, docs, tests via AST parsing.

### Phase 2: Call Graph (slower, ~10 min)

Claude runs `index_calls` — starts Pyright LSP, resolves actual call targets across files. This may timeout via MCP for large codebases. Alternative:

```bash
cd /path/to/your-project/coding-core
python -c "
import sys, json, time
sys.path.insert(0, '.')
from neo4j import GraphDatabase
from extraction.lsp_extractor import extract_calls_via_lsp
from graph.writers import write_calls

config = json.load(open('config.json'))
db = config['database']
target = config['target']

driver = GraphDatabase.driver(db['uri'], auth=(db['user'], db['password']))
methods, functions = [], []
with driver.session(database=db['name']) as s:
    methods = s.run('MATCH (c:Class)-[:CONTAINS_METHOD]->(m:Method) WHERE c.file_path IS NOT NULL AND m.line_start IS NOT NULL RETURN m.qualified_name AS qualified_name, m.name AS name, c.file_path AS file_path, m.line_start AS line_start').data()
    functions = s.run('MATCH (f:Function) WHERE f.file_path IS NOT NULL AND f.line_start IS NOT NULL RETURN f.qualified_name AS qualified_name, f.name AS name, f.file_path AS file_path, f.line_start AS line_start').data()

result = extract_calls_via_lsp(target['project_root'], target['source_roots'], methods, functions)
with driver.session(database=db['name']) as s:
    write_calls(s, result['calls'])
print(f'Done: {result[\"stats\"][\"calls_found\"]} CALLS edges')
driver.close()
"
```

## MCP Tools Reference

| Tool | What it does |
|------|-------------|
| `ping` | Check Neo4j connection |
| `index_codebase` | Extract code → Neo4j (AST + docs + tests) |
| `index_calls` | Add call graph via Pyright LSP |
| `show_architecture` | Package → Module → Class tree |
| `class_footprint` | Full class context (inheritance, methods, callers, docs, tests) |
| `trace_call_chain` | Follow method calls to depth N |
| `find_references` | Who uses this? (callers, subclasses, tests) |
| `find_siblings` | Classes sharing a parent |
| `show_contract` | Interface/abstract methods to implement |
| `find_entry_points` | HTTP routes + handlers |
| `code_search` | Hybrid: vector + fulltext + graph |
| `impact_analysis` | What breaks if I change this? |
| `find_docs_for_code` | Code → linked documentation |

## Multiple Projects on One Neo4j

Each project gets its own database name (auto-derived from folder name, or set with `--db-name`). All share the same Neo4j server and port.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| MCP tools not available | Restart Claude Code session |
| `ping` returns "down" | Check Neo4j is running, port matches config |
| `index_codebase` stacks/timeouts | Run with `skip_embeddings=true` (default) |
| `index_calls` stacks | Run the script above instead of via MCP |
| No CALLS edges | Need Phase 2 (Pyright). Install: `npm install -g pyright` |
| `code_search` slow first time | Embedding model loading (~30s one-time) |