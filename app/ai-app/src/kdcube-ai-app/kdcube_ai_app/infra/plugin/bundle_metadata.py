"""Display metadata shared by descriptor and runtime bundle projections."""
from __future__ import annotations


def bundle_display_name(bundle_id: str, name: str | None = None) -> str:
    """Use the configured nonblank name, otherwise the complete bundle id."""
    return str(name or "").strip() or str(bundle_id or "").strip()
