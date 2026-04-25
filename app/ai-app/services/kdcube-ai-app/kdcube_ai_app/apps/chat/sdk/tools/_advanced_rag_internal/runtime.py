# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Long-lived runtime container for the advanced RAG tool.

Holds dependencies injected once per orchestrator boot. Per-turn context
(conversation_id, turn_id, search_settings, etc.) is fetched at call-time via
`get_runtime_ctx` so we never hold stale per-turn data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class AdvancedRAGRuntime:
    kb: Any                                        # KBClient (already initialised)
    model_service: Any                             # ModelService for LLM calls
    conv_store: Any                                # ConversationStore (sync API)
    get_runtime_ctx: Callable[[], Any]             # () -> RuntimeCtx (current turn)
    enabled: bool = True

    # Optional: if True, the bundle wants advanced RAG suppressed entirely
    # (e.g. via `features.enable_knowledge_search=False`). Tool returns [].
    knowledge_enabled_check: Optional[Callable[[], bool]] = None

    def is_available(self) -> bool:
        if not self.enabled or self.kb is None:
            return False
        if self.knowledge_enabled_check is not None:
            try:
                return bool(self.knowledge_enabled_check())
            except Exception:
                return False
        return True
