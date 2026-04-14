# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# -- sdk/tools/code_graph_state.py --
# Shared code graph client state between bundle entrypoints and SDK tools.
# Loaded via importlib with shared module name (_kdcube_code_graph_state)
# so that any entrypoint and code_graph_tools access the same globals.
#
# Follows the same pattern as knowledge/resolver.py KNOWLEDGE_ROOT.

from __future__ import annotations

from typing import Any, Optional

# Set by entrypoint.orchestrate(); read by code_graph_tools.py.
# When APP_GRAPH_ENABLED=false or feature toggle is off,
# this will be a NullCodeGraphClient (null object pattern).
CLIENT: Optional[Any] = None
