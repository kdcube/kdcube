# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── knowledge_base/store.py ──
# Persistent JSON-file knowledge base for technology comparisons.
#
# Stores:
#   - Technology profiles (name, category, description, capabilities, links)
#   - Daily comparison results (KDCube vs each technology)
#   - Comparison history with timestamped snapshots
#   - Cache layer with daily TTL to avoid redundant lookups
#
# Storage layout under <storage_root>/comparison_kb/:
#   technologies/          — one JSON per technology
#   comparisons/           — daily comparison results
#   history/               — timestamped history snapshots
#   cache/                 — daily cache metadata

from __future__ import annotations

import json
import pathlib
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class ComparisonKnowledgeBase:
    """File-backed knowledge base for technology comparisons."""

    def __init__(self, storage_root: pathlib.Path):
        self.root = pathlib.Path(storage_root) / "comparison_kb"
        self._tech_dir = self.root / "technologies"
        self._comp_dir = self.root / "comparisons"
        self._history_dir = self.root / "history"
        self._cache_dir = self.root / "cache"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for d in (self._tech_dir, self._comp_dir, self._history_dir, self._cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _slug(name: str) -> str:
        return name.lower().replace(" ", "-").replace("/", "-").replace(".", "-")

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Technology profiles ──────────────────────────────────────

    def list_technologies(self) -> List[Dict[str, Any]]:
        techs = []
        for f in sorted(self._tech_dir.glob("*.json")):
            try:
                techs.append(json.loads(f.read_text()))
            except Exception:
                continue
        return techs

    def get_technology(self, name: str) -> Optional[Dict[str, Any]]:
        path = self._tech_dir / f"{self._slug(name)}.json"
        if path.exists():
            return json.loads(path.read_text())
        return None

    def upsert_technology(self, tech: Dict[str, Any]) -> None:
        name = tech.get("name", "")
        slug = self._slug(name)
        tech.setdefault("slug", slug)
        tech.setdefault("added_at", datetime.now(timezone.utc).isoformat())
        tech["updated_at"] = datetime.now(timezone.utc).isoformat()
        path = self._tech_dir / f"{slug}.json"
        path.write_text(json.dumps(tech, indent=2, default=str))

    def remove_technology(self, name: str) -> bool:
        path = self._tech_dir / f"{self._slug(name)}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # ── Comparisons ──────────────────────────────────────────────

    def get_comparison(self, tech_name: str, date: str = None) -> Optional[Dict[str, Any]]:
        date = date or self._today()
        path = self._comp_dir / f"{self._slug(tech_name)}_{date}.json"
        if path.exists():
            return json.loads(path.read_text())
        return None

    def save_comparison(self, tech_name: str, comparison: Dict[str, Any]) -> None:
        date = self._today()
        slug = self._slug(tech_name)
        comparison["date"] = date
        comparison["technology"] = tech_name
        comparison["saved_at"] = datetime.now(timezone.utc).isoformat()

        # Save daily comparison
        path = self._comp_dir / f"{slug}_{date}.json"
        path.write_text(json.dumps(comparison, indent=2, default=str))

        # Append to history
        self._append_history(tech_name, comparison)

    def get_all_comparisons(self, date: str = None) -> List[Dict[str, Any]]:
        date = date or self._today()
        results = []
        for f in sorted(self._comp_dir.glob(f"*_{date}.json")):
            try:
                results.append(json.loads(f.read_text()))
            except Exception:
                continue
        return results

    def get_latest_comparisons(self) -> List[Dict[str, Any]]:
        """Return the most recent comparison for each technology."""
        latest: Dict[str, Dict[str, Any]] = {}
        for f in sorted(self._comp_dir.glob("*.json")):
            try:
                comp = json.loads(f.read_text())
                tech = comp.get("technology", "")
                date = comp.get("date", "")
                if tech not in latest or date > latest[tech].get("date", ""):
                    latest[tech] = comp
            except Exception:
                continue
        return list(latest.values())

    # ── History ──────────────────────────────────────────────────

    def _append_history(self, tech_name: str, comparison: Dict[str, Any]) -> None:
        slug = self._slug(tech_name)
        path = self._history_dir / f"{slug}.jsonl"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "comparison": comparison,
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def get_history(self, tech_name: str, limit: int = 30) -> List[Dict[str, Any]]:
        slug = self._slug(tech_name)
        path = self._history_dir / f"{slug}.jsonl"
        if not path.exists():
            return []
        entries = []
        for line in path.read_text().strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
        return entries[-limit:]

    # ── Cache ────────────────────────────────────────────────────

    def is_cached_today(self, tech_name: str) -> bool:
        date = self._today()
        return self.get_comparison(tech_name, date) is not None

    def get_cache_status(self) -> Dict[str, Any]:
        date = self._today()
        techs = self.list_technologies()
        cached = []
        stale = []
        for tech in techs:
            name = tech.get("name", "")
            if self.is_cached_today(name):
                cached.append(name)
            else:
                stale.append(name)
        return {
            "date": date,
            "cached": cached,
            "stale": stale,
            "total_technologies": len(techs),
        }

    # ── Aggregate comparison table ───────────────────────────────

    def build_comparison_table(self) -> Dict[str, Any]:
        """Build a full comparison table from latest comparisons + technology profiles."""
        techs = self.list_technologies()
        comparisons = self.get_latest_comparisons()
        comp_by_tech = {c.get("technology", ""): c for c in comparisons}

        rows = []
        for tech in techs:
            name = tech.get("name", "")
            comp = comp_by_tech.get(name, {})
            rows.append({
                "technology": name,
                "category": tech.get("category", ""),
                "examples": tech.get("examples", ""),
                "what_it_does": comp.get("what_it_does", tech.get("what_it_does", "")),
                "what_it_does_not": comp.get("what_it_does_not", tech.get("what_it_does_not", "")),
                "kdcube_advantage": comp.get("kdcube_advantage", ""),
                "comparison_date": comp.get("date", ""),
                "profile": tech,
            })

        # Always add KDCube as the final highlighted row
        rows.append({
            "technology": "KDCube",
            "category": "Semantic Sandbox",
            "examples": "",
            "what_it_does": (
                "Intercepts tool calls and API actions; enforces budget caps, "
                "rate limits, and tenant boundaries before execution "
                "(full semantic workflow constraints are on the roadmap)"
            ),
            "what_it_does_not": (
                "Not an LLM proxy, not a framework, not a log aggregator"
            ),
            "kdcube_advantage": "",
            "comparison_date": self._today(),
            "is_kdcube": True,
        })

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "date": self._today(),
            "row_count": len(rows),
            "rows": rows,
        }

    def export_for_widget(self) -> Dict[str, Any]:
        """Export data formatted for the website frontend widget."""
        table = self.build_comparison_table()
        widget_rows = []
        for row in table["rows"]:
            widget_rows.append({
                "name": row["technology"],
                "category": row.get("category", ""),
                "examples": row.get("examples", ""),
                "does": row.get("what_it_does", ""),
                "does_not": row.get("what_it_does_not", ""),
                "advantage": row.get("kdcube_advantage", ""),
                "is_kdcube": row.get("is_kdcube", False),
                "date": row.get("comparison_date", ""),
            })
        return {
            "generated_at": table["generated_at"],
            "date": table["date"],
            "rows": widget_rows,
        }
