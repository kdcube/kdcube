"""
LSP-based extraction for CALLS edges and type hierarchy refinement.
Uses Pyright to resolve actual call targets across files.
"""

import logging
from pathlib import Path

from extraction.lsp_client import LSPClient, LSPError

log = logging.getLogger("coding-core-mcp")


def _file_uri(path: str) -> str:
    """Convert a file path to a file:// URI."""
    return Path(path).as_uri()


def _uri_to_path(uri: str) -> str:
    """Convert a file:// URI back to a path string."""
    if uri.startswith("file:///"):
        # Windows: file:///C:/... -> C:/...
        path = uri[8:]
    elif uri.startswith("file://"):
        path = uri[7:]
    else:
        path = uri
    # URL decode
    import urllib.parse
    return urllib.parse.unquote(path)


def extract_calls_via_lsp(
    project_root: str,
    source_roots: list[str],
    methods: list[dict],
    functions: list[dict],
    lsp_config: dict = None,
) -> dict:
    """
    Extract CALLS edges using Pyright's call hierarchy.

    Args:
        project_root: Absolute path to project root
        source_roots: List of source root directories (relative to project_root)
        methods: List of method dicts (from python_extractor) with qualified_name, file_path, line_start
        functions: List of function dicts with qualified_name, file_path, line_start
        lsp_config: LSP server config (command, args)

    Returns:
        dict with "calls" (list of {caller_qname, callee_qname}) and "stats"
    """
    if lsp_config is None:
        lsp_config = {"command": "pyright-langserver", "args": ["--stdio"]}

    root = Path(project_root)
    # Build the full source path for LSP
    src_path = str(root / source_roots[0]) if source_roots else str(root)

    client = LSPClient(
        command=lsp_config["command"],
        args=lsp_config["args"],
        cwd=src_path,
    )

    calls = []
    stats = {"total_symbols": 0, "calls_found": 0, "errors": 0, "skipped": 0}

    try:
        log.info("[LSP] Starting Pyright for %s", src_path)
        client.start(timeout=120.0)
        log.info("[LSP] Pyright initialized")

        # Build lookups to map LSP locations back to our graph nodes
        qname_by_location = {}  # (abs_path, 0-based line) -> qualified_name
        qname_by_name = {}  # simple name -> qualified_name (for fallback)
        all_symbols = methods + functions

        for sym in all_symbols:
            file_path = sym.get("file_path", "")
            line = sym.get("line_start", 0)
            name = sym.get("name", "")
            if file_path and line:
                abs_path = str(root / file_path)
                key = (abs_path, line - 1)  # LSP uses 0-based lines
                qname_by_location[key] = sym["qualified_name"]
            if name and name not in qname_by_name:
                qname_by_name[name] = sym["qualified_name"]

        stats["total_symbols"] = len(all_symbols)
        log.info("[LSP] Processing %d symbols for call hierarchy", len(all_symbols))

        _opened_files = {}  # abs_path -> list of lines
        processed = 0
        for sym in all_symbols:
            file_path = sym.get("file_path", "")
            line = sym.get("line_start", 0)
            name = sym.get("name", "")

            if not file_path or not line:
                stats["skipped"] += 1
                continue

            abs_path = str(root / file_path)
            if not Path(abs_path).exists():
                stats["skipped"] += 1
                log.debug("[LSP] File not found: %s", abs_path)
                continue

            file_uri = _file_uri(abs_path)
            lsp_line = line - 1  # 0-based

            # Open the file in LSP if not already opened, cache lines
            if abs_path not in _opened_files:
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                    client.did_open(file_uri, "python", text)
                    _opened_files[abs_path] = text.split("\n")
                except Exception:
                    stats["skipped"] += 1
                    continue

            # Find the column of the symbol name in the source line
            file_lines = _opened_files.get(abs_path, [])
            col = 4  # default fallback
            if lsp_line < len(file_lines):
                source_line = file_lines[lsp_line]
                idx = source_line.find(name)
                if idx >= 0:
                    col = idx

            try:
                items = client.prepare_call_hierarchy(file_uri, lsp_line, col)
                if not items:
                    stats["skipped"] += 1
                    continue

                item = items[0] if isinstance(items, list) else items

                # Get outgoing calls (what this symbol calls)
                outgoing = client.call_hierarchy_outgoing(item)
                if outgoing:
                    for call in outgoing:
                        target = call.get("to", {})
                        target_uri = target.get("uri", "")
                        target_line = target.get("range", {}).get("start", {}).get("line", -1)
                        target_name = target.get("name", "")

                        if target_uri and target_line >= 0:
                            target_path = _uri_to_path(target_uri)
                            target_key = (target_path, target_line)
                            callee_qname = qname_by_location.get(target_key)

                            if not callee_qname:
                                # Try lookup by name as fallback
                                callee_qname = qname_by_name.get(target_name)

                            if callee_qname:
                                calls.append({
                                    "caller_qname": sym["qualified_name"],
                                    "callee_qname": callee_qname,
                                })

                processed += 1
                if processed % 100 == 0:
                    log.info("[LSP] Processed %d/%d symbols, %d calls found",
                             processed, len(all_symbols), len(calls))

            except (LSPError, TimeoutError) as e:
                stats["errors"] += 1
                if stats["errors"] <= 5:
                    log.warning("[LSP] Error on %s.%s: %s", file_path, name, e)
            except Exception as e:
                stats["errors"] += 1
                if stats["errors"] <= 5:
                    log.warning("[LSP] Unexpected error on %s.%s: %s", file_path, name, e)

        # Deduplicate calls
        seen = set()
        unique_calls = []
        for c in calls:
            key = (c["caller_qname"], c["callee_qname"])
            if key not in seen:
                seen.add(key)
                unique_calls.append(c)

        stats["calls_found"] = len(unique_calls)
        log.info("[LSP] Extraction complete: %d unique CALLS edges from %d symbols",
                 len(unique_calls), processed)

    except Exception as e:
        log.error("[LSP] Fatal error: %s", e)
        stats["fatal_error"] = str(e)
    finally:
        client.shutdown()

    return {"calls": unique_calls, "stats": stats}