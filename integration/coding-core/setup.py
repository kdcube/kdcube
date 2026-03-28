"""
Coding-Core Setup Script
========================
Run from your project root:
    python coding-core/setup.py

Or with arguments:
    python coding-core/setup.py --db-uri bolt://127.0.0.1:7690 --db-name my-project --db-password mypass

This will:
1. Auto-detect source roots, docs, and test patterns
2. Generate coding-core/config.json
3. Generate .mcp.json in your project root
4. Generate .claude/settings.local.json with MCP permissions
5. Generate .claude/rules/coding-core-workflow.md
"""

import argparse
import json
import sys
from pathlib import Path


def detect_source_roots(project_root: Path) -> list[str]:
    """Auto-detect Python/TypeScript source roots."""
    roots = []
    # Common Python patterns
    for candidate in ["src", "lib", "app", "."]:
        p = project_root / candidate
        if p.exists():
            # Check for __init__.py or .py files
            py_files = list(p.rglob("*.py"))
            if len(py_files) > 5:
                roots.append(candidate)
                break

    # If no standard root, look for the deepest __init__.py pattern
    if not roots:
        for init in sorted(project_root.rglob("__init__.py")):
            rel = str(init.parent.relative_to(project_root))
            parts = rel.replace("\\", "/").split("/")
            if len(parts) >= 2:
                roots.append("/".join(parts[:2]))
                break

    # TypeScript/JS
    for candidate in ["src", "lib", "app"]:
        p = project_root / candidate
        if p.exists():
            ts_files = list(p.rglob("*.ts")) + list(p.rglob("*.tsx"))
            if len(ts_files) > 5 and candidate not in roots:
                roots.append(candidate)
                break

    return roots if roots else ["."]


def detect_docs_root(project_root: Path) -> str:
    """Auto-detect documentation directory."""
    for candidate in ["docs", "doc", "documentation", "wiki"]:
        p = project_root / candidate
        if p.exists() and list(p.rglob("*.md")):
            return candidate
    return "docs"


def detect_test_patterns(project_root: Path) -> list[str]:
    """Auto-detect test file patterns."""
    patterns = []
    if list(project_root.rglob("test_*.py")):
        patterns.append("test_*.py")
    if list(project_root.rglob("*_test.py")):
        patterns.append("*_test.py")
    if list(project_root.rglob("*.test.ts")):
        patterns.append("*.test.ts")
    if list(project_root.rglob("*.spec.ts")):
        patterns.append("*.spec.ts")
    return patterns if patterns else ["test_*.py"]


def main():
    parser = argparse.ArgumentParser(description="Setup Coding-Core for your project")
    parser.add_argument("--db-uri", default="bolt://127.0.0.1:7687",
                        help="Neo4j bolt URI (default: bolt://127.0.0.1:7687)")
    parser.add_argument("--db-user", default="neo4j",
                        help="Neo4j username (default: neo4j)")
    parser.add_argument("--db-password", default="",
                        help="Neo4j password")
    parser.add_argument("--db-name", default="",
                        help="Neo4j database name (default: derived from project folder)")
    parser.add_argument("--project-root", default=None,
                        help="Project root (default: parent of coding-core/ folder)")
    args = parser.parse_args()

    # Resolve paths
    script_dir = Path(__file__).parent.resolve()
    project_root = Path(args.project_root).resolve() if args.project_root else script_dir.parent

    if not project_root.exists():
        print(f"ERROR: Project root not found: {project_root}")
        sys.exit(1)

    # Derive database name from project folder if not given
    db_name = args.db_name or project_root.name.lower().replace(" ", "-").replace("_", "-")

    # Prompt for password if not provided
    db_password = args.db_password
    if not db_password:
        db_password = input(f"Neo4j password for {args.db_uri}: ").strip()
        if not db_password:
            print("ERROR: Password is required")
            sys.exit(1)

    print(f"Project root: {project_root}")
    print(f"Database: {db_name} @ {args.db_uri}")

    # Auto-detect project structure
    print("\nDetecting project structure...")
    source_roots = detect_source_roots(project_root)
    docs_root = detect_docs_root(project_root)
    test_patterns = detect_test_patterns(project_root)

    print(f"  Source roots: {source_roots}")
    print(f"  Docs root: {docs_root}")
    print(f"  Test patterns: {test_patterns}")

    # 1. Generate config.json
    config = {
        "database": {
            "uri": args.db_uri,
            "user": args.db_user,
            "password": db_password,
            "name": db_name,
        },
        "embedding": {
            "model": "all-MiniLM-L6-v2",
            "dimensions": 384,
        },
        "lsp": {
            "servers": {
                "python": {"command": "pyright-langserver", "args": ["--stdio"]},
                "typescript": {"command": "typescript-language-server", "args": ["--stdio"]},
            }
        },
        "target": {
            "project_root": str(project_root),
            "source_roots": source_roots,
            "docs_root": docs_root,
            "test_patterns": test_patterns,
        },
    }

    config_path = script_dir / "config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"\n[OK] {config_path}")

    # 2. Generate .mcp.json
    server_path = str(script_dir / "server.py").replace("\\", "\\\\")
    mcp_json = {
        "mcpServers": {
            "coding-core": {
                "type": "stdio",
                "command": "python",
                "args": [server_path],
                "env": {
                    "DB_URI": args.db_uri,
                    "DB_USER": args.db_user,
                    "DB_PASSWORD": db_password,
                    "DB_NAME": db_name,
                    "PROJECT_ROOT": str(project_root),
                },
            }
        }
    }

    mcp_path = project_root / ".mcp.json"
    # Merge with existing .mcp.json if present
    if mcp_path.exists():
        with open(mcp_path) as f:
            existing = json.load(f)
        existing.setdefault("mcpServers", {}).update(mcp_json["mcpServers"])
        mcp_json = existing

    with open(mcp_path, "w") as f:
        json.dump(mcp_json, f, indent=2)
    print(f"[OK] {mcp_path}")

    # 3. Generate .claude/settings.local.json
    claude_dir = project_root / ".claude"
    claude_dir.mkdir(exist_ok=True)

    settings_path = claude_dir / "settings.local.json"
    permissions = [
        "mcp__coding-core__ping",
        "mcp__coding-core__index_codebase",
        "mcp__coding-core__index_calls",
        "mcp__coding-core__show_architecture",
        "mcp__coding-core__class_footprint",
        "mcp__coding-core__trace_call_chain",
        "mcp__coding-core__find_references",
        "mcp__coding-core__find_siblings",
        "mcp__coding-core__show_contract",
        "mcp__coding-core__find_entry_points",
        "mcp__coding-core__code_search",
        "mcp__coding-core__impact_analysis",
        "mcp__coding-core__find_docs_for_code",
    ]

    if settings_path.exists():
        with open(settings_path) as f:
            settings = json.load(f)
        existing_perms = settings.get("permissions", {}).get("allow", [])
        # Merge permissions
        all_perms = list(dict.fromkeys(existing_perms + permissions))
        settings.setdefault("permissions", {})["allow"] = all_perms
    else:
        settings = {"permissions": {"allow": permissions}}

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
    print(f"[OK] {settings_path}")

    # 4. Generate .claude/rules/coding-core-workflow.md
    rules_dir = claude_dir / "rules"
    rules_dir.mkdir(exist_ok=True)

    rules_content = """# Coding-Core MCP Workflow Rules

## Rule 1: Ping Neo4j First

**BEFORE ANY MCP CALL:** Call `mcp__coding-core__ping` to check Neo4j status.

Only need to ping once per session. If it returned "ok" earlier, proceed without re-ping.

---

## Rule 2: Use Code Graph for Structural Questions

For questions about class relationships, call chains, inheritance, or "who uses X?":

1. Use `class_footprint`, `trace_call_chain`, `find_references`, `find_siblings`, `show_contract`
2. Do NOT grep/glob the codebase when the graph has the answer
3. Graph queries return in <200ms vs 60-80 tool calls for manual exploration

---

## Rule 3: Use Code Search for Conceptual Questions

For "how does X work?" or "where is the Y logic?":

1. Use `code_search` with hybrid mode first (vector + fulltext + graph)
2. Fall back to file reading only if graph results are insufficient
3. Always check `find_docs_for_code` to include official documentation in explanations

---

## Rule 4: Re-index After Code Changes

After significant code changes to the target codebase:

1. Run `index_incremental` to update the graph
2. Do NOT rely on stale graph data for structural queries
3. If incremental fails, run `index_codebase` with force_reindex=true

---

## Rule 5: Always Check Documentation Links

When explaining code to the user:

1. Use `find_docs_for_code` to find linked documentation
2. Include doc references in explanations (the "why" behind the "what")
3. If graph shows no docs for a class, mention that it lacks documentation

---

## Rule 6: Impact Analysis Before Refactoring

Before renaming, deleting, or modifying a public symbol:

1. Run `impact_analysis` to find all affected code
2. Present the impact summary to the user before proceeding
3. Include: direct callers, transitive callers, overrides, subclass overrides, tests that break
"""

    rules_path = rules_dir / "coding-core-workflow.md"
    with open(rules_path, "w") as f:
        f.write(rules_content)
    print(f"[OK] {rules_path}")

    # 5. Add to .gitignore
    gitignore_path = project_root / ".gitignore"
    ignore_entries = [
        "# Coding-Core",
        "coding-core/config.json",
        ".claude/settings.local.json",
    ]

    if gitignore_path.exists():
        existing = gitignore_path.read_text()
        if "coding-core/config.json" not in existing:
            with open(gitignore_path, "a") as f:
                f.write("\n" + "\n".join(ignore_entries) + "\n")
            print(f"[OK] Updated {gitignore_path}")
    else:
        with open(gitignore_path, "w") as f:
            f.write("\n".join(ignore_entries) + "\n")
        print(f"[OK] Created {gitignore_path}")

    # Summary
    print(f"""
{'='*60}
Setup complete!

Next steps:
  1. Install dependencies:
     pip install -r coding-core/requirements.txt

  2. Start/restart Claude Code in this project

  3. Ask Claude to index:
     "Ping Neo4j, then run index_codebase"

  4. For call graph (optional, takes ~10 min):
     Run manually:
     cd coding-core && python -c "
     import sys; sys.path.insert(0, '.')
     # ... or ask Claude to run index_calls
     "

  5. Start exploring:
     "What are the main classes?"
     "Show me the class footprint for MyClass"
     "Who calls authenticate()?"
     "What would break if I rename UserService?"
{'='*60}
""")


if __name__ == "__main__":
    main()