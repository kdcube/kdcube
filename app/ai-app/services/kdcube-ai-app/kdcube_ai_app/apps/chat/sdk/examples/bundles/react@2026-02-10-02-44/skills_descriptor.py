# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import pathlib
from typing import Optional, Dict, Any

# Bundle root = directory containing this file (i.e., the bundle root)
BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

# Optional external root for custom skills (namespace "custom").
# Set this to a directory that contains skills in the same folder layout:
#   <root>/<namespace>/<skill_id>/SKILL.md
CUSTOM_SKILLS_ROOT: Optional[pathlib.Path] = BUNDLE_ROOT / "skills"

# Per-agent skill filters. Only enabled skills are visible in the gallery for that agent.
# Use fully qualified ids: "<namespace>.<skill_id>".
AGENTS_CONFIG: Dict[str, Dict[str, Any]] = {
    "solver.coordinator.v2": {
        # "enabled": [
        #     "public.url-gen",
        # ]
        "disabled": ["product.*"],
    },
    "solver.react.decision.v2": {
        # "enabled": [
        #     "public.url-gen",
        # ]
        "disabled": ["product.*"],
    },
    "answer.generator.strong": {
        "disabled": [
            "public.*-press",
            "public.url-gen",
        ]
    },
    "answer.generator.regular": {
        "disabled": [
            "public.*-press",
            "public.url-gen",
        ]
    },
}
