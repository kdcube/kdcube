# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── skills_descriptor.py ──
# Skills descriptor for the comparison bundle.

from __future__ import annotations

import pathlib
from typing import Optional, Dict, Any

BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

CUSTOM_SKILLS_ROOT: Optional[pathlib.Path] = BUNDLE_ROOT / "skills"

AGENTS_CONFIG: Dict[str, Dict[str, Any]] = {
    "solver.react.decision.v2": {}
}
