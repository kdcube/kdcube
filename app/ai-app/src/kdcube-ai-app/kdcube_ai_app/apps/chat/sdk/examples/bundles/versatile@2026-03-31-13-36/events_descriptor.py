from __future__ import annotations

from typing import Any, Dict, List

# Event-source visibility is separate from tool visibility.
#
# TOOLS_SPECS in tools_descriptor.py lists model-callable tools. This descriptor
# lists runtime event surfaces: policies, event-source readers, and namespace
# rehosters. Loading canvas.events.resolver here makes cnv: refs pullable by
# ReAct. The canvas pin tool (canvas.patch) is also registered as a callable
# tool in tools_descriptor.py, so the agent can deliberately pin to the board.
EVENT_SOURCE_SPECS: List[Dict[str, Any]] = [
    {
        "module": "kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.resolver",
        "alias": "canvas",
    },
]
