from __future__ import annotations

import pathlib
from typing import Any, Dict, Optional

BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent
CUSTOM_SKILLS_ROOT: Optional[pathlib.Path] = BUNDLE_ROOT / "skills"
AGENTS_CONFIG: Dict[str, Dict[str, Any]] = {
    "solver.react.decision.v2": {},
}
