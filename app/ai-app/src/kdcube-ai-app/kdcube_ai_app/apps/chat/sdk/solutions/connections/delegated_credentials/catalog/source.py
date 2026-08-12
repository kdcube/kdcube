# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The catalog body source: exact effective Connection Hub ``connections`` props.

Only ``on_app_deploy`` reads this. Request paths consume the registered catalog
document instead.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping


def connections_from_props(props: Any) -> dict[str, Any]:
    """The ``connections`` mapping exactly as it appears in effective props."""
    if not isinstance(props, Mapping):
        return {}
    raw = props.get("connections")
    if not isinstance(raw, Mapping):
        return {}
    return copy.deepcopy(dict(raw))


__all__ = ["connections_from_props"]
