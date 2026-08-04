# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── tools/comparison_tools.py ──
# Bundle-local tools for the comparison demo.
# Provides:
#   - compare_technology: research and compare a technology against KDCube
#   - add_technology:     add a new technology to the knowledge base
#   - list_technologies:  list all tracked technologies
#   - get_comparison_table: build the full comparison table
#   - get_comparison_history: retrieve history for a technology
#   - get_cache_status:   show what's cached today vs stale
#   - export_widget_data: export data for the website widget

from __future__ import annotations

import json
import pathlib
from typing import Any

from semantic_kernel.functions import kernel_function

# Module-level KB reference set by entrypoint on bundle load
_kb = None


def set_kb(kb):
    global _kb
    _kb = kb


def _get_kb():
    if _kb is None:
        raise RuntimeError("Knowledge base not initialized. Bundle must call set_kb() first.")
    return _kb


@kernel_function(
    name="compare_technology",
    description=(
        "Research and compare a named technology against KDCube. "
        "Generates a structured comparison with what the technology does, "
        "what it does NOT do, and KDCube's advantage. "
        "Results are cached daily — if already compared today, returns cached data. "
        "Input: technology name (string). Returns: comparison dict."
    ),
)
def compare_technology(
    technology_name: str,
    what_it_does: str = "",
    what_it_does_not: str = "",
    kdcube_advantage: str = "",
    category: str = "",
    examples: str = "",
) -> str:
    """Compare a technology against KDCube and store the result."""
    kb = _get_kb()

    # Check daily cache
    cached = kb.get_comparison(technology_name)
    if cached and not what_it_does:
        return json.dumps({
            "status": "cached",
            "message": f"Comparison for '{technology_name}' already cached today.",
            "comparison": cached,
        }, indent=2)

    # Ensure technology exists in KB
    tech = kb.get_technology(technology_name)
    if not tech:
        tech = {
            "name": technology_name,
            "category": category or "Uncategorized",
            "examples": examples,
        }
        kb.upsert_technology(tech)

    # Save the comparison
    comparison = {
        "technology": technology_name,
        "what_it_does": what_it_does,
        "what_it_does_not": what_it_does_not,
        "kdcube_advantage": kdcube_advantage,
        "category": category or tech.get("category", ""),
        "examples": examples or tech.get("examples", ""),
    }
    kb.save_comparison(technology_name, comparison)

    return json.dumps({
        "status": "saved",
        "message": f"Comparison for '{technology_name}' saved.",
        "comparison": comparison,
    }, indent=2)


@kernel_function(
    name="add_technology",
    description=(
        "Add a new technology to the comparison knowledge base. "
        "Provide name, category, description, examples, and capabilities."
    ),
)
def add_technology(
    name: str,
    category: str = "",
    description: str = "",
    examples: str = "",
    homepage: str = "",
    what_it_does: str = "",
    what_it_does_not: str = "",
) -> str:
    """Add or update a technology profile in the knowledge base."""
    kb = _get_kb()
    tech = {
        "name": name,
        "category": category,
        "description": description,
        "examples": examples,
        "homepage": homepage,
        "what_it_does": what_it_does,
        "what_it_does_not": what_it_does_not,
    }
    kb.upsert_technology(tech)
    return json.dumps({"status": "added", "technology": tech}, indent=2)


@kernel_function(
    name="list_technologies",
    description="List all technologies tracked in the comparison knowledge base.",
)
def list_technologies() -> str:
    """Return all tracked technologies."""
    kb = _get_kb()
    techs = kb.list_technologies()
    return json.dumps({
        "count": len(techs),
        "technologies": [
            {"name": t["name"], "category": t.get("category", ""), "examples": t.get("examples", "")}
            for t in techs
        ],
    }, indent=2)


@kernel_function(
    name="get_comparison_table",
    description=(
        "Build the full comparison table with all technologies compared against KDCube. "
        "Returns the latest comparison data for each technology."
    ),
)
def get_comparison_table() -> str:
    """Build and return the aggregate comparison table."""
    kb = _get_kb()
    table = kb.build_comparison_table()
    return json.dumps(table, indent=2)


@kernel_function(
    name="get_comparison_history",
    description="Get comparison history for a specific technology. Shows how comparisons evolved over time.",
)
def get_comparison_history(technology_name: str, limit: int = 10) -> str:
    """Return comparison history for a technology."""
    kb = _get_kb()
    history = kb.get_history(technology_name, limit=int(limit))
    return json.dumps({
        "technology": technology_name,
        "entries": len(history),
        "history": history,
    }, indent=2)


@kernel_function(
    name="get_cache_status",
    description="Show which technologies have been compared today and which need updating.",
)
def get_cache_status() -> str:
    """Return cache status for all technologies."""
    kb = _get_kb()
    status = kb.get_cache_status()
    return json.dumps(status, indent=2)


@kernel_function(
    name="export_widget_data",
    description=(
        "Export the comparison data formatted for the website frontend widget. "
        "Returns JSON that can be embedded directly in the KDCube website."
    ),
)
def export_widget_data() -> str:
    """Export comparison data for the website widget."""
    kb = _get_kb()
    data = kb.export_for_widget()
    return json.dumps(data, indent=2)
