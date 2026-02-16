# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/solutions/chatbot/base_workflow.py

import os, time, datetime, json, re
import pathlib
import random
import traceback
from typing import Dict, Any, List, Optional, Type, Callable, Awaitable

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.comm.emitters import AIBEmitters
from kdcube_ai_app.apps.chat.sdk.context.memory.conv_memories import ConvMemoriesStore
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.browser import ContextBrowser

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_index import ConvTicketIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore, Ticket
from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import subject_id_of
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.turn_reporting import _format_ms_table, _format_ms_table_markdown
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad, TurnPhaseError
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.gate.gate_contract import GateOut
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.runtime import ReactSolverV2
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.turn_log import TurnLog
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.conversation_turn_work_status import \
    ConversationTurnWorkStatus
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import create_tool_subsystem_with_mcp, ToolSubsystem
from kdcube_ai_app.apps.chat.sdk.runtime.user_inputs import ingest_user_attachments
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import CTurnScratchpad
from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import SkillsSubsystem
from kdcube_ai_app.apps.chat.sdk.util import (truncate_text_by_tokens, _to_jsonable,
                                              ensure_event_markdown, _to_json_safe, _jd,  _now_ms,
                                              _tstart, _tend)
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError, is_context_limit_error

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger, ModelServiceBase, Config, _mid
from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import ApplicationHostingService
from kdcube_ai_app.apps.chat.sdk.context.graph.graph_ctx import GraphCtx
from kdcube_ai_app.apps.chat.sdk.runtime.user_inputs import (
    attachment_summary_index_text,
)

# ---------- small utilities ----------

def _ttl_for(user_type: str, requested: int) -> int:
    ttl_map = {
        "anonymous": 1,
        "registered": 7,
        "privileged": 90,
        "paid": 365
    }
    hard = ttl_map.get((user_type or "anonymous").lower(), 7)
    return min(requested, hard)

def _norm_topic(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")

# ---------- Orchestrator ----------

class BaseWorkflow():

    def __init__(self,
                 conv_idx: ConvIndex,
                 kb: KBClient,
                 store: ConversationStore,
                 comm: ChatCommunicator,
                 model_service: ModelServiceBase,
                 conv_ticket_store: ConvTicketStore,
                 config: Config,
                 comm_context: ChatTaskPayload,
                 ctx_client: Any = None,
                 message_resources_fn: Optional[Callable[[str, bool], str]] = None,
                 gate_out_class: Optional[Type] = None,
                 answer_system_prompt: Optional[str] = None,
                 graph: GraphCtx = None):

        self.graph = graph
        self.kb = kb
        self.comm = comm
        self._comm = AIBEmitters(comm)
        self.comm_context = comm_context

        self.model_service = model_service
        self.store = store
        self.conv_idx = conv_idx

        self.conv_ticket_store = conv_ticket_store
        self.ticket_index = ConvTicketIndex(conv_ticket_store)
        self.logger = AgentLogger("base.workflow")

        self._ctx = {}

        # do not reorder these initializations below
        self.config = config
        self.ctx_client = ctx_client or ContextRAGClient(conv_idx=self.conv_idx,
                                                        store=self.store,
                                                        model_service=self.model_service,)

        self.gate_out_class = gate_out_class or GateOut

        self.hosting_service = ApplicationHostingService(
            store=self.store,
            comm=self.comm,
            logger=self.logger,
        )

        self.conv_memories = ConvMemoriesStore(self.graph)
        if self.ctx_client:
            self.conv_memories.bind_ctx_client(self.ctx_client)
        self.turn_status = ConversationTurnWorkStatus(
            emit_delta=self.comm.delta,
            agent="orchestrator",
        )
        self._thinking_delta_idx: Dict[str, int] = {}
        self._answer_delta_idx: int = 0

        self.message_resources_fn = message_resources_fn or (lambda err_code, fallback=None: None)
        self.answer_system_prompt = answer_system_prompt
        # Runtime context + context browser are constructed once per workflow instance
        try:
            self.runtime_ctx = RuntimeCtx(
                tenant=self.comm_context.actor.tenant_id,
                project=self.comm_context.actor.project_id,
                user_id=self.comm_context.user.user_id,
                user_type=self.comm_context.user.user_type,
                timezone=self.comm_context.user.timezone,
                conversation_id=self.comm_context.routing.conversation_id,
                turn_id=self.comm_context.routing.turn_id,
                bundle_id=self.config.ai_bundle_spec.id,
                max_tokens=getattr(self.config, "max_tokens", None),
            )
            self.ctx_browser = ContextBrowser(
                ctx_client=self.ctx_client,
                logger=self.logger,
                model_service=self.model_service,
                runtime_ctx=self.runtime_ctx,
            )
        except Exception:
            self.runtime_ctx = RuntimeCtx()
            self.ctx_browser = ContextBrowser(
                ctx_client=self.ctx_client,
                logger=self.logger,
                model_service=self.model_service,
                runtime_ctx=self.runtime_ctx,
            )

    # ---------- Comm ----------

    async def _emit(self, evt: Dict[str, Any]):
        raw = evt.get("data") or {}
        data = _to_jsonable(raw)
        await self.comm.event(
            agent=evt.get("agent"),
            type=evt.get("type","chat.step"),
            route="chat.step",
            title=evt.get("title"),
            step=evt.get("step","event"),
            data=data,
            markdown=evt.get("markdown"),
            status=evt.get("status","update"),
            broadcast=evt.get("broadcast", False),
        )

    def _envelope(self, evt: Dict[str, Any]) -> Dict[str, Any]:
        et = (evt.get("type") or "chat.step").strip()
        # ensure markdown for non-deltas
        if et != "chat.assistant.delta":
            try:
                ensure_event_markdown(evt)  # populates evt["markdown"] if missing
            except Exception:
                pass

        env: Dict[str, Any] = {
            "type": et,
            "ts": evt.get("ts") or _now_ms(),
            "service": dict(self._ctx.get("service") or {}),
            "conversation": dict(self._ctx.get("conversation") or {}),
            "event": {
                "agent": evt.get("agent"),
                "step": evt.get("step"),
                "status": evt.get("status") or "update",
                "title": evt.get("title"),
                "markdown": evt.get("markdown"),
                "timing": evt.get("timing") or {},
            },
            "data": evt.get("data") or {}
        }

        # delta-specific block
        if et in ("chat.assistant.delta", "chat.delta"):
            txt = (evt.get("text") or "").rstrip("\0")
            marker = evt.get("marker") or "answer"
            env["delta"] = {"text": txt, "marker": marker}
            # back-compat mirrors (client may still read these)
            env["text"] = txt

        # keep a safe version
        return _to_json_safe(env)

    async def emit_conversation_title(self, conversation_id: str, turn_id: str, title: str) -> None:
        """
        Emits a chat event for conversation title update.
        """
        if title:
            await self._emit({
                "type": "chat.conversation.title",
                "agent": "system",
                "step": "conversation_title",
                "status": "completed",
                "title": "Conversation Title Updated",
                "data": {
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "title": title
                },
                "broadcast": True
            })

    async def _emit_agent_error(self, *, origin: str, err: Exception, step: str, extra: Optional[dict] = None):
        """
        Emit a chat.error event to the client with JSON-serializable payload.
        `origin` is the logical agent name (e.g. "gate", "ctx.reconciler", "answer_generator").
        `step` must be unique within the workflow (e.g. "gate.service_error").
        """
        err_info = {
            "origin": origin,
            "type": err.__class__.__name__,
            "message": str(err),
        }
        # traceback is useful but purely diagnostic; if you don't want it on the wire, drop it
        try:
            err_info["traceback"] = traceback.format_exc()
        except Exception:
            pass

        if extra:
            err_info["extra"] = extra

        await self._emit({
            "type": "chat.error",
            "agent": origin,
            "step": step,
            "status": "error",
            "title": f"{origin} failed",
            "data": err_info,
        })

    async def emit_suggested_followups(self, suggested_followups: Optional[list[str]] = None):
        if not suggested_followups:
            return
        await self._emit({"type": "chat.followups", "agent": "answer.generator", "step": "followups",
                          "status": "completed", "title": "Follow-ups: User Shortcuts", "data": {"items": suggested_followups}})

    async def _persist_attachment_summaries(self, scratchpad) -> None:
        attachments = getattr(scratchpad, "user_attachments", None) or []
        if not attachments:
            return
        for a in attachments:
            if not isinstance(a, dict):
                continue
            if a.get("summary_persisted"):
                continue
            summary = (a.get("summary") or "").strip()
            if not summary:
                continue
            filename = (a.get("filename") or "attachment").strip()
            artifact_name = (a.get("artifact_name") or "").strip()
            payload = {
                "summary": summary,
                "text": (a.get("text") or "").strip(),
                "filename": filename,
                "artifact_name": artifact_name,
                "mime": (a.get("mime") or a.get("mime_type") or "").strip(),
                "size": a.get("size") or a.get("size_bytes"),
                "rn": a.get("rn"),
                "hosted_uri": a.get("hosted_uri") or a.get("source_path") or a.get("path"),
                "key": a.get("key"),
            }
            if self.ctx_client:
                content_str = attachment_summary_index_text(payload) if attachment_summary_index_text else str(payload)[:1000]
                embedding = None
                if self.model_service:
                    try:
                        [embedding] = await self.model_service.embed_texts([summary])
                    except Exception:
                        embedding = None
                await self.ctx_browser.save_artifact(
                    kind="user.attachment",
                    tenant=self.runtime_ctx.tenant,
                    project=self.runtime_ctx.project,
                    user_id=self.runtime_ctx.user_id,
                    conversation_id=self.runtime_ctx.conversation_id,
                    user_type=self.runtime_ctx.user_type,
                    turn_id=self.runtime_ctx.turn_id,
                    content=payload,
                    content_str=content_str,
                    embedding=embedding,
                    ttl_days=_ttl_for(self.runtime_ctx.user_type, 365),
                    meta={
                        "title": f"User Attachment Summary: {filename}",
                        "kind": "user.attachment",
                        "request_id": self._ctx["service"]["request_id"],
                    },
                    bundle_id=self.config.ai_bundle_spec.id,
                )
            a["summary_persisted"] = True

    def _topics_from_summary(self, summary: dict) -> List[str]:
        domain = (summary.get("domain") or "").strip()
        if not domain:
            return []
        return [d.strip() for d in domain.split(";") if d.strip()]

    def _merge_topics(self, primary: List[str], secondary: List[str]) -> List[str]:
        out = []
        for t in (primary or []) + (secondary or []):
            t = (t or "").strip()
            if t and t not in out:
                out.append(t)
        return out

    def _prefs_from_summary(self, summary: dict) -> tuple[List[dict], List[dict]]:
        prefs = summary.get("prefs") or {}
        assertions = []
        for a in (prefs.get("assertions") or []):
            key = a.get("key")
            if not key:
                continue
            entry = {
                "key": key,
                "value": a.get("value"),
                "severity": a.get("severity") or "prefer",
            }
            if a.get("scope"):
                entry["scope"] = a.get("scope")
            if a.get("applies_to"):
                entry["applies_to"] = a.get("applies_to")
            assertions.append(entry)
        exceptions = []
        for e in (prefs.get("exceptions") or []):
            key = e.get("key")
            if not key:
                continue
            entry = {
                "key": key,
                "value": e.get("value"),
                "severity": e.get("severity") or "avoid",
            }
            if e.get("scope"):
                entry["scope"] = e.get("scope")
            if e.get("applies_to"):
                entry["applies_to"] = e.get("applies_to")
            exceptions.append(entry)
        return assertions, exceptions

    def _assistant_signals_from_summary(self, summary: dict) -> List[dict]:
        signals = []
        for s in (summary.get("assistant_signals") or []):
            key = (s.get("key") or "").strip()
            if not key:
                continue
            entry = {
                "key": key,
                "value": s.get("value"),
            }
            if s.get("severity"):
                entry["severity"] = s.get("severity")
            if s.get("scope"):
                entry["scope"] = s.get("scope")
            if s.get("applies_to"):
                entry["applies_to"] = s.get("applies_to")
            signals.append(entry)
        return signals

    async def _persist_stream_artifacts(self) -> None:
        if not self.ctx_client:
            return
        try:
            tenant = self.runtime_ctx.tenant
            project = self.runtime_ctx.project
            user_id = self.runtime_ctx.user_id
            user_type = self.runtime_ctx.user_type
            conversation_id = self.runtime_ctx.conversation_id
            turn_id = self.runtime_ctx.turn_id
        except Exception:
            return

        all_deltas = self.comm.get_delta_aggregates(
            conversation_id=conversation_id, turn_id=turn_id, merge_text=True
        )
        canvas_and_tools_blocks = [d for d in all_deltas if d.get("marker") in ["canvas", "tool", "subsystem"] and (d.get("text") or d.get("chunks"))]

        subsystem_blocks = [d for d in all_deltas if d.get("marker") in ["subsystem"] and (d.get("text") or d.get("chunks"))]

        canvas_full = [
            {**{k: v for k, v in item.items() if k != "chunks"},
             "chunks_num": len(item.get("chunks") or [])}
            for item in canvas_and_tools_blocks
        ]
        canvas_idx = [
            {**{k: v for k, v in item.items() if k not in ("text", "chunks")},
             "text_size": len(item.get("text") or ""),
             "chunks_num": len(item.get("chunks") or [])}
            for item in canvas_and_tools_blocks
        ]

        if canvas_and_tools_blocks:
            await self.ctx_browser.save_artifact(
                kind="conv.artifacts.stream",
                tenant=tenant, project=project,
                turn_id=turn_id,
                user_id=user_id,
                conversation_id=conversation_id,
                bundle_id=self.config.ai_bundle_spec.id,
                user_type=user_type,
                content={"version": "v1", "items": canvas_full},
                content_str=json.dumps(canvas_idx),
                extra_tags=["conversation", "stream", "canvas"],
            )

        self.comm.clear_delta_aggregates(conversation_id=conversation_id, turn_id=turn_id)

    async def _snapshot_execution_tree(
            self,
            *,
            outdir: Optional[str],
            workdir: Optional[str],
            tenant: str, project: str, user: str, conversation_id: str,
            user_type: str, turn_id: str, codegen_run_id: str
    ):
        snap = await self.store.put_execution_snapshot(
            tenant=tenant, project=project, user=user, fingerprint=None,
            conversation_id=conversation_id, turn_id=turn_id,
            out_dir=outdir, pkg_dir=workdir,
            codegen_run_id=codegen_run_id,
            user_type=user_type
        )
        return snap

    async def persist_user_message(self, scratch: CTurnScratchpad):

        tenant, project, user, user_type, request_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"]
        conversation_id, turn_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"]

        if getattr(scratch, "user_message_persisted", False):
            return

        # (5) persist + index the USER message
        t5a, ms5a = _tstart()
        truncated_text = truncate_text_by_tokens(scratch.user_text)
        [scratch.uvec] = await self.model_service.embed_texts([truncated_text])
        timing_user_embed = _tend(t5a, ms5a)

        step_title = "User message embedded"
        scratch.timings.append({"title": step_title, "elapsed_ms": timing_user_embed["elapsed_ms"]})

        t5, ms5 = _tstart()
        ts = self._ctx["conversation"]["ts"]
        msg_ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
        msgid_u = f"{_mid('user', msg_ts)}-conv.user"
        s3u = "index_only"
        rn_u = "index_only"
        await self.conv_idx.add_message(
            user_id=user,
            conversation_id=conversation_id,
            bundle_id=self.config.ai_bundle_spec.id,
            turn_id=turn_id,
            role="user",
            text=scratch.short_text,
            hosted_uri=s3u,
            ts=ts,
            tags=["chat:user", f"turn:{turn_id}"] + [f"topic:{t}" for t in scratch.turn_topics_plain or []],
            ttl_days=_ttl_for(user_type, 365),
            user_type=user_type,
            embedding=scratch.uvec,
            message_id=msgid_u,
        )
        timing_user_persist = _tend(t5, ms5)
        step_title = "User Message Persisted"
        await self._emit({"type": "chat.step", "agent": "store", "step": "conversation.persist.user_message",
                          "status": "completed", "title": step_title,
                          "data": {"hosted_uri": s3u, "message_id": msgid_u, "rn": rn_u},
                          "timing": timing_user_persist})
        scratch.timings.append({
            "title": step_title,
            "elapsed_ms": timing_user_persist["elapsed_ms"]
        })
        self.logger.log_step("user.persisted",
                             {"s3": s3u, "message_id": msgid_u,
                                   "embed_dim": len(scratch.uvec) if scratch.uvec else 0})
        scratch.user_message_persisted = True

    async def persist_assistant(self, scratchpad: TurnScratchpad):

        tenant, project, user, user_type, request_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"]
        conversation_id, turn_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"]

        t14, ms14 = _tstart()
        scratchpad.avec = (await self.model_service.embed_texts([scratchpad.answer]))[0] if scratchpad.answer else None
        answer_for_storage = (scratchpad.answer_raw or scratchpad.answer or "")

        msg_ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
        msgid_a = f"{_mid('assistant', msg_ts)}-conv.assistant"
        s3a = "index_only"
        rn_a = "index_only"
        await self.conv_idx.add_message(
            user_id=user, conversation_id=conversation_id,
            bundle_id=self.config.ai_bundle_spec.id,
            turn_id=turn_id,
            role="assistant", text=answer_for_storage, hosted_uri=s3a,
            ts=datetime.datetime.utcnow().isoformat() + "Z",
            tags=["chat:assistant", f"turn:{turn_id}"] + [f"topic:{t}" for t in scratchpad.turn_topics_plain or []],
            ttl_days=_ttl_for(user_type, 365),
            user_type=user_type,
            embedding=scratchpad.avec,
            message_id=msgid_a,
        )
        timing_assist_persist = _tend(t14, ms14)
        step_title = "Assistant Message Persisted"
        await self._emit({"type": "chat.step", "agent": "store", "step": "conversation.persist.assistant_message",
                          "status": "completed", "title": step_title,
                          "data": { "hosted_uri": s3a, "message_id": msgid_a, "rn": rn_a },
                          "timing": timing_assist_persist})
        scratchpad.timings.append({
            "title": step_title,
            "elapsed_ms": timing_assist_persist["elapsed_ms"]
        })

    async def _summarize_user_attachments(self, scratchpad: CTurnScratchpad) -> None:
        if not (scratchpad.user_attachments or []):
            return
        try:
            max_ctx_chars = int(os.getenv("ATTACHMENT_SUMMARY_MAX_CONTEXT_CHARS", "12000"))
        except Exception:
            max_ctx_chars = 12000
        try:
            max_tokens = int(os.getenv("ATTACHMENT_SUMMARY_MAX_TOKENS", "600"))
        except Exception:
            max_tokens = 600

        async with with_accounting(
                self.config.ai_bundle_spec.id,
                agent="attachment.summarizer",
                metadata={"agent": "attachment.summarizer"},
        ):
            from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.attachment_summary import (
                summarize_user_attachments_for_turn_log,
            )
            items = await summarize_user_attachments_for_turn_log(
                svc=self.model_service,
                user_text=scratchpad.user_text or "",
                user_attachments=list(scratchpad.user_attachments or []),
                max_ctx_chars=max_ctx_chars,
                max_tokens=max_tokens,
            )
            scratchpad.user_attachments = items

    async def handle_conversation_title(self, *, scratchpad: CTurnScratchpad, pre_out: dict):
        conversation_id = self.runtime_ctx.conversation_id

        # Conversation title now stored in timeline
        if scratchpad.is_new_conversation:
            conversation_title = (pre_out.get("conversation_title") or "").strip()
            scratchpad.conversation_title = conversation_title
            try:
                if self.ctx_browser and self.ctx_browser.timeline:
                    self.ctx_browser.timeline.set_conversation_title(conversation_title)
                    if not self.ctx_browser.timeline.conversation_started_at:
                        self.ctx_browser.timeline.set_conversation_started_at(self.runtime_ctx.started_at or "")
                    self.ctx_browser.timeline.write_local()
            except Exception:
                pass
        await self.emit_conversation_title(conversation_id=conversation_id, turn_id=self._ctx["conversation"]["turn_id"], title=scratchpad.conversation_title)

    async def handle_feedback(self, scratchpad: TurnScratchpad, gate):
        tenant, project, user, user_type = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"]
        conversation_id = self._ctx["conversation"]["conversation_id"]
        current_turn_id = scratchpad.turn_id  # Add this - the turn where feedback was given

        fb = gate.get("feedback") or {}
        self.logger.log(f"Feedback {fb}; conversation_id={conversation_id};")

        FEEDBACK_MIN_CONF = 0.70
        feedback_text = (fb.get("text") or "").strip()
        feedback_confidence = float(fb.get("confidence") or 0.0)
        reaction = fb.get("reaction")

        target_tid = fb.get("turn_id")
        match_targets = list((gate.get("feedback_match_targets") or []))


        if feedback_text and feedback_confidence >= FEEDBACK_MIN_CONF:
            try:
                # Custom scoring function that prioritizes similarity
                def feedback_scoring(sim: float, rec: float, ts: str) -> float:
                    # Prioritize similarity heavily, add small recency bias
                    return 0.85 * sim + 0.15 * rec

                target_tid, hits = await self.ctx_browser.search(
                    targets=match_targets,
                    user=user,
                    conv=conversation_id,
                    scoring_mode="custom",
                    custom_score_fn=feedback_scoring,
                    top_k=5,
                    days=365,
                    with_payload=True
                )
                hits = [{**h, "log_payload": h.get("payload") or {}} for h in hits]

                target_turn = next(iter([h for h in hits or [] if h["turn_id"] == target_tid]), None)

                # emit feedback block regardless of search success
                reaction_payload = {
                    "origin": "user",
                    "reaction": reaction,
                    "confidence": feedback_confidence,
                    "text": feedback_text,
                    "from_turn_id": target_tid,
                    "ts": datetime.datetime.utcnow().isoformat() + "Z",
                }
                self.ctx_browser.contribute_feedback(
                    reaction=reaction_payload,
                )

                if target_tid:
                    try:
                        self.logger.log(f"Feedback target turn: {target_turn or target_tid}; conversation_id={conversation_id};")
                        feedback_ts = datetime.datetime.utcnow().isoformat() + "Z"

                        # Build machine-inferred feedback (no reaction field for machine feedback)
                        scratchpad.detected_feedback = {
                            "turn_id": target_tid,
                            "text": feedback_text,
                            "confidence": feedback_confidence,
                            "reaction": reaction,
                            "ts": feedback_ts,
                            "origin": "machine"  # mark as machine-inferred
                        }

                        # 1) Log to current turn (where feedback was given) with origin="machine"
                        await self.ctx_client.append_reaction_to_turn_log(
                            turn_id=target_tid,
                            bundle_id=self.config.ai_bundle_spec.id,
                            reaction=scratchpad.detected_feedback,
                            tenant=tenant, project=project, user=user,
                            fingerprint=None, user_type=user_type,
                            conversation_id=conversation_id,
                            origin="machine",
                        )

                        # 2) Apply feedback to target turn log (update the actual turn)
                        await self.ctx_client.apply_feedback_to_turn_log(
                            tenant=tenant,
                            project=project,
                            user=user,
                            user_type=user_type,
                            conversation_id=conversation_id,
                            turn_id=target_tid,  # The turn being commented on
                            bundle_id=self.config.ai_bundle_spec.id,
                            feedback={
                                "text": feedback_text,
                                "confidence": feedback_confidence,
                                "ts": feedback_ts,
                                "from_turn_id": current_turn_id,  # Where the feedback came from
                                "origin": "machine",  # mark as machine-inferred
                                "reaction": reaction
                            }
                        )

                        # 3) Format details for logging
                        target_turn_details = ""
                        if target_turn:
                            ts = target_turn.get('ts')
                            # Handle both datetime objects and ISO strings
                            if isinstance(ts, str):
                                ts_str = ts[:16]
                            elif hasattr(ts, 'isoformat'):
                                ts_str = ts.isoformat()[:16]
                            else:
                                ts_str = str(ts)[:16] if ts else ""

                            if ts_str:
                                target_turn_details = f" originated on {ts_str}"
                        trace_ = (
                            f"{feedback_text} (confidence={feedback_confidence}; "
                            f"to turn {target_tid}{target_turn_details}; origin=machine)"
                        )
                        self.logger.log(f"Feedback applied. {trace_}; conversation_id={conversation_id};")

                    except Exception:
                        self.logger.log(traceback.format_exc(), "ERROR")
            except Exception:
                self.logger.log(traceback.format_exc(), "ERROR")

    # ------ streaming ---------
    async def _emit_turn_work_status(self, choices: List[str]) -> None:
        if not choices:
            return
        await self.turn_status.send(random.choice(choices))

    async def _emit_thinking_delta(self, *, agent: str, text: str, completed: bool = False) -> None:
        if not text and not completed:
            return
        idx = self._thinking_delta_idx.get(agent, 0)
        if text:
            self._thinking_delta_idx[agent] = idx + 1
        await self.comm.delta(
            text=text,
            index=idx,
            marker="thinking",
            agent=agent,
            completed=completed,
            format="text",
        )

    def mk_thinking_streamer(self, agent: str):
        async def _emit(text: str, completed: bool = False, **_):
            await self._emit_thinking_delta(agent=agent, text=text, completed=completed)
        return _emit

    async def _emit_answer_delta(self, *, text: str, completed: bool = False, agent: str = "answer.generator") -> None:
        if not text and not completed:
            return
        idx = self._answer_delta_idx
        if text:
            self._answer_delta_idx = idx + 1
        await self.comm.delta(
            text=text,
            index=idx,
            marker="answer",
            agent=agent,
            completed=completed,
            format="markdown",
        )
    # ------ end of streaming ---------

    def bundle_root(self):
        spec = self.config.ai_bundle_spec
        if spec and spec.module and spec.path:
            # This is how you compute bundle_root elsewhere
            bundle_root = pathlib.Path(spec.path).joinpath(
                "/".join(spec.module.split(".")[:-1])
            )
        else:
            # Fallback: directory above orchestrator/ (the bundle root)
            bundle_root = pathlib.Path(__file__).resolve().parents[1]
        return bundle_root

    def build_react(self,
                    scratchpad: TurnScratchpad,
                    mod_tools_spec: Optional[List[Dict[str, Any]]] = None,
                    mcp_tools_spec: Optional[List[Dict[str, Any]]] = None,
                    tools_runtime: Optional[Dict[str, str]] = None,
                    custom_skills_root: Optional[str] = None,
                    skills_visibility_agents_config: Optional[Dict[str, Dict[str, Any]]] = None) -> ReactSolverV2:

        bundle_root = self.bundle_root()

        async def _kb_proxy(query: str, top_n: int = 8, providers: Optional[List[str]] = None):
            vec = (await self.model_service.embed_texts([query]))[0]
            return await self.kb.hybrid_search(
                query=query, embedding=vec, top_n=top_n,
                include_expired=False, providers=(providers or None)
            )
        self.conv_memories.bind_ctx_client(self.ctx_client)
        if not custom_skills_root:
            candidate = bundle_root / "skills"
            if candidate.exists():
                custom_skills_root = candidate

        tool_subsystem, mcp_subsystem = create_tool_subsystem_with_mcp(
            service=self.model_service,
            comm=self.comm,
            logger=self.logger,
            bundle_spec=self.config.ai_bundle_spec,
            context_rag_client=self.ctx_client,
            registry={"kb_client": self.kb},
            raw_tool_specs=mod_tools_spec,
            tool_runtime=tools_runtime,
            mcp_tool_specs=mcp_tools_spec or [],
            mcp_env_json=os.environ.get("MCP_SERVICES") or "",
        )

        tools = tool_subsystem or ToolSubsystem(
            service=self.model_service,
            comm=self.comm,
            bundle_spec=self.config.ai_bundle_spec,
            logger=self.logger,
            context_rag_client=self.ctx_client,
            registry={
                "kb_client": self.kb
            },
            mcp_subsystem=mcp_subsystem,
            tool_runtime=tools_runtime
        )
        skills = SkillsSubsystem(
            descriptor={
                "custom_skills_root": str(custom_skills_root) if custom_skills_root else None,
                "agents_config": skills_visibility_agents_config,
            },
            bundle_root=bundle_root,
        )
        react = ReactSolverV2(
            service=self.model_service,
            logger=self.logger,
            tools_subsystem=tools,     # exposes .tools to React
            skills_subsystem=skills,
            comm=self.comm,
            comm_context=self.comm_context,
            hosting_service=self.hosting_service,
            ctx_browser=self.ctx_browser,
            scratchpad=scratchpad
        )
        return react

    # -------------------- Create solver --------------------
    async def report_timings(self, scratchpad: CTurnScratchpad, ms0u, total_ms):

        timings_list = [t for t in scratchpad.timings if isinstance(t.get("elapsed_ms"), int)]
        agg = {}
        order = []
        for t in timings_list:
            title_i = (t.get("title") or t.get("step") or "").strip() or "(untitled)"
            if title_i not in agg:
                agg[title_i] = 0
                order.append(title_i)
            agg[title_i] += int(t.get("elapsed_ms") or 0)

        rows = [(title_i, agg[title_i]) for title_i in order]
        rows.append(("TOTAL", total_ms))

        ms_pretty_table = _format_ms_table(rows)
        ms_markdown = _format_ms_table_markdown(scratchpad.timings)
        step_title = "Turn Summary (Timings)"
        await self._emit({"type": "chat.turn.summary", "agent": "turn_controller", "step": "turn.summary",
                          "status": "completed",
                          "markdown": f"{ms_markdown}",
                          "title": step_title, "data": {"elapsed_ms": total_ms},
                          "timing": {"started_ms": ms0u, "ended_ms": _now_ms(), "elapsed_ms": total_ms}})

        # Put it right in your face in the console and also via logger
        self.logger.log("\n" + ms_pretty_table + "\n")
        return ms_pretty_table, ms_markdown, timings_list

    # -------------------- Create turn --------------------
    async def construct_turn_and_scratchpad(self, payload: dict) -> CTurnScratchpad:

        rid = payload["request_id"]
        tenant, project, user = payload["tenant"], payload["project"], payload["user"]
        session_id = payload.get("session_id")
        conversation_id = payload.get("conversation_id") or session_id
        user_type = payload.get("user_type") or "anonymous"
        turn_id = payload.get("turn_id")
        text = (payload["text"] or "").strip()
        attachments = payload.get("attachments") or []
        attachments = await self._ingest_user_attachments(attachments=attachments)

        # bind for envelope composition
        self._ctx["service"] = {"request_id": rid, "tenant": tenant, "project": project,
                                "user": user, "user_type": user_type, "session_id": session_id}
        self._ctx["conversation"] = {"conversation_id": conversation_id,
                                     "turn_id": turn_id,
                                     "ts": datetime.datetime.utcnow().isoformat() + "Z"}
        scratchpad = CTurnScratchpad(user=user,
                                     conversation_id=conversation_id,
                                     turn_id=turn_id,
                                     text=text.strip(),
                                     attachments=attachments,
                                     gate_out_class=self.gate_out_class)
        scratchpad.user_ts = self._ctx["conversation"].get("ts")
        return scratchpad

    async def _ingest_user_attachments(self, *, attachments: list) -> list:
        return await ingest_user_attachments(attachments=attachments, store=self.store)

    def _attachments_summary_text(self, scratchpad: CTurnScratchpad) -> str:
        items = getattr(scratchpad, "user_attachments", None) or []
        lines: List[str] = []
        for a in items:
            if not isinstance(a, dict):
                continue
            name = (a.get("artifact_name") or a.get("filename") or "attachment").strip()
            summary = (a.get("summary") or "").strip()
            if summary:
                lines.append(f"- {name}: {summary}")
            else:
                lines.append(f"- {name}")
        return "\n".join(lines).strip()

    async def start_turn(self,
                         scratchpad: CTurnScratchpad,
                         summarize_attachments: bool = False):

        tenant, project, user, user_type, request_id, session_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"], self._ctx["service"]["session_id"]
        conversation_id, turn_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"]

        # (0) ensure User ↔ Conversation link (cheap + idempotent)
        t_turn0 = time.perf_counter()
        t0u, ms0u = _tstart()
        self._ctx["turn"] = {
            "t_turn0": t_turn0,
            "t0u": t0u,
            "ms0u": ms0u,
        }
        timing_uconv = _tend(t0u, ms0u)
        step_title = "User↔Conversation Linked"
        status = "completed"
        await self._emit({"type": "chat.step", "agent": "graph", "step": "context.graph",
                          "status": status, "title": step_title,
                          "data": {"user": user, "conversation": conversation_id},
                          "timing": timing_uconv})
        scratchpad.timings.append({
            "title": step_title,
            "elapsed_ms": timing_uconv["elapsed_ms"]
        })

        # start of turn
        if summarize_attachments:
            await self._summarize_user_attachments(scratchpad)
            await self._persist_attachment_summaries(scratchpad)

        # --- 1) Load context bundle + timeline blocks
        t1, ms1 = _tstart()
        try:
            await self._emit_turn_work_status(
                [
                    "loading",
                    "preparing context",
                    "setting up the thread",
                ]
            )
            # Bundles can override gate_out_class after construction if they use a custom gate contract.
            async def _before_compaction(payload: dict) -> None:
                await self._emit_turn_work_status(
                    [
                        "compacting",
                        "organizing the thread",
                        "distilling context",
                    ]
                )
            async def _after_compaction(payload: dict) -> None:
                await self._emit_turn_work_status(
                    [
                        "back to work",
                        "continuing",
                        "progressing",
                    ]
                )
            async def _save_summary(payload: dict) -> None:
                if not isinstance(payload, dict):
                    return
                summary = (payload.get("summary") or "").strip()
                if not summary:
                    return
                if not self.ctx_browser:
                    return
                user_type = self.runtime_ctx.user_type or "anonymous"
                embedding = None
                if self.model_service:
                    try:
                        [embedding] = await self.model_service.embed_texts([summary])
                    except Exception:
                        embedding = None
                try:
                    await self.ctx_browser.save_artifact(
                        kind="conv.range.summary",
                        tenant=self.runtime_ctx.tenant,
                        project=self.runtime_ctx.project,
                        user_id=self.runtime_ctx.user_id,
                        conversation_id=self.runtime_ctx.conversation_id,
                        user_type=user_type,
                        turn_id=self.runtime_ctx.turn_id,
                        content=dict(payload),
                        content_str=summary,
                        embedding=embedding,
                        ttl_days=_ttl_for(user_type, 365),
                        bundle_id=self.config.ai_bundle_spec.id,
                        index_only=True,
                    )
                except Exception:
                    pass
            self.runtime_ctx.on_before_compaction = _before_compaction
            self.runtime_ctx.on_after_compaction = _after_compaction
            self.runtime_ctx.save_summary = _save_summary
            self.runtime_ctx.started_at = scratchpad.started_at
            # refresh per-turn ids
            self.runtime_ctx.turn_id = scratchpad.turn_id
            self.runtime_ctx.conversation_id = scratchpad.conversation_id
            self.runtime_ctx.user_id = scratchpad.user
            try:
                await self.ctx_browser.load_timeline(
                    days=365,
                )
            except Exception:
                pass
            # Set new-conversation flag and seed title from timeline
            try:
                tl = self.ctx_browser.timeline
                scratchpad.is_new_conversation = len(tl.get_history_blocks()) == 0
                if tl.conversation_title:
                    scratchpad.conversation_title = tl.conversation_title
            except Exception:
                pass

        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")

            timing_ctx = _tend(t1, ms1)
            scratchpad.timings.append({"title": "context.load", "elapsed_ms": timing_ctx["elapsed_ms"]})

        # (1) user message
        await self._emit({"type": "chat.conversation.accepted", "agent": "user", "step": "chat.user.message", "status": "completed",
                          "title": "User Message", "data": {"text": scratchpad.short_text, "chars": len(scratchpad.short_text)}})
        self.logger.log_step("recv_user_message", {"len": len(scratchpad.user_text)})

        # Contribute user prompt + attachments to current turn log
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_user_input_blocks
            self.ctx_browser.contribute(
                blocks=build_user_input_blocks(
                    runtime=self.ctx_browser.runtime_ctx,
                    user_text=scratchpad.user_text or "",
                    user_attachments=list(scratchpad.user_attachments or []),
                    block_factory=self.ctx_browser.timeline.block,
                ),
            )
            # Add attachments to sources_pool so local attachment paths are citable.
            try:
                from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.sources import merge_sources_pool_for_attachment_rows
                turn_id = self.ctx_browser.runtime_ctx.turn_id if self.ctx_browser and self.ctx_browser.runtime_ctx else ""
                new_rows = []
                for att in (scratchpad.user_attachments or []):
                    if not isinstance(att, dict):
                        continue
                    filename = (att.get("filename") or att.get("name") or "").strip()
                    if not filename or not turn_id:
                        continue
                    local_path = f"{turn_id}/attachments/{filename}"
                    hosted_uri = (att.get("hosted_uri") or att.get("source_path") or att.get("path") or att.get("key") or "").strip()
                    row = {
                        "url": hosted_uri or local_path,
                        "title": filename,
                        "text": "",
                        "source_type": "attachment",
                        "mime": (att.get("mime") or att.get("mime_type") or "").strip(),
                        "size_bytes": att.get("size") or att.get("size_bytes"),
                        "local_path": local_path,
                        "artifact_path": f"fi:{turn_id}.user.attachments/{filename}",
                        "turn_id": turn_id,
                    }
                    if hosted_uri:
                        row["hosted_uri"] = hosted_uri
                    if att.get("rn"):
                        row["rn"] = att.get("rn")
                    if att.get("key"):
                        row["key"] = att.get("key")
                    new_rows.append(row)
                if new_rows:
                    merge_sources_pool_for_attachment_rows(ctx_browser=self.ctx_browser, rows=new_rows)
            except Exception:
                pass
        except Exception:
            pass

        self.logger.start_operation(
            "orchestrator.process",
            request_id=request_id, tenant=tenant, project=project, user=user,
            session=session_id, conversation=conversation_id, text_preview=scratchpad.short_text,
        )

    async def finish_turn(self,
                          scratchpad: TurnScratchpad,
                          ok: bool = True,
                          result_summary: str | None = None,
                          on_flush_completed_hook: Optional[Callable[[CTurnScratchpad], Awaitable[None]]] = None):
        # prevent double-finish from multiple branches / nested handlers
        if getattr(scratchpad, "_turn_finished", False):
            return
        scratchpad._turn_finished = True

        tenant, project, user, user_type, request_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"]
        conversation_id, turn_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"]
        t_turn0, ms0u = self._ctx["turn"]["t_turn0"], self._ctx["turn"]["ms0u"]

        if scratchpad.answer:
            # Contribute pre-answer blocks (e.g., final ANNOUNCE)
            try:
                runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
                pre_hook = getattr(runtime_ctx, "on_before_completion_contribution", None) if runtime_ctx else None
                pre_blocks = pre_hook() if callable(pre_hook) else None
                if callable(pre_hook):
                    try:
                        runtime_ctx.on_before_completion_contribution = None
                    except Exception:
                        pass
                if pre_blocks:
                    try:
                        types = [b.get("type") for b in pre_blocks if isinstance(b, dict)]
                        self.logger.log(f"[workflow] pre_completion_blocks: {types}", level="INFO")
                    except Exception:
                        pass
                    self.ctx_browser.contribute(blocks=list(pre_blocks))
            except Exception:
                pass
            # Contribute assistant completion to current turn log
            try:
                from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_assistant_completion_blocks
                self.ctx_browser.contribute(
                    blocks=build_assistant_completion_blocks(
                        runtime=self.ctx_browser.runtime_ctx,
                        answer_text=scratchpad.answer or "",
                        ended_at=getattr(scratchpad, "ended_at", None),
                        block_factory=self.ctx_browser.timeline.block,
                    ),
                )
                # set suggested followups on scratchpad to add them on timeline. This will render the timeline properly
                if scratchpad.suggested_followups:
                    # in order to later retrieve with fetch and in order to contribute to assistant answer block during rendering
                    self.ctx_browser.contribute_suggested_followups(suggested_followups=scratchpad.suggested_followups)

            except Exception:
                pass
            await self.persist_assistant(scratchpad)
            # Post-answer blocks (react.state / react.exit)
            try:
                runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
                post_hook = getattr(runtime_ctx, "on_after_completion_contribution", None) if runtime_ctx else None
                post_blocks = post_hook() if callable(post_hook) else None
                if callable(post_hook):
                    try:
                        runtime_ctx.on_after_completion_contribution = None
                    except Exception:
                        pass
                if post_blocks:
                    try:
                        types = [b.get("type") for b in post_blocks if isinstance(b, dict)]
                        self.logger.log(f"[workflow] post_completion_blocks: {types}", level="INFO")
                    except Exception:
                        pass
                    self.ctx_browser.contribute(blocks=list(post_blocks))
            except Exception:
                pass
        else:
            # No assistant answer; still emit pre/post blocks if present
            try:
                runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
                pre_hook = getattr(runtime_ctx, "on_before_completion_contribution", None) if runtime_ctx else None
                pre_blocks = pre_hook() if callable(pre_hook) else None
                if callable(pre_hook):
                    try:
                        runtime_ctx.on_before_completion_contribution = None
                    except Exception:
                        pass
                if pre_blocks:
                    try:
                        types = [b.get("type") for b in pre_blocks if isinstance(b, dict)]
                        self.logger.log(f"[workflow] pre_completion_blocks: {types}", level="INFO")
                    except Exception:
                        pass
                    self.ctx_browser.contribute(blocks=list(pre_blocks))
            except Exception:
                pass
            try:
                runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
                post_hook = getattr(runtime_ctx, "on_after_completion_contribution", None) if runtime_ctx else None
                post_blocks = post_hook() if callable(post_hook) else None
                if callable(post_hook):
                    try:
                        runtime_ctx.on_after_completion_contribution = None
                    except Exception:
                        pass
                if post_blocks:
                    try:
                        types = [b.get("type") for b in post_blocks if isinstance(b, dict)]
                        self.logger.log(f"[workflow] post_completion_blocks: {types}", level="INFO")
                    except Exception:
                        pass
                    self.ctx_browser.contribute(blocks=list(post_blocks))
            except Exception:
                pass
        # Save turn log (always) - v2
        try:
            contrib_log = []
            try:
                if self.ctx_browser:
                    contrib_log = list(self.ctx_browser.current_turn_blocks() or [])
            except Exception:
                contrib_log = []
            tlog = TurnLog(
                turn_id=turn_id,
                ts=(scratchpad.started_at or ""),
                blocks=contrib_log,
            )
            payload = tlog.to_dict()
            # sources_pool is stored in timeline artifact, not in turn log
        except Exception:
            payload = {"turn_id": turn_id, "ts": (scratchpad.started_at or ""), "blocks": []}
        await self.ctx_client.save_turn_log_as_artifact(
            tenant=tenant, project=project, user=user,
            conversation_id=conversation_id, user_type=user_type,
            turn_id=turn_id,
            bundle_id=self.config.ai_bundle_spec.id,
            payload=payload,
            extra_tags=[],
        )

        # MEMORY management. post-answer reconciliation (cadenced). Only if turn finished w/ service error.
        if ok:
            if on_flush_completed_hook:
                await on_flush_completed_hook(scratchpad)
        try:
            await self.ctx_browser.persist_timeline()
        except Exception:
            pass
        # (19) done

        total_ms = int((time.perf_counter() - t_turn0) * 1000)
        step_title = "Plan Completed" if ok else "Plan Failed"
        await self._emit({"type": "chat.conversation.turn.completed", "agent": "planner", "step": "plan.done", "status": "completed",
                          "title": step_title, "data": {"elapsed_ms": total_ms},
                          "timing": {"started_ms": ms0u, "ended_ms": _now_ms(), "elapsed_ms": total_ms}})
        scratchpad.timings.append({
            "title": step_title,
            "elapsed_ms": total_ms
        })

        def_status = "ok" if ok else "failed"
        self.logger.finish_operation(ok, result_summary=(result_summary or f"{def_status} • elapsed={total_ms}ms"))
        await self.report_timings(scratchpad, ms0u, total_ms)

        try:
            await self._persist_stream_artifacts()
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")

    async def _handle_turn_exception(self,
                                     exc: Exception,
                                     scratchpad: CTurnScratchpad) -> None:

        # ---- phase info ----
        phase = getattr(scratchpad, "current_phase", None)
        agent = (phase.agent if phase else None) or "workflow"
        stage = (phase.name if phase else None) or "workflow"
        meta = (phase.meta if phase else {}) if phase else {}

        t_turn0, ms0u = self._ctx["turn"]["t_turn0"], self._ctx["turn"]["ms0u"]
        tenant, project, user, user_type, request_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"]

        total_ms = int((time.perf_counter() - t_turn0) * 1000)
        ms_pretty_table, ms_markdown, timings = await self.report_timings(scratchpad, ms0u, total_ms)

        extra_data: dict = {}
        managed_exception: Exception | None = None
        show_error_in_timeline = True

        # Defaults for generic path
        message = str(exc) or repr(exc)
        error_type = exc.__class__.__name__
        if not isinstance(exc, ServiceException):
            try:
                safe_msg = self.message_resources_fn("server_error") if self.message_resources_fn else None
            except Exception:
                safe_msg = None
            if safe_msg:
                message = safe_msg
            try:
                extra_data["raw_error"] = str(exc)
                extra_data["traceback"] = traceback.format_exc()
            except Exception:
                pass

        # ---- unwrap ServiceException / TurnPhaseError vs generic errors ----
        if isinstance(exc, ServiceException):
            se: ServiceError = exc.err

            # Optional: prefer service payload for "agent"/"stage" if present
            agent = se.service_name or agent
            stage = se.stage or stage

            # message = se.message
            service_message = se.message
            # user-facing message (no internals)
            message = self.message_resources_fn("usage_limit")
            # prefer canonical codes if provided
            error_type = se.code or se.error_type or "ServiceError"

            extra_data = {
                "service_error": se.model_dump(),
                "service_kind": getattr(se.kind, "value", se.kind),
                "service_name": se.service_name,
                "provider": se.provider,
                "model_name": se.model_name,
                "http_status": se.http_status,
                "retryable": se.retryable,
                "service_stage": se.stage,
            }
            show_error_in_timeline = False

            # ---- build economics payload (entrypoint-style) ----
            bundle_id = self.config.ai_bundle_spec.id
            subj = subject_id_of(tenant, project, user)

            # "derived from service error"
            code = (se.code or se.error_type or (f"http_{int(se.http_status)}" if se.http_status else None) or "services_quota_exceeded")

            econ_payload = {
                "message": message,
                "reason": "services_quota_exceeded",
                "bundle_id": bundle_id,
                "subject_id": subj,
                "user_type": user_type,
                "code": code,
                "show_in_timeline": False,
                "service_error": se.model_dump(),  # <-- required nesting
            }

            # Emit service event so client can handle it
            try:
                await self.comm.service_event(
                    type="rate_limit.ai_services_quota",
                    step="rate_limit",
                    status="error",
                    title="Services quota exceeded",
                    agent="bundle.rate_limiter",
                    data=econ_payload,
                )
            except Exception:
                # best-effort; don't mask the main flow
                pass

            managed_exception = EconomicsLimitException(message, code=code, data=econ_payload)

        elif isinstance(exc, TurnPhaseError):
            message = str(exc)
            error_type = exc.code or "TurnPhaseError"
            extra_data = dict(exc.data or {})
        else:
            if not message:
                message = str(exc) or repr(exc)
            error_type = exc.__class__.__name__

        # ---- log ----
        self.logger.log(
            f"Turn failed at phase={stage} agent={agent}: {message}\n"
            f"Timings:\n{ms_pretty_table}\n"
            f"error_type={error_type};phase_meta={meta}",
            level="ERROR",
        )

        # ---- build message payload ----
        data = {
            "agent": agent,
            "stage": stage,
            "phase_meta": meta,
            "error_type": error_type,
            "timings": timings,
            "timings_markdown": ms_markdown,
            **extra_data,
        }
        safe_data = data
        try:
            safe_data = json.loads(json.dumps(data, ensure_ascii=False, default=str))
        except Exception:
            safe_data = {
                "agent": agent,
                "stage": stage,
                "error_type": error_type,
            }
        data_for_user = dict(safe_data)
        # keep internals out of timeline-facing error payloads
        for k in ("raw_error", "traceback"):
            if k in data_for_user:
                data_for_user.pop(k, None)
        if show_error_in_timeline:
            # pass
            # Emit error event for telemetry and an answer bubble for the user.
            await self.comm.error(message=message, agent="turn.error", data=data_for_user)
            # await self.comm.delta(text=message, index=0, marker="answer", agent="turn_exception", completed=True)
        else:
            # Keep it out of timeline; still produce an "answer" bubble
            await self.comm.delta(text=message, index=0, marker="answer", agent="turn_exception", completed=True)

        # no-op (kept for alignment with prior error handling)

        # ---- rollback ----
        try:
            await self.ctx_client.delete_turn(
                tenant=tenant,
                project=project,
                user_id=user,
                conversation_id=scratchpad.conversation_id,
                turn_id=scratchpad.turn_id,
                user_type=user_type,
                bundle_id=self.config.ai_bundle_spec.id,
                where="index_only",   # important: keep blobs for monitoring, just rollback index
            )
        except Exception as e:
            self.logger.log(f"Rollback delete_turn(index_only) failed: {traceback.format_exc()}")

        # ---- bubble ----
        if managed_exception is not None:
            raise managed_exception from exc

        # preserve original traceback as much as possible
        raise exc.with_traceback(exc.__traceback__)
