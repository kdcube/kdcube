#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Standalone CLI entry point (no package install required)."""
from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_sys_path() -> None:
    here = Path(__file__).resolve()
    ai_app_root = here.parents[3]
    services_root = ai_app_root / "services/kdcube-ai-app"
    if services_root.exists():
        sys.path.insert(0, str(services_root))


def main() -> None:
    _bootstrap_sys_path()
    try:
        from kdcube_ai_app.ops.cli.installer import main as installer_main
    except ImportError as exc:
        raise SystemExit(
            "Could not import the installer. "
            "Make sure you are running inside the kdcube-ai-app repo."
        ) from exc
    installer_main()


if __name__ == "__main__":
    main()
