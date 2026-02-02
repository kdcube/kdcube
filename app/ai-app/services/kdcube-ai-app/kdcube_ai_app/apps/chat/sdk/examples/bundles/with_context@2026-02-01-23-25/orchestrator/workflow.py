# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# examples/bundles/with_context@YYYY-MM-DD-hh-mm/orchestrator/workflow.py

from typing import Dict, Any, List, Optional

from langchain_core.messages import HumanMessage, AIMessage

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.context.graph.graph_ctx import GraphCtx
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.scratchpad import CTurnScratchpad, TurnView
from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.turn_summary_generator import stream_turn_summary
from kdcube_ai_app.apps.chat.sdk.util import _tstart, _tend, _jd
from kdcube_ai_app.apps.chat.sdk.runtime.solution.gate.gate_contract import gate_ctx_queries
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context import journal as ctx_presentation_module
from kdcube_ai_app.apps.chat.sdk.context.retrieval.documenting import _messages_with_context
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers

from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, Config

from ..agents.gate import gate_stream, GateOut as MinimalGateOut
from ..agents.final_answer_generator import stream_final_answer


class MinimalContextWorkflow(BaseWorkflow):
    def __init__(
        self,
        *,
        conv_idx: ConvIndex,
        graph: GraphCtx,
        kb: KBClient,
        store: ConversationStore,
        comm: ChatCommunicator,
        model_service: ModelServiceBase,
        conv_ticket_store: ConvTicketStore,
        config: Config,
        comm_context: ChatTaskPayload,
        ctx_client: Any = None,
    ):
        super().__init__(
            conv_idx=conv_idx,
            graph=graph,
            kb=kb,
            store=store,
            comm=comm,
            model_service=model_service,
            conv_ticket_store=conv_ticket_store,
            config=config,
            comm_context=comm_context,
            ctx_client=ctx_client,
        )
        TurnView.gate_out_class = MinimalGateOut

    async def construct_turn_and_scratchpad(self, payload: dict) -> CTurnScratchpad:
        scratchpad = await super().construct_turn_and_scratchpad(payload)
        scratchpad.gate_out_class = MinimalGateOut
        return scratchpad

    async def process(self, payload: dict) -> Dict[str, Any]:
        scratchpad = await self.construct_turn_and_scratchpad(payload)
        await self.start_turn(scratchpad)

        try:
            # --- 1) Retrospective view (baseline, last N turns)
            t1, ms1 = _tstart()
            scratchpad.guess_ctx_str, scratchpad.guess_ctx = await ctx_presentation_module.retrospective_context_view(
                ctx_client=self.ctx_client,
                user_id=self._ctx["service"]["user"],
                conversation_id=self._ctx["conversation"]["conversation_id"],
                turn_id=self._ctx["conversation"]["turn_id"],
                last_turns=3,
                turn_view_class=TurnView,
                scratchpad=scratchpad,
            )
            timing_ctx = _tend(t1, ms1)
            scratchpad.timings.append({"title": "context.retrospective", "elapsed_ms": timing_ctx["elapsed_ms"]})

            # --- 2) Gate agent (context queries only)
            t2, ms2 = _tstart()

            gate_payload, gate_channels = await gate_stream(
                self.model_service,
                user_text=scratchpad.user_text,
                attachments_summary=self._attachments_summary_text(scratchpad),
                retrospective_context=scratchpad.guess_ctx_str,
                timezone=self.comm_context.user.timezone,
                is_new_conversation=bool(getattr(scratchpad, "is_new_conversation", False)),
                on_thinking_delta=self.mk_thinking_streamer("gate"),
            )
            try:
                scratchpad.gate = MinimalGateOut.model_validate(gate_payload)
            except Exception:
                scratchpad.gate = MinimalGateOut()
            scratchpad.register_agentic_response("gate", gate_payload)
            scratchpad.register_agentic_response("gate.channels", gate_channels)
            logging_helpers.log_agent_packet(
                "gate",
                "gate",
                {
                    "user_thinking": gate_channels.get("thinking"),
                    "agent_response": gate_payload,
                },
            )
            logging_helpers.log_stream_channels("gate", "channels", gate_channels)

            # Uses BaseWorkflow to persist + emit conversation title on first turn.
            await self._process_prefs_and_policy(scratchpad=scratchpad, pre_out=gate_payload)

            timing_gate = _tend(t2, ms2)
            scratchpad.timings.append({"title": "gate", "elapsed_ms": timing_gate["elapsed_ms"]})
            await self._emit({
                "type": "chat.step",
                "agent": "gate",
                "step": "gate",
                "status": "completed",
                "title": "Gate",
                "data": gate_payload,
                "timing": timing_gate,
            })

            # --- 3) Context search using gate queries
            t3, ms3 = _tstart()
            targets = gate_ctx_queries(gate_payload)
            best_tid, hits = await self.ctx_browser.search(
                targets=targets,
                user=self._ctx["service"]["user"],
                conv=self._ctx["conversation"]["conversation_id"],
                track=self._ctx["conversation"]["track_id"],
                scoring_mode="hybrid",
                half_life_days=7.0,
                top_k=5,
                days=365,
                with_payload=True,
            )

            search_hits_formatted = []
            hit_turn_ids: List[str] = []
            hit_queries_by_turn_id: Dict[str, List[str]] = {}
            for h in hits:
                try:
                    tv = TurnView.from_turn_dict(h, gate_out_class=MinimalGateOut)
                    compressed_text = tv.to_compressed_search_view(include_turn_info=False)
                    search_hits_formatted.append({
                        "turn_id": h.get("turn_id"),
                        "text": compressed_text,
                        "score": h.get("score"),
                        "sim_score": h.get("sim"),
                        "recency_score": h.get("rec"),
                        "matched_via_role": h.get("matched_via_role"),
                        "source_query": h.get("source_query"),
                        "ts": h["ts"].isoformat() if hasattr(h.get("ts"), "isoformat") else h.get("ts"),
                    })
                except Exception:
                    continue
                tid = h.get("turn_id")
                if tid and tid not in hit_turn_ids:
                    hit_turn_ids.append(tid)
                q = (h.get("source_query") or "").strip() or "(unspecified)"
                if tid:
                    hit_queries_by_turn_id.setdefault(tid, [])
                    if q not in hit_queries_by_turn_id[tid]:
                        hit_queries_by_turn_id[tid].append(q)

            timing_search = _tend(t3, ms3)
            scratchpad.timings.append({"title": "context.search", "elapsed_ms": timing_search["elapsed_ms"]})

            # --- 4) Retrospective view with search hints
            scratchpad.guess_ctx_str, scratchpad.guess_ctx = await ctx_presentation_module.retrospective_context_view(
                ctx_client=self.ctx_client,
                user_id=self._ctx["service"]["user"],
                conversation_id=self._ctx["conversation"]["conversation_id"],
                turn_id=self._ctx["conversation"]["turn_id"],
                last_turns=3,
                recommended_turn_ids=hit_turn_ids,
                context_hit_queries=hit_queries_by_turn_id,
                scratchpad=scratchpad,
                turn_view_class=TurnView,
            )

            # --- 5) Context reconciler (no buckets)
            from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_reconciler import ctx_reconciler_stream

            t4, ms4 = _tstart()
            rr = await ctx_reconciler_stream(
                self.model_service,
                guess_package_json=_jd({
                    "turn_memories": [],
                    "last_turns_details": (scratchpad.guess_ctx or {}).get("last_turns_mate", []),
                    "current_turn_details": (scratchpad.guess_ctx or {}).get("current_turn_details"),
                }),
                current_context_str=scratchpad.guess_ctx_str,
                search_hits_json=_jd(search_hits_formatted),
                bucket_cards_json="[]",
                limit_ctx=6,
                max_buckets=0,
                gate_decision=gate_payload,
                timezone=self.comm_context.user.timezone,
                on_thinking_delta=self.mk_thinking_streamer("ctx.reconciler"),
            )
            logging_helpers.log_agent_packet("ctx.reconciler", "ctx.reconciler", rr)
            rro = rr.get("agent_response") or {}
            scratchpad.register_agentic_response("ctx.reconciler", rro)

            if rro.get("user_input_summary"):
                scratchpad.user_input_summary = (rro.get("user_input_summary") or "").strip()
            if rro.get("objective"):
                scratchpad.objective = (rro.get("objective") or "").strip()
                scratchpad.tlog.objective(scratchpad.objective)

            prefs_assertions = list(rro.get("assertions") or [])
            prefs_exceptions = list(rro.get("exceptions") or [])
            prefs_facts = list(rro.get("facts") or [])
            if prefs_assertions or prefs_exceptions:
                scratchpad.extracted_prefs = {
                    "assertions": prefs_assertions,
                    "exceptions": prefs_exceptions,
                }
                scratchpad.tlog.prefs({
                    "assertions": prefs_assertions,
                    "exceptions": prefs_exceptions,
                })
            if prefs_facts:
                scratchpad.proposed_facts = prefs_facts

            # persist user message (index)
            await self.persist_user_message(scratchpad)

            timing_rr = _tend(t4, ms4)
            scratchpad.timings.append({"title": "ctx.reconciler", "elapsed_ms": timing_rr["elapsed_ms"]})
            await self._emit({
                "type": "chat.step",
                "agent": "ctx.reconciler",
                "step": "ctx.reconciler",
                "status": "completed",
                "title": "Ctx Reconciler",
                "data": rro,
                "timing": timing_rr,
            })

            # --- 6) Materialize selected turns (if any)
            turn_ids = list((rro.get("turn_ids") or [])[:6])
            scratchpad.materialize_turn_ids = turn_ids
            if turn_ids:
                t5, ms5 = _tstart()
                bundle = await self.ctx_browser.materialize(
                    materialize_turn_ids=turn_ids,
                    user_id=self._ctx["service"]["user"],
                    conversation_id=self._ctx["conversation"]["conversation_id"],
                )
                scratchpad.context_bundle = bundle
                scratchpad.sources_pool = bundle.sources_pool or []
                timing_materialize = _tend(t5, ms5)
                scratchpad.timings.append({"title": "context.materialize", "elapsed_ms": timing_materialize["elapsed_ms"]})

            # --- 7) Answer generation (multi-channel)
            t6, ms6 = _tstart()

            async def _emit_delta(**kwargs):
                channel = kwargs.pop("channel", None)
                text = kwargs.get("text") or ""
                completed = bool(kwargs.get("completed"))
                if channel == "thinking":
                    await self._emit_thinking_delta(agent="answer.generator.simple", text=text, completed=completed)
                elif channel == "answer":
                    await self._emit_answer_delta(text=text, completed=completed, agent="answer.generator.simple")

            sys_prompt = (
                "You are a simple, helpful assistant.\n"
                "Use provided context only when it is relevant to the user's question.\n"
                "If context is insufficient, ask a brief clarifying question.\n"
                "Respond directly and succinctly.\n\n"
                "Output protocol (strict):\n"
                "<channel:thinking> ... </channel:thinking>\n"
                "<channel:answer> ... </channel:answer>\n"
                "<channel:followup> {\"followups\": [ ...list of string... ] } </channel:followup>\n"
                "FOLLOWUP may be empty: {\"followups\": []}.\n\n"
                f"User timezone: {self.comm_context.user.timezone}\n"
            )

            messages_prepared = _messages_with_context(
                system_message=sys_prompt,
                prior_pairs=[],
                current_user_text=scratchpad.user_text,
                current_context_items=[{
                    "type": "text",
                    "title": "Retrospective Context",
                    "content": scratchpad.guess_ctx_str,
                }],
                turn_artifact={},
                current_turn_id=scratchpad.turn_id,
                current_user_blocks=scratchpad.user_blocks,
                current_user_attachments=scratchpad.user_attachments,
                attachment_mode=None,
            )

            answer, followups, thinking, answer_channels = await stream_final_answer(
                self.model_service,
                user_text=scratchpad.user_text,
                attachments_summary=self._attachments_summary_text(scratchpad),
                retrospective_context=scratchpad.guess_ctx_str,
                timezone=self.comm_context.user.timezone,
                emit_delta=_emit_delta,
                prepared_messages=messages_prepared,
            )
            scratchpad.answer = answer
            scratchpad.final_internal_thinking = thinking
            scratchpad.user_shortcuts = followups
            scratchpad.register_agentic_response("answer.generator.channels", answer_channels)
            logging_helpers.log_agent_packet(
                "answer.generator",
                "answer.generator",
                {
                    "user_thinking": answer_channels.get("thinking"),
                    "agent_response": {
                        "answer": answer,
                        "followups": followups,
                    },
                },
            )
            logging_helpers.log_stream_channels("answer.generator", "channels", answer_channels)
            if followups:
                await self._comm.followups(followups, agent="answer.generator.simple")

            timing_answer = _tend(t6, ms6)
            scratchpad.timings.append({"title": "answer.generator", "elapsed_ms": timing_answer["elapsed_ms"]})
            await self._emit({
                "type": "chat.step",
                "agent": "answer.generator",
                "step": "chat.assistant.done",
                "status": "completed",
                "title": "Answer Streaming Complete",
                "data": {"answer": answer},
                "timing": timing_answer,
            })
            self.logger.log(f"[Final.Answer]\n{answer}", "INFO")

            # --- 8) Turn summary
            t7, ms7 = _tstart()
            summary, internal = await stream_turn_summary(
                svc=self.model_service,
                context_messages=[
                    HumanMessage(content=scratchpad.user_text),
                    AIMessage(content=answer or ""),
                ],
                assistant_answer=answer or "",
                timezone=self.comm_context.user.timezone,
            )
            scratchpad.turn_summary = summary
            scratchpad.final_internal_thinking = internal or scratchpad.final_internal_thinking

            timing_summary = _tend(t7, ms7)
            scratchpad.timings.append({"title": "turn.summary", "elapsed_ms": timing_summary["elapsed_ms"]})

            # --- finish
            await self.finish_turn(scratchpad, ok=True)
            return {"answer": answer, "followups": followups}

        except Exception as e:
            await self._handle_turn_exception(e, scratchpad)
            raise
