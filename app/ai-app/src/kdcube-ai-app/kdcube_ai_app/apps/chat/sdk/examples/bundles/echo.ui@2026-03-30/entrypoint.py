# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── entrypoint.py ──
# Minimal echo bundle for testing the bundle UI build pipeline.
#
# What it does:
#   1. Registers the bundle under "echo.ui" (@agentic_workflow)
#   2. Echoes the user's message back as the final answer (no LLM)
#   3. Declares `ui.main_view` in configuration_defaults so that
#      BaseEntrypoint.on_bundle_load() builds the custom frontend via npm
#
# The UI source lives in ui-src/ (Vite + React).
# After build, static files are served at:
#   GET /api/integrations/static/{tenant}/{project}/echo.ui/{path}

from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import StateGraph, START, END

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.infra.service_hub.inventory import Config, BundleState
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, api, bundle_id, cron
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint

BUNDLE_ID = "echo.ui"


@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=10)
@bundle_id(id="echo.ui@2026-03-30")
class EchoUIBundle(BaseEntrypoint):
    """
    Minimal echo bundle that reflects the user's message back unchanged.
    Demonstrates the bundle UI build pipeline — the custom React frontend
    in ui-src/ is built by on_bundle_load() and served as static assets.
    """

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ChatTaskPayload = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
        )
        self.graph = self._build_graph()

    def configuration_defaults(self) -> Dict[str, Any]:
        return {
            "ui": {
                "main_view": {
                    # Path to the React source, relative to this bundle's directory.
                    "src_folder": "ui-src",
                    # npm build command; <VI_BUILD_DEST_ABSOLUTE_PATH> is replaced
                    # by BaseEntrypoint._ensure_ui_build() with the actual output path
                    # inside bundle local storage. The resolved delivery id is
                    # passed separately through the build environment as
                    # VI_BUNDLE_ID / VITE_BUNDLE_ID, so it does not need to
                    # appear literally in the shell command string.
                    "build_command": (
                        "npm install && "
                        "OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build"
                    ),
                }
            }
        }

    def _build_graph(self) -> StateGraph:
        g = StateGraph(BundleState)

        async def echo(state: BundleState) -> BundleState:
            state["final_answer"] = state.get("text") or ""
            return state

        g.add_node("echo", echo)
        g.add_edge(START, "echo")
        g.add_edge("echo", END)
        return g.compile()

    @api(alias="echo")
    async def echo(
            self,
            *,
            text: str = "",
            user_id: str = "",
            fingerprint: str = "",
            **kwargs) -> Dict[str, Any]:
        return {"text": text}

    async def execute_core(
        self,
        *,
        state: Dict[str, Any],
        thread_id: str,
        params: Dict[str, Any],
    ):
        return await self.graph.ainvoke(state)

    @cron(
        alias="echo-heartbeat",
        cron_expression="* * * * *",
        expr_config="routines.heartbeat.cron",
        span="system",
    )
    async def scheduled_heartbeat(self) -> None:
        """
        Sandbox cron job for testing @cron decorator.
        Fires every minute by default; can be overridden or disabled via bundle props:
          routines.heartbeat.cron: "*/5 * * * *"   # change interval
          routines.heartbeat.cron: "disable"        # disable the job
        """
        import logging
        log = logging.getLogger("echo.ui.cron")
        props_snapshot = dict(self.bundle_props or {})
        log.info(
            "[echo.ui] scheduled_heartbeat fired | bundle_props keys=%s | redis=%s",
            list(props_snapshot.keys()),
            "ok" if self.redis is not None else "none",
        )
