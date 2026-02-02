# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/context/browser.py

from __future__ import annotations

import copy, logging
import pathlib
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type

from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import BaseTurnView, TurnScratchpad
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.retrieval import reconcile_citations_for_context
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
import kdcube_ai_app.apps.chat.sdk.runtime.solution.context.retrieval as ctx_retrieval_module
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import search_context, ContextRAGClient
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext  # used only by make_react_context

PROJECT_LOG_SLOTS = { "project_log" }

logger = logging.getLogger(__name__)
@dataclass
class ContextBundle:
    """A single, reusable container for both React and Codegen."""
    program_history: List[Dict[str, Any]]
    program_history_reconciled: List[Dict[str, Any]]
    sources_pool: List[Dict[str, Any]]

    user_id: Optional[str] = None

    conversation_id: Optional[str] = None
    turn_id: Optional[str] = None
    track_id: Optional[str] = None

    session_id: Optional[str] = None
    bundle_id: Optional[str] = None
    user_type: Optional[str] = None


class ContextBrowser:
    """
    Centralized context/history loader + reconciliation.
    Produces a ContextBundle that can be consumed by:
      - ReactSolver (to build a ReactContext)
      - CodegenToolManager (to extract canvas, playbook, etc.)
    Uses ContextRAGClient for both search and materialization.
    """

    def __init__(self, *,
                 ctx_client: Optional[ContextRAGClient] = None,
                 turn_view_class: Type[BaseTurnView] = BaseTurnView,
                 logger: Optional[AgentLogger] = None):
        self.ctx_client = ctx_client
        self.log = logger or AgentLogger("context_browser")
        self.turn_view_class = turn_view_class

    async def materialize(
            self,
            *,
            materialize_turn_ids: Optional[List[str]] = None,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            scope: str = "track",
            days: int = 365,
            max_sources: int = 60,
    ) -> ContextBundle:
        """
        Fetch history → reconcile citations → compute playbook + last working canvas.
        Returns a ContextBundle that fits both React and Codegen flows.
        """
        materialize_turn_ids = materialize_turn_ids or []

        # --- 1) history
        program_history: List[Dict[str, Any]] = []
        if materialize_turn_ids:
            try:
                program_history = await ctx_retrieval_module.build_program_history_from_turn_ids_by_client(
                    ctx_client=self.ctx_client,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    turn_ids=materialize_turn_ids,
                    scope=scope,
                    days=days,
                )
            except Exception as e:
                self.log.log(f"[context_browser] history error: {e}", level="ERROR")

        # --- 3) reconcile (updates tokens + sources_pool-derived citations)
        program_history_reconciled = copy.deepcopy(program_history)
        sources_pool: List[Dict[str, Any]] = []
        try:
            rec = reconcile_citations_for_context(
                program_history_reconciled, max_sources=max_sources, rewrite_tokens_in_place=True
            )
            sources_pool = rec.get("sources_pool", [])
            self.log.log(f"[context_browser] sources pool: {len(sources_pool)}")
        except Exception as e:
            self.log.log(f"[context_browser] reconcile error: {e}", level="ERROR")

        return ContextBundle(
            program_history=program_history,
            program_history_reconciled=program_history_reconciled,
            sources_pool=sources_pool,
            # last_mat_working_canvas=last_mat_working_canvas,
        )

    async def search(
            self,
            *,
            targets: List[dict],
            user: str,
            conv: str,
            track: str,
            top_k: int = 5,
            days: int = 365,
            half_life_days: float = 7.0,
            scoring_mode: str = "hybrid",
            sim_weight: float = 0.8,
            rec_weight: float = 0.2,
            custom_score_fn: Optional[Any] = None,
            with_payload: bool = False,
            conv_idx: Optional[Any] = None,
            ctx_client: Optional[ContextRAGClient] = None,
            model_service: Optional[Any] = None,
    ) -> tuple[Optional[str], List[dict]]:
        """
        Convenience wrapper around ctx_rag.search_context.
        """
        ctx_client = ctx_client or self.ctx_client
        conv_idx = conv_idx or (getattr(ctx_client, "idx", None) if ctx_client else None)
        model_service = model_service or (getattr(ctx_client, "model_service", None) if ctx_client else None)
        if not conv_idx or not model_service:
            raise ValueError("ContextBrowser.search requires conv_idx and model_service.")
        return await search_context(
            conv_idx=conv_idx,
            ctx_client=ctx_client,
            model_service=model_service,
            targets=targets,
            user=user,
            conv=conv,
            track=track,
            top_k=top_k,
            days=days,
            half_life_days=half_life_days,
            scoring_mode=scoring_mode,
            sim_weight=sim_weight,
            rec_weight=rec_weight,
            custom_score_fn=custom_score_fn,
            with_payload=with_payload,
            logger=self.log,
        )

    async def save_artifact(
            self,
            *,
            kind: str,
            tenant: str,
            project: str,
            user_id: str,
            conversation_id: str,
            user_type: str,
            turn_id: str,
            track_id: Optional[str],
            content: dict,
            content_str: Optional[str] = None,
            meta: Optional[Dict[str, Any]] = None,
            extra_tags: Optional[List[str]] = None,
            bundle_id: Optional[str] = None,
            index_only: bool = False,
            store_only: bool = False,
            embedding: Optional[List[float]] = None,
            ttl_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Proxy to ContextRAGClient.save_artifact(...).
        """
        if not self.ctx_client:
            raise ValueError("ContextBrowser.save_artifact requires ctx_client.")
        return await self.ctx_client.save_artifact(
            kind=kind,
            tenant=tenant,
            project=project,
            user_id=user_id,
            conversation_id=conversation_id,
            user_type=user_type,
            turn_id=turn_id,
            track_id=track_id,
            content=content,
            content_str=content_str,
            meta=meta,
            extra_tags=extra_tags,
            bundle_id=bundle_id,
            index_only=index_only,
            store_only=store_only,
            embedding=embedding,
            ttl_days=ttl_days,
        )

    def make_react_context(
            self,
            *,
            bundle: ContextBundle,
            scratchpad: TurnScratchpad,
            user_id: Optional[str],
            conversation_id: Optional[str],
            turn_id: Optional[str],
            bundle_id: Optional[str]
    ) -> ReactContext:
        """
        Convert a ContextBundle into a fully prepared ReactContext, including:
          - prior_turns
          - sources_pool seeded with sources_pool
          - SOURCE_ID_CV initialization
          - turn meta (ids, user text, start ts)
        """
        ctx = ReactContext(history_turns=copy.deepcopy(bundle.program_history_reconciled),
                           scratchpad=scratchpad)

        # Build from reconciled history
        for rec_turn in (bundle.program_history_reconciled or []):
            try:
                execution_id, meta = next(iter(rec_turn.items()))
                deliverables: Dict[str, Dict[str, Any]] = {}
                for d_item in (meta.get("deliverables") or []):
                    slot = d_item.get("slot")
                    if not slot:
                        continue
                    deliverables[slot] = d_item

                turn_sources_pool = meta.get("sources_pool")
                if not turn_sources_pool and isinstance(meta.get("turn_log"), dict):
                    turn_sources_pool = meta["turn_log"].get("sources_pool")
                t_id = meta.get("turn_id")
                turn_log = meta.get("turn_log") or {}
                turn_user = (turn_log.get("user") if isinstance(turn_log, dict) else None) or {}
                turn_assistant = (turn_log.get("assistant") if isinstance(turn_log, dict) else None) or {}
                ctx.prior_turns[t_id] = {
                    "turn_id": t_id,
                    "ts": (meta.get("ts") or ""),
                    "user": turn_user,
                    "assistant": turn_assistant,
                    "project_log": (meta.get("project_log") or {}),
                    "solver_failure": meta.get("solver_failure", ""),
                    "deliverables": deliverables,
                    "turn_log": turn_log,
                    "sources_pool": turn_sources_pool or [],
                }
            except Exception as e:
                self.log.log(f"[context_browser] react_context parse error: {e}", level="ERROR")

        # Seed source pool with canonical sources if present
        if bundle.sources_pool:
            try:
                ctx.set_sources_pool(bundle.sources_pool, persist=False)
            except Exception:
                ctx.sources_pool = bundle.sources_pool
            try:
                ctx.max_sid = max(
                    int(s.get("sid") or 0) for s in bundle.sources_pool if isinstance(s, dict)
                )
            except Exception:
                ctx.max_sid = 0
        else:
            try:
                ctx.seed_sources_pool_from_prior()
            except Exception as e:
                self.log.log(f"[context_browser] seed pool error: {e}", level="ERROR")

        # Keep SOURCE_ID_CV in sync
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
            val = ctx.max_sid
            try:
                next_sid = int(val) + 1
            except (TypeError, ValueError):
                next_sid = 0
            SOURCE_ID_CV.set({"next": next_sid})
            self.log.log(f"[context_browser] SOURCE_ID_CV initialized to {ctx.max_sid}")
        except Exception as e:
            self.log.log(f"[context_browser] failed to set SOURCE_ID_CV: {e}", level="ERROR")

        # Basic ids + user text
        ctx.turn_id = getattr(scratchpad, "turn_id", None) or turn_id
        ctx.conversation_id = conversation_id
        ctx.user_id = user_id
        ctx.user_text = getattr(scratchpad, "user_text", "")
        ctx.user_input_summary = getattr(scratchpad, "user_input_summary", "")
        ctx.user_attachments = list(getattr(scratchpad, "user_attachments", None) or [])
        ctx.started_at = getattr(scratchpad, "started_at", None)
        ctx.bundle_id = bundle_id
        ctx.context_bundle = bundle

        return ctx

    async def rehost_previous_files(self,
                                    bundle: ContextBundle,
                                    workdir: pathlib.Path,
                                    ctx: str):
        """
        Rehost prior files referenced in history to workdir, organized by turn.
        """
        import kdcube_ai_app.apps.chat.sdk.runtime.solution.solution_workspace as solution_workspace

        try:
            for turn in (bundle.program_history_reconciled or bundle.program_history or []):
                if not isinstance(turn, dict):
                    continue
                turn_program = next(iter(turn.values()), {})
                if turn_program:
                    # Extract turn_id for directory naming
                    turn_id = turn_program.get("turn_id", "unknown_turn")

                    deliverables = turn_program.get("deliverables") or []
                    file_delivs = [
                        d for d in deliverables
                        if (d.get("value") or {}).get("type") == "file"
                    ]

                    turn_log = turn_program.get("turn_log") or {}
                    assistant_obj = turn_log.get("assistant") if isinstance(turn_log.get("assistant"), dict) else {}
                    assistant_files = assistant_obj.get("files") or []
                    assistant_file_delivs: list[dict] = []
                    for f in assistant_files:
                        if not isinstance(f, dict):
                            continue
                        key = (f.get("key") or "").strip()
                        hosted_uri = (f.get("hosted_uri") or "").strip()
                        path = key or hosted_uri
                        if not path:
                            continue
                        assistant_file_delivs.append({
                            "value": {
                                "type": "file",
                                "path": path,
                                "mime": (f.get("mime") or "").strip(),
                                "filename": (f.get("filename") or "").strip(),
                                "text": (f.get("text") or ""),
                                "summary": (f.get("summary") or "").strip(),
                            }
                        })

                    all_files = [*file_delivs, *assistant_file_delivs]
                    if all_files:
                        await solution_workspace.rehost_previous_files(
                            all_files,
                            workdir,
                            turn_id=turn_id  # ← Pass turn_id
                        )
                        self.log.log(
                            f"[{ctx}] Rehosted {len(all_files)} files from turn {turn_id} "
                            f"to {turn_id}/ subdirectory"
                        )
        except Exception as e:
            self.log.log(
                f"[{ctx}] Warning: Failed to rehost previous files: {e}",
                level="WARNING"
            )

    async def rehost_previous_attachments(
            self,
            bundle: ContextBundle,
            workdir: pathlib.Path,
            ctx: str,
    ) -> None:
        """
        Rehost user attachments referenced in history to workdir, organized by turn.
        """
        import kdcube_ai_app.apps.chat.sdk.runtime.solution.solution_workspace as solution_workspace

        try:
            for turn in (bundle.program_history_reconciled or bundle.program_history or []):
                if not isinstance(turn, dict):
                    continue
                turn_program = next(iter(turn.values()), {})
                if not turn_program:
                    continue
                turn_log = turn_program.get("turn_log") or {}
                turn_id = turn_program.get("turn_id", "unknown_turn")
                user = turn_log.get("user") or turn_program.get("user") or {}
                attachments = user.get("attachments") or []
                if not attachments:
                    continue
                rehosted = await solution_workspace.rehost_previous_attachments(
                    attachments,
                    workdir,
                    turn_id=turn_id,
                )
                user["attachments"] = rehosted
                turn_program["user"] = user
                turn_log["user"] = user
                turn_program["turn_log"] = turn_log
                self.log.log(
                    f"[{ctx}] Rehosted {len(attachments)} attachments from turn {turn_id} "
                    f"to {turn_id}/attachments/ subdirectory"
                )
        except Exception as e:
            self.log.log(
                f"[{ctx}] Warning: Failed to rehost previous attachments: {e}",
                level="WARNING"
            )
