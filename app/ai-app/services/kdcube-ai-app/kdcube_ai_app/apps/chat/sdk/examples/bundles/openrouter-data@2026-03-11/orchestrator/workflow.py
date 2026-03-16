# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── orchestrator/workflow.py ──
# Single-turn data-processing workflow backed by OpenRouter.
#
# Unlike the ReAct bundle this does NOT iterate with tools. It sends the
# user's message (plus an optional system prompt) to OpenRouter in a single
# completion call, streams nothing, and returns the result directly.
#
# The accounting decorator on ``openrouter_completion`` ensures that usage
# events are emitted automatically.

import logging
from typing import Any, Dict

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.inventory import Config
from kdcube_ai_app.infra.service_hub.openrouter import openrouter_completion

logger = logging.getLogger(__name__)

# ── System prompts for common data-processing tasks ──
SYSTEM_PROMPT_DATA_PROCESSOR = (
    "You are a precise data-processing assistant. "
    "Follow the user's instructions exactly. "
    "When asked to extract, classify, tag, summarize, or generate schemas, "
    "produce clean, structured output. "
    "Prefer JSON output when the task is structured."
)

# Default model for ad-hoc processing — fast and cheap via OpenRouter.
DEFAULT_MODEL = "google/gemini-2.5-flash-preview"


class OpenRouterDataWorkflow:
    """
    Minimal orchestrator: user message → OpenRouter completion → answer.

    No tool calling, no multi-turn, no context caching.
    """

    def __init__(
        self,
        *,
        comm: ChatCommunicator,
        config: Config,
        comm_context: ChatTaskPayload = None,
    ):
        self.comm = comm
        self.config = config
        self.comm_context = comm_context

    def _resolve_model(self) -> str:
        """Resolve the OpenRouter model from role config or default."""
        role_models = getattr(self.config, "role_models", {}) or {}
        spec = role_models.get("data-processor") or {}
        return spec.get("model") or DEFAULT_MODEL

    async def process(self, payload: dict) -> Dict[str, Any]:
        """
        Execute a single-turn data processing call via OpenRouter.

        Parameters
        ----------
        payload : dict
            Standard bundle payload with at minimum ``text`` (the user query).

        Returns
        -------
        dict
            {"answer": str, "followups": list}
        """
        user_text = payload.get("text") or ""
        model = self._resolve_model()

        # Emit a progress step so the UI knows work is happening
        await self.comm.step(
            step="processing",
            status="running",
            title="Processing via OpenRouter",
            data={"model": model},
            markdown=f"Sending request to **{model}** via OpenRouter...",
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_DATA_PROCESSOR},
            {"role": "user", "content": user_text},
        ]

        # The @track_llm decorator on openrouter_completion handles accounting
        with with_accounting("data-processor", metadata={"openrouter_model": model}):
            result = await openrouter_completion(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=4096,
            )

        if not result.get("success"):
            error_msg = result.get("error") or "Unknown error"
            await self.comm.step(
                step="processing",
                status="error",
                title="OpenRouter Error",
                data={"error": error_msg, "model": model},
                markdown=f"**Error:** {error_msg}",
            )
            return {"answer": f"Processing failed: {error_msg}", "followups": []}

        answer = result.get("text") or ""
        usage = result.get("usage") or {}
        actual_model = result.get("model") or model

        await self.comm.step(
            step="processing",
            status="done",
            title="Processing Complete",
            data={
                "model": actual_model,
                "usage": usage,
            },
            markdown=f"Processed by **{actual_model}** "
                     f"({usage.get('total_tokens', 0)} tokens)",
        )

        return {
            "answer": answer,
            "followups": [],
        }
