# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/solutions/chatbot/base_workflow.py

import os, time, datetime, json, re
import pathlib
import random
import traceback
from typing import Dict, Any, List, Optional, Type, Callable, Awaitable

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.comm.emitters import AIBEmitters, StepPayload, EventPayload
from kdcube_ai_app.apps.chat.sdk.context.memory.conv_memories import ConvMemoriesStore
from kdcube_ai_app.apps.chat.sdk.context.memory.active_set_management import _preload_conversation_memory_state
from kdcube_ai_app.apps.chat.sdk.context.memory.turn_fingerprint import TurnFingerprintV1
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import FINGERPRINT_KIND, ContextRAGClient
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_reconciler import CtxRerankOut

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_index import ConvTicketIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore, Ticket
from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import subject_id_of
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.reporting.turn_reporting import _format_ms_table, _format_ms_table_markdown
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad, TurnPhaseError
from kdcube_ai_app.apps.chat.sdk.runtime.solution.contracts import SolveResult
from kdcube_ai_app.apps.chat.sdk.runtime.solution.gate.gate_contract import gate_ctx_queries
from kdcube_ai_app.apps.chat.sdk.runtime.solution.solution_engine import SolverSystem
from kdcube_ai_app.apps.chat.sdk.runtime.solution.widgets.conversation_turn_work_status import \
    ConversationTurnWorkStatus
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import create_tool_subsystem_with_mcp
from kdcube_ai_app.apps.chat.sdk.runtime.user_inputs import ingest_user_attachments
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.scratchpad import CTurnScratchpad, TurnView
from kdcube_ai_app.apps.chat.sdk.util import (truncate_text_by_tokens, _to_jsonable, _utc_now_iso_minute,
                                              ensure_event_markdown, _to_json_safe, _jd,  _now_ms,
                                              _tstart, _tend)
import kdcube_ai_app.apps.chat.sdk.tools.citations as md_utils
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger, ModelServiceBase, Config, _mid
from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.runtime.solution.solution_workspace import ApplicationHostingService
from kdcube_ai_app.apps.chat.sdk.context.graph.graph_ctx import GraphCtx
from kdcube_ai_app.apps.chat.sdk.runtime.user_inputs import (
    attachment_summary_index_text,
)

import kdcube_ai_app.apps.chat.sdk.runtime.solution.context.journal as ctx_presentation_module
import kdcube_ai_app.apps.chat.sdk.viz.logging_helpers as logging_helpers

def _here(*parts: str) -> pathlib.Path:
    """Path relative to this file (workflow.py)."""
    return pathlib.Path(__file__).resolve().parent.joinpath(*parts)

def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


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
                 graph: GraphCtx,
                 kb: KBClient,
                 store: ConversationStore,
                 comm: ChatCommunicator,
                 model_service: ModelServiceBase,
                 conv_ticket_store: ConvTicketStore,
                 config: Config,
                 comm_context: ChatTaskPayload,
                 ctx_client: Any = None,
                 message_resources_fn: Optional[Callable[[str, bool], str]] = None,):

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
        self.logger = AgentLogger("customer.orchestrator")

        self._ctx = {}

        self.config = config
        self.ctx_client = ctx_client
        self.ctx_browser = ContextBrowser(
            ctx_client=self.ctx_client,
            logger=self.logger,
            turn_view_class=TurnView,
        )
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

    # ---------- Artifact helpers ----------

    def _artifact_index_text(
            self,
            *,
            kind: str,
            payload: Dict[str, Any],
            title: Optional[str] = None,
            payload_txt: Optional[str] = None,
    ) -> str:
        if payload_txt:
            return payload_txt if payload_txt.startswith("[") else f"[{kind}]\n{payload_txt}"
        if kind == "conv.user_shortcuts":
            items = payload.get("items") or []
            return "followups: " + " ; ".join([str(x) for x in items][:20])
        if kind == "conv.clarification_questions":
            items = payload.get("items") or []
            return "clarification questions: " + " ; ".join([str(x) for x in items][:20])
        if kind == "user.input.summary":
            return (payload.get("summary") or "")[:4000]
        if kind == "user.attachment":
            return attachment_summary_index_text(payload) if attachment_summary_index_text else str(payload)[:1000]
        if kind == "project.log":
            return payload.get("markdown") or ""
        if kind == "solver.program.presentation":
            return payload.get("markdown") or ""
        if kind == "solver.failure":
            return payload.get("markdown") or ""
        # fallback: include kind header + json
        header = f"[{kind}]"
        body = json.dumps(payload, ensure_ascii=False)
        return f"{header}\n{body}" if body else header

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
                content_str = self._artifact_index_text(
                    kind="user.attachment",
                    payload=payload,
                    payload_txt=summary,
                )
                embedding = None
                if self.model_service:
                    try:
                        [embedding] = await self.model_service.embed_texts([summary])
                    except Exception:
                        embedding = None
                await self.ctx_browser.save_artifact(
                    kind="user.attachment",
                    tenant=self._ctx["service"]["tenant"],
                    project=self._ctx["service"]["project"],
                    user_id=self._ctx["service"]["user"],
                    conversation_id=self._ctx["conversation"]["conversation_id"],
                    user_type=self._ctx["service"]["user_type"],
                    turn_id=self._ctx["conversation"]["turn_id"],
                    track_id=self._ctx["conversation"]["track_id"],
                    content=payload,
                    content_str=content_str,
                    embedding=embedding,
                    ttl_days=_ttl_for(self._ctx["service"]["user_type"], 365),
                    meta={
                        "title": f"User Attachment Summary: {filename}",
                        "kind": "user.attachment",
                        "request_id": self._ctx["service"]["request_id"],
                    },
                    bundle_id=self.config.ai_bundle_spec.id,
                )
            a["summary_persisted"] = True

    def _log_attachments_in_turn_log(self, scratchpad) -> None:
        items = []
        if hasattr(scratchpad, "_compact_user_attachments_for_turn_log"):
            try:
                items = scratchpad._compact_user_attachments_for_turn_log()
            except Exception:
                items = []
        if not items:
            return
        if hasattr(scratchpad, "tlog"):
            try:
                scratchpad.tlog.user_attachments(items)
            except Exception:
                return

    # -------------------- scratchpad persistence --------------------
    async def _update_graph_with_local_memories(
            self,
            scratch: TurnScratchpad,
            *,
            tenant: str,
            project: str,
            user: str,
            conversation_id: str,
            turn_id: str,
            user_type: str,
    ) -> None:
        # NOTE: facts/assertions/exceptions shape is legacy and may be outdated.
        for f in scratch.proposed_facts:
            await self.graph.add_assertion(
                tenant=tenant, project=project, user=user, conversation=conversation_id,
                key=f["key"],
                value=f["value"],
                desired=bool(f.get("desired", True)),
                scope=f.get("scope") or "conversation",
                confidence=float(f.get("confidence", 0.6)),
                ttl_days=_ttl_for(user_type, int(f.get("ttl_days", 365))),
                reason=f.get("reason") or "turn-proposed",
                turn_id=turn_id,
                user_type=user_type
            )
        for ex in scratch.exceptions:
            await self.graph.add_exception(
                tenant=tenant, project=project, user=user, conversation=conversation_id,
                rule_key=ex["rule_key"], scope=ex["scope"], value=ex["value"], reason=ex["reason"],
                turn_id=turn_id, user_type=user_type
            )

    async def _store_followups(
            self,
            scratch: TurnScratchpad,
            *,
            tenant: str,
            project: str,
            user: str,
            conversation_id: str,
            turn_id: str,
            user_type: str,
            track_id: str,
            request_id: str,
    ) -> None:
        if not scratch.user_shortcuts:
            return
        if not self.ctx_client:
            return
        payload = {
            "items": scratch.user_shortcuts,
            "turn_id": turn_id,
            "request_id": request_id,
            "ts": datetime.datetime.utcnow().isoformat() + "Z",
        }
        await self.ctx_browser.save_artifact(
            kind="conv.user_shortcuts",
            tenant=tenant,
            project=project,
            user_id=user,
            conversation_id=conversation_id,
            user_type=user_type,
            turn_id=turn_id,
            track_id=track_id,
            content=payload,
            content_str=json.dumps(payload, ensure_ascii=False),
            bundle_id=self.config.ai_bundle_spec.id,
            meta={"title": "User Shortcuts", "kind": "conv.user_shortcuts"},
        )

    async def _store_clarification_questions(
            self,
            scratch: TurnScratchpad,
            *,
            tenant: str,
            project: str,
            user: str,
            conversation_id: str,
            turn_id: str,
            user_type: str,
            track_id: str,
            request_id: str,
    ) -> None:
        if not scratch.clarification_questions:
            return
        if not self.ctx_client:
            return
        payload = {
            "items": scratch.clarification_questions,
            "turn_id": turn_id,
            "request_id": request_id,
            "ts": datetime.datetime.utcnow().isoformat() + "Z",
        }
        await self.ctx_browser.save_artifact(
            kind="conv.clarification_questions",
            tenant=tenant,
            project=project,
            user_id=user,
            conversation_id=conversation_id,
            user_type=user_type,
            turn_id=turn_id,
            track_id=track_id,
            content=payload,
            content_str=json.dumps(payload, ensure_ascii=False),
            bundle_id=self.config.ai_bundle_spec.id,
            meta={"title": "Clarification Questions", "kind": "conv.clarification_questions"},
        )

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

    def _update_turn_fp(
            self,
            scratchpad: CTurnScratchpad,
            *,
            objective: str | None = None,
            topics: List[str] | None = None,
            assertions: List[dict] | None = None,
            exceptions: List[dict] | None = None,
            facts: List[dict] | None = None,
            assistant_signals: List[dict] | None = None,
            ctx_queries: List[dict] | None = None,
    ) -> TurnFingerprintV1:
        fp = scratchpad.turn_fp if isinstance(getattr(scratchpad, "turn_fp", None), TurnFingerprintV1) else None
        if fp is None:
            fp = TurnFingerprintV1(
                version="v1",
                turn_id=scratchpad.turn_id,
                objective=(objective or "").strip(),
                topics=list(topics or []),
                assertions=list(assertions or []),
                exceptions=list(exceptions or []),
                assistant_signals=list(assistant_signals or []),
                facts=list(facts or []),
                ctx_retrieval_queries=list(ctx_queries or []),
                made_at=_utc_now_iso_minute(),
            )
            scratchpad.turn_fp = fp
            return fp

        if objective and not fp.objective:
            fp.objective = objective
        if topics:
            fp.topics = self._merge_topics(fp.topics or [], topics)
        if assertions:
            fp.assertions = list(assertions)
        if exceptions:
            fp.exceptions = list(exceptions)
        if facts:
            fp.facts = list(facts)
        if assistant_signals:
            fp.assistant_signals = list(assistant_signals)
        if ctx_queries:
            fp.ctx_retrieval_queries = list(ctx_queries)
        if not fp.made_at:
            fp.made_at = _utc_now_iso_minute()
        scratchpad.turn_fp = fp
        return fp

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

    async def _persist_turn_fingerprint(self,
                                        scratchpad: CTurnScratchpad) -> None:
        if getattr(scratchpad, "_turn_fp_persisted", False):
            return
        summary = scratchpad.turn_summary if isinstance(scratchpad.turn_summary, dict) else {}
        objective = (scratchpad.objective or summary.get("objective") or "").strip()
        topics = self._merge_topics(
            scratchpad.turn_topics_plain or [],
            self._topics_from_summary(summary),
            )
        assertions, exceptions = self._prefs_from_summary(summary)
        assistant_signals = self._assistant_signals_from_summary(summary)
        ctx_queries = gate_ctx_queries(getattr(scratchpad, "gate", None))
        ts = _utc_now_iso_minute()
        fp = self._update_turn_fp(
            scratchpad,
            objective=objective,
            topics=topics,
            assertions=assertions,
            exceptions=exceptions,
            assistant_signals=assistant_signals,
            ctx_queries=ctx_queries,
        )
        fp.made_at = fp.made_at or ts
        if scratchpad.is_new_conversation:
            title = (scratchpad.conversation_title or "").strip()
            if title:
                fp.conversation_title = title
        scratchpad.proposed_facts = assertions
        try:
            scratchpad.tlog.state = scratchpad.tlog.state or {}
            scratchpad.tlog.state["fingerprint"] = fp.to_json()
        except Exception:
            pass

        extra_tags = ["conv.start"] if scratchpad.is_new_conversation else []
        if assistant_signals:
            extra_tags.append("assistant_signal")
            for s in assistant_signals:
                key = _norm_topic((s.get("key") or "").strip())
                if key:
                    extra_tags.append(f"assistant_signal:{key}")
        tenant = self._ctx["service"]["tenant"]
        project = self._ctx["service"]["project"]
        user = self._ctx["service"]["user"]
        user_type = self._ctx["service"]["user_type"]
        conversation_id = self._ctx["conversation"]["conversation_id"]
        turn_id = self._ctx["conversation"]["turn_id"]
        track_id = self._ctx["conversation"]["track_id"]
        try:
            await self.ctx_browser.save_artifact(
                kind=FINGERPRINT_KIND,
                tenant=tenant, project=project,
                turn_id=turn_id,
                track_id=track_id,
                user_id=user,
                conversation_id=conversation_id,
                bundle_id=self.config.ai_bundle_spec.id,
                user_type=user_type,
                content=_to_jsonable(fp),
                extra_tags=extra_tags,
                index_only=True,
            )
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")
        scratchpad._turn_fp_persisted = True

    async def _flush_active_set(self, scratchpad: CTurnScratchpad) -> None:
        if getattr(scratchpad, "_active_set_flushed", False):
            return
        active_set = scratchpad.active_set
        if not isinstance(active_set, dict):
            return
        if "new" in active_set:
            del active_set["new"]
        tenant = self._ctx["service"]["tenant"]
        project = self._ctx["service"]["project"]
        user = self._ctx["service"]["user"]
        user_type = self._ctx["service"]["user_type"]
        conversation_id = self._ctx["conversation"]["conversation_id"]
        turn_id = self._ctx["conversation"]["turn_id"]
        track_id = self._ctx["conversation"]["track_id"]
        await self.conv_memories.put_active_set(
            tenant=tenant, project=project, user=user, conversation=conversation_id,
            turn_id=turn_id,
            active_set=active_set, user_type=user_type, track_id=track_id,
            bundle_id=self.config.ai_bundle_spec.id
        )
        scratchpad._active_set_flushed = True


    async def persist_project_log_from_sr(
            self,
            *,
            sr: SolveResult,
            tenant: str, project: str, user: str, conversation_id: str,
            user_type: str, turn_id: str, rid: str, track_id: str
    ) -> Optional[dict]:
        """
        If a 'project_log' deliverable (markdown) exists, persist it as its own artifact:
          kind = "project.log"
        This is user-facing and should be searchable independently of the general deliverables blob.
        """
        try:
            dmap = sr.deliverables_map() or {}
        except Exception:
            dmap = {}

        spec = dmap.get("project_log")
        if not spec:
            # attempt best-effort recovery from out_items
            try:
                for it in (sr.out_items() or []):
                    rid_i = (it or {}).get("resource_id") or ""
                    if rid_i == "slot:project_log":
                        spec = {"description": it.get("description") or "",
                                "value": {"type": "inline", "mime": "text/markdown", "output": (it.get("output") or {})}}
                        break
            except Exception:
                spec = None

        if not spec:
            return None

        val = (spec or {}).get("value") or {}
        if val.get("type") != "inline":
            # Currently we only index the inline markdown representation
            return None

        raw = (val.get("output") or {}).get("text") or ""
        if raw is None:
            return None

        if not isinstance(raw, str):
            try:
                raw = json.dumps(raw, ensure_ascii=False)
            except Exception:
                raw = str(raw)

        payload = {
            "markdown": raw,
            "slot": "project_log",
            "description": (spec or {}).get("description") or "",
            "mime": val.get("mime") or "text/markdown",
            "turn_id": turn_id,
            "request_id": rid
        }

        extra_tags = ["slot:project_log"]
        uri = None
        mid = None
        if self.ctx_client:
            content_str = self._artifact_index_text(kind="project.log", payload=payload)
            res = await self.ctx_browser.save_artifact(
                kind="project.log",
                tenant=tenant,
                project=project,
                user_id=user,
                conversation_id=conversation_id,
                user_type=user_type,
                turn_id=turn_id,
                track_id=track_id,
                content=payload,
                content_str=content_str,
                embedding=None,
                ttl_days=_ttl_for(user_type, 365),
                meta={
                    "title": "Project Log",
                    "kind": "project.log",
                    "request_id": rid,
                },
                extra_tags=extra_tags,
                bundle_id=self.config.ai_bundle_spec.id,
                index_only=True,
            )
            uri = res.get("hosted_uri")
            mid = res.get("message_id")

        return {
            "role": "artifact",
            "text": "[project.log]\n" + raw,
            "extra": {"message_id": mid, "uri": uri},
            "payload": payload,
            "meta": {"kind": "project.log"}
        }

    async def _persist_stream_artifacts(self) -> None:
        if not self.ctx_client:
            return
        try:
            tenant = self._ctx["service"]["tenant"]
            project = self._ctx["service"]["project"]
            user_id = self._ctx["service"]["user"]
            user_type = self._ctx["service"]["user_type"]
            conversation_id = self._ctx["conversation"]["conversation_id"]
            turn_id = self._ctx["conversation"]["turn_id"]
            track_id = self._ctx["conversation"]["track_id"]
        except Exception:
            return

        all_deltas = self.comm.get_delta_aggregates(
            conversation_id=conversation_id, turn_id=turn_id, merge_text=True
        )
        thinking_blocks = [d for d in all_deltas if d.get("marker") == "thinking" and (d.get("text") or d.get("chunks"))]
        canvas_and_tools_blocks = [d for d in all_deltas if d.get("marker") in ["canvas", "tool", "subsystem"] and (d.get("text") or d.get("chunks"))]
        timeline_text_blocks = [d for d in all_deltas if d.get("marker") in ["timeline_text"] and (d.get("text") or d.get("chunks"))]

        thinking_full = [
            {"agent": item["agent"], "ts_first": item["ts_first"], "ts_last": item["ts_last"],
             "text": item["text"], "chunks_num": len(item.get("chunks") or [])}
            for item in thinking_blocks
        ]
        thinking_idx = [
            {**{k: v for k, v in i.items() if k not in ("text", "chunks")},
             "text_size": len(i.get("text") or ""), "chunks_num": len(i.get("chunks") or [])}
            for i in thinking_blocks
        ]

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

        timeline_text_full = [
            {"agent": item["agent"], "ts_first": item["ts_first"], "ts_last": item["ts_last"],
             "artifact_name": item.get("artifact_name"),
             "text": item["text"], "chunks_num": len(item.get("chunks") or [])}
            for item in timeline_text_blocks
        ]
        timeline_text_idx = [
            {**{k: v for k, v in i.items() if k not in ("text", "chunks")},
             "text_size": len(i.get("text") or ""), "chunks_num": len(i.get("chunks") or [])}
            for i in timeline_text_blocks
        ]

        if thinking_blocks:
            await self.ctx_browser.save_artifact(
                kind="conv.thinking.stream",
                tenant=tenant, project=project,
                turn_id=turn_id,
                track_id=track_id,
                user_id=user_id,
                conversation_id=conversation_id,
                bundle_id=self.config.ai_bundle_spec.id,
                user_type=user_type,
                content={"version": "v1", "items": thinking_full},
                content_str=json.dumps(thinking_idx),
                extra_tags=["conversation", "stream"],
            )
        if canvas_and_tools_blocks:
            await self.ctx_browser.save_artifact(
                kind="conv.artifacts.stream",
                tenant=tenant, project=project,
                turn_id=turn_id,
                track_id=track_id,
                user_id=user_id,
                conversation_id=conversation_id,
                bundle_id=self.config.ai_bundle_spec.id,
                user_type=user_type,
                content={"version": "v1", "items": canvas_full},
                content_str=json.dumps(canvas_idx),
                extra_tags=["conversation", "stream", "canvas"],
            )
        if timeline_text_blocks:
            await self.ctx_browser.save_artifact(
                kind="conv.timeline_text.stream",
                tenant=tenant, project=project,
                turn_id=turn_id,
                track_id=track_id,
                user_id=user_id,
                conversation_id=conversation_id,
                bundle_id=self.config.ai_bundle_spec.id,
                user_type=user_type,
                content={"version": "v1", "items": timeline_text_full},
                content_str=json.dumps(timeline_text_idx),
                extra_tags=["conversation", "stream", "timeline_text"],
            )

        self.comm.clear_delta_aggregates(conversation_id=conversation_id, turn_id=turn_id)


    async def _persist_program_presentation(
            self,
            *,
            tenant: str, project: str, user: str, conversation_id: str,
            user_type: str, turn_id: str, rid: str, track_id: str,
            markdown: str,
            out_resource_ids: Optional[List[str]] = None
    ):
        if not (markdown and markdown.strip()):
            return None

        heading = next((ln.lstrip("# ").strip() for ln in markdown.splitlines() if ln.strip().startswith("#")), "") or "Program Presentation"
        payload = {
            "markdown": markdown,
            "turn_id": turn_id,
            "request_id": rid,
        }
        payload_txt = (
            "[solver.program.presentation]\n"
            f"{markdown}"
        )
        extra_tags = []
        for rid_i in (out_resource_ids or []):
            if rid_i:
                extra_tags.append(f"resource:{rid_i}")

        uri = None
        mid = None
        if self.ctx_client:
            content_str = self._artifact_index_text(
                kind="solver.program.presentation",
                payload=payload,
                payload_txt=payload_txt,
            )
            embedding = None
            if self.model_service:
                try:
                    [embedding] = await self.model_service.embed_texts([payload_txt])
                except Exception:
                    embedding = None
            res = await self.ctx_browser.save_artifact(
                kind="solver.program.presentation",
                tenant=tenant,
                project=project,
                user_id=user,
                conversation_id=conversation_id,
                user_type=user_type,
                turn_id=turn_id,
                track_id=track_id,
                content=payload,
                content_str=content_str,
                embedding=embedding,
                ttl_days=_ttl_for(user_type, 365),
                meta={
                    "title": "Program Presentation",
                    "kind": "solver.program.presentation",
                    "request_id": rid,
                },
                extra_tags=extra_tags,
                bundle_id=self.config.ai_bundle_spec.id,
                index_only=True,
            )
            uri = res.get("hosted_uri")
            mid = res.get("message_id")

        # Return a context item for _messages_with_context → _format_context_block
        return {
            "role": "artifact",
            "text": payload_txt,
            "extra": {"message_id": mid, "uri": uri},
            "payload": payload,
            "meta": {
                "kind": "solver.program.presentation",
            }
        }

    async def _persist_solver_failure(
            self,
            *,
            tenant: str, project: str, user: str, conversation_id: str,
            user_type: str, turn_id: str, rid: str, track_id: str,
            sr: SolveResult
    ):
        failure_presentation = sr.failure_presentation
        if not failure_presentation:
            return None
        markdown = failure_presentation.get("markdown")
        payload = {
            **failure_presentation,
            "turn_id": turn_id,
            "request_id": rid,
            # **{"codegen_run_id": run_id if run_id else {}},
        }
        payload_txt = (
            "[solver.failure]\n"
            f"{markdown}"
        )
        uri = None
        mid = None
        if self.ctx_client:
            content_str = self._artifact_index_text(
                kind="solver.failure",
                payload=payload,
                payload_txt=payload_txt,
            )
            embedding = None
            if self.model_service:
                try:
                    [embedding] = await self.model_service.embed_texts([payload_txt])
                except Exception:
                    embedding = None
            res = await self.ctx_browser.save_artifact(
                kind="solver.failure",
                tenant=tenant,
                project=project,
                user_id=user,
                conversation_id=conversation_id,
                user_type=user_type,
                turn_id=turn_id,
                track_id=track_id,
                content=payload,
                content_str=content_str,
                embedding=embedding,
                ttl_days=_ttl_for(user_type, 365),
                meta={
                    "title": "Solver Failure Presentation",
                    "kind": "solver.failure",
                    "request_id": rid,
                },
                bundle_id=self.config.ai_bundle_spec.id,
                index_only=True,
            )
            uri = res.get("hosted_uri")
            mid = res.get("message_id")

        return {
            "role": "artifact",
            "text": payload_txt,
            "extra": {"message_id": mid, "uri": uri},
            "payload": payload,
            "meta": {
                "kind": "solver.failure",
            }
        }

    async def _snapshot_execution_tree(
            self,
            *,
            rid: str,
            outdir: Optional[str],
            workdir: Optional[str],
            tenant: str, project: str, user: str, conversation_id: str,
            user_type: str, turn_id: str, track_id: str, codegen_run_id: str
    ):
        """
        Save /out and /pkg (if present) into the ConversationStore snapshot API.
        Also persist an artifact indexed as `codegen.program.exec` that *points* to
        the snapshot directories (best-effort: dir/prefix/s3_prefix/root/base_uri).
        """
        snap = await self.store.put_execution_snapshot(
            tenant=tenant, project=project, user=user, fingerprint=None,
            conversation_id=conversation_id, turn_id=turn_id,
            out_dir=outdir, pkg_dir=workdir,
            track_id=track_id, codegen_run_id=codegen_run_id,
            user_type=user_type
        )

        def _dir_of(node: dict) -> str:
            node = node or {}
            return (node.get("dir") or node.get("prefix") or node.get("s3_prefix")
                    or node.get("root") or node.get("base_uri") or "")

        mani = snap or {}
        out = (mani.get("out") or {})
        pkg = (mani.get("pkg") or {})
        out_files = len(out.get("files") or [])
        pkg_files = len(pkg.get("files") or [])
        out_dir = _dir_of(out)
        pkg_dir = _dir_of(pkg)

        # compact header string drives index text; we point to the dirs
        payload_txt = (
            "[codegen.program.exec]\n"
            f"out_dir={out_dir};"
            f"pkg_dir={pkg_dir};"
            f"out_files={out_files};"
            f"pkg_files={pkg_files};"
        )
        return snap

    async def persist_user_message(self, scratch: CTurnScratchpad):

        tenant, project, user, user_type, request_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"]
        conversation_id, turn_id, track_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"], self._ctx["conversation"]["track_id"]

        if getattr(scratch, "user_message_persisted", False):
            return

        # (5) persist + index the USER message
        t5a, ms5a = _tstart()
        truncated_text = truncate_text_by_tokens(scratch.user_text)
        [scratch.uvec] = await self.model_service.embed_texts([truncated_text])
        timing_user_embed = _tend(t5a, ms5a)

        step_title = "User message embedded"
        scratch.timings.append({"title": step_title, "elapsed_ms": timing_user_embed["elapsed_ms"]})

        # (attachments module) after you ingest/host attachments:
        # att_lines like ["budget.xlsx (xlsx, 12 KB)", "incident.txt (txt, 1.1 KB)"]
        # Also index their extracted text into rag_chunks(corpus='attachments', ...).
        # TODO!
        # if scratch.user_attachments:
        #     scratch.tlog.attachments(" / ".join(scratch.user_attachments))

        t5, ms5 = _tstart()
        ts = self._ctx["conversation"]["ts"]
        prompt_text = (scratch.user_text or "").strip()
        prompt_summary = (scratch.user_input_summary or "").strip()
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
            tags=["chat:user", f"track:{track_id}", f"turn:{turn_id}"] + [f"topic:{t}" for t in scratch.turn_topics_plain or []],
            ttl_days=_ttl_for(user_type, 365),
            user_type=user_type,
            embedding=scratch.uvec,
            message_id=msgid_u,
            track_id=track_id,
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

    async def persist_assistant(self, scratchpad: CTurnScratchpad):

        tenant, project, user, user_type, request_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"]
        conversation_id, turn_id, track_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"], self._ctx["conversation"]["track_id"]

        t14, ms14 = _tstart()
        scratchpad.avec = (await self.model_service.embed_texts([scratchpad.answer]))[0] if scratchpad.answer else None
        answer_for_storage = (scratchpad.answer_raw or scratchpad.answer or "")
        completion_text = answer_for_storage.strip()
        completion_summary = ""
        if isinstance(scratchpad.turn_summary, dict):
            completion_summary = (scratchpad.turn_summary.get("assistant_answer") or "").strip()
        sources_used = list(scratchpad.answer_used_sids or md_utils.sids_in_text(answer_for_storage))

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
            tags=["chat:assistant", f"track:{track_id}", f"turn:{turn_id}"] + [f"topic:{t}" for t in scratchpad.turn_topics_plain or []],
            ttl_days=_ttl_for(user_type, 365),
            user_type=user_type,
            embedding=scratchpad.avec,
            message_id=msgid_a,
            track_id=track_id
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

        track_id = self._ctx["conversation"]["track_id"]
        async with with_accounting(
                self.config.ai_bundle_spec.id,
                track_id=track_id,
                agent="attachment.summarizer",
                metadata={"track_id": track_id, "agent": "attachment.summarizer"},
        ):
            await scratchpad.summarize_user_attachments_for_turn_log(
                svc=self.model_service,
                max_ctx_chars=max_ctx_chars,
                max_tokens=max_tokens,
            )


    async def _process_prefs_and_policy(self, *, scratchpad: CTurnScratchpad, pre_out: dict):
        conversation_id = self._ctx["conversation"]["conversation_id"]

        active_set = scratchpad.active_set or {}
        if scratchpad.is_new_conversation:
            conversation_title = pre_out.get("conversation_title")
            active_set["conversation_title"] = conversation_title
            scratchpad.conversation_title = conversation_title
            scratchpad.active_set = active_set
            scratchpad.active_set_dirty = True
            # Persist the title immediately so fetch_conversation_artifacts can see it.
            try:
                # Allow re-flush if we already persisted the initial active-set pointer.
                scratchpad._active_set_flushed = False
                await self._flush_active_set(scratchpad)
            except Exception:
                pass

        await self.emit_conversation_title(conversation_id=conversation_id, turn_id=self._ctx["conversation"]["turn_id"], title=scratchpad.conversation_title)

    async def handle_feedback(self, scratchpad: CTurnScratchpad, gate):
        tenant, project, user, user_type = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"]
        conversation_id, track_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["track_id"]
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
                    track=track_id,
                    scoring_mode="custom",
                    custom_score_fn=feedback_scoring,
                    top_k=5,
                    days=365,
                    with_payload=True
                )
                # hits = [{**h, "log_payload": (h.get("payload") or {}).get("payload") or {}} for h in hits]
                hits = [{**h, "log_payload": h.get("payload") or {}} for h in hits]

                target_turn = next(iter([h for h in hits or [] if h["turn_id"] == target_tid]), None)

                # Fallbacks
                if not target_tid:
                    ltd = list(((scratchpad.guess_ctx or {}).get("last_turns_details") or []))
                    ltd.sort(key=lambda x: x.get("ts") or "", reverse=True)
                    target_turn = next((it for it in ltd if it.get("turn_id")), None)
                    target_tid = target_turn.get("turn_id") if target_turn else None
                if not target_tid:
                    mats = list(scratchpad.materialize_turn_ids or [])
                    target_tid = mats[-1] if mats else None
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
                            conversation_id=conversation_id, track_id=track_id,
                            origin="machine",
                        )

                        # 2) Apply feedback to target turn log (update the actual turn)
                        await self.ctx_client.apply_feedback_to_turn_log(
                            tenant=tenant,
                            project=project,
                            user=user,
                            user_type=user_type,
                            conversation_id=conversation_id,
                            track_id=track_id,
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
                            turn_summary = target_turn.get("turn_summary") or target_turn.get("log_payload", {}).get("turn_summary") or {}
                            ts = target_turn.get('ts')
                            # Handle both datetime objects and ISO strings
                            if isinstance(ts, str):
                                ts_str = ts[:16]
                            elif hasattr(ts, 'isoformat'):
                                ts_str = ts.isoformat()[:16]
                            else:
                                ts_str = str(ts)[:16] if ts else ""

                            objective = turn_summary.get('objective', '')
                            if ts_str and objective:
                                target_turn_details = f" originated on {ts_str} with objective: {objective}"
                        trace_ = (
                            f"{feedback_text} (confidence={feedback_confidence}; "
                            f"to turn {target_tid}{target_turn_details}; origin=machine)"
                        )
                        scratchpad.tlog.feedback(trace_)
                        self.logger.log(f"Feedback applied. {trace_}; conversation_id={conversation_id};")

                    except Exception:
                        self.logger.log(traceback.format_exc(), "ERROR")
            except Exception:
                self.logger.log(traceback.format_exc(), "ERROR")


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

    def _graph_enabled(self) -> bool:
        return bool(getattr(self.graph, "enabled", False))

    def _build_solver(self,
                      turn_view_class: Optional[Type] = None,
                      mod_tools_spec: Optional[List[Dict[str, Any]]] = None,
                      mcp_tools_spec: Optional[List[Dict[str, Any]]] = None,
                      tools_runtime: Optional[Dict[str, str]] = None,
                      custom_skills_root: Optional[str] = None,
                      skills_visibility_agents_config: Optional[Dict[str, Dict[str, Any]]] = None) -> SolverSystem:

        if turn_view_class is None:
            turn_view_class = TurnView
        spec = self.config.ai_bundle_spec
        if spec and spec.module and spec.path:
            # This is how you compute bundle_root elsewhere
            bundle_root = pathlib.Path(spec.path).joinpath(
                "/".join(spec.module.split(".")[:-1])
            )
        else:
            # Fallback: directory above orchestrator/ (the bundle root)
            bundle_root = pathlib.Path(__file__).resolve().parents[1]

        # Bridge for local tools to reach orchestrator services safely.
        # This proxy embeds the query and calls KB hybrid search.
        async def _kb_proxy(query: str, top_n: int = 8, providers: Optional[List[str]] = None):
            vec = (await self.model_service.embed_texts([query]))[0]
            return await self.kb.hybrid_search(
                query=query, embedding=vec, top_n=top_n,
                include_expired=False, providers=(providers or None)
            )
        self.ctx_client = ContextRAGClient(conv_idx=self.conv_idx,
                                           store=self.store,
                                           default_ctx_path=str(bundle_root / "context.json"),
                                           model_service=self.model_service,)
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

        return SolverSystem(
            service=self.model_service,
            comm=self.comm,
            comm_context=self.comm_context,
            context_rag_client=self.ctx_client,
            logger=self.logger,
            tool_subsystem=tool_subsystem,
            mcp_subsystem=mcp_subsystem,
            skills_descriptor={
                "custom_skills_root": str(custom_skills_root) if custom_skills_root else None,
                "agents_config": skills_visibility_agents_config,
            },
            bundle_spec=self.config.ai_bundle_spec,
            registry={  # plugins can pull these in bind_registry(...)
                "kb_client": self.kb # <- pre-initialized KBClient instance
            },
            turn_view_class=turn_view_class,
            hosting_service=self.hosting_service,
        )


    async def _handle_memory_route(self, scratchpad: CTurnScratchpad):
        # Keep a tiny newest window of delta FPS as HINTS only
        window = list(scratchpad.delta_turns_local_mem_entries or [])
        window.sort(key=lambda d: (d.get("made_at") or d.get("ts") or ""), reverse=True)
        # newest first -> cap -> restore oldest->newest
        window = list(reversed(window[:25]))
        # window = list(reversed(window))
        scratchpad.delta_turns_local_mem_entries = window
        # memory logs will be filled AFTER reranker once buckets are picked
        scratchpad.selected_local_memories_turn_ids = []

    async def _handle_turn_insights(self, *, scratchpad: CTurnScratchpad, g: dict):
        """
        Turn-time insights handling:

        - Build LLM-facing context strictly as:
            [RECONCILED ACTIVE SET] = last snapshot from graph (scratchpad.active_set)
            [NON-RECONCILED TURN INSIGHTS] = scratchpad.delta_fps_filtered (since last reconcile)
            [CURRENT TURN] = current_turn_log  (separate block)
        """

        scratchpad.proposed_facts = []


    # -------------------- Conv topics --------------------
    async def update_conv_topics(self, scratch: CTurnScratchpad):

        tenant, project, user = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"]
        conversation_id = self._ctx["conversation"]["conversation_id"]

        topics = scratch.turn_topics_plain
        if scratch.turn_topics_plain:
            scratch.tlog.note(f"topics: " + ", ".join(scratch.turn_topics_plain))

        is_new_conversation = scratch
        # (4) store conversation meta
        t4, ms4 = _tstart()
        topics_meta = { "topic_updated_at": int(time.time()) }
        if topics:
            topics_meta["topic_latest"] = topics[0]
            topics_meta["topics"] = topics
            topics_meta["topics_turn"] = json.dumps(scratch.turn_topics_with_confidence, ensure_ascii=False)
        if self._graph_enabled():
            await self.graph.set_conversation_meta(tenant=tenant, project=project, conversation=conversation_id, fields=topics_meta)

        timing_meta = _tend(t4, ms4)

        step_title = "Conversation Topics Updated"
        await self._emit({"type": "chat.conversation.topics", "agent": "policy", "step": "conversation.topics",
                          "status": "completed", "title": step_title,
                          "data": {"topics": topics}, "timing": timing_meta})
        scratch.timings.append({
            "title": step_title,
            "elapsed_ms": timing_meta["elapsed_ms"]
        })

    # -------------------- Ticket helpers (via ConvTicketIndex) --------------------

    async def _load_open_ticket(self, user_id: str, conversation_id: str,
                                turn_id: Optional[str] = None, track_id: Optional[str] = None) -> Optional[dict]:
        t = await self.ticket_index.fetch_latest_open_ticket(user_id=user_id,
                                                             conversation_id=conversation_id,
                                                             turn_id=turn_id,
                                                             track_id=track_id)
        return _to_jsonable(t) if t else None

    async def _resolve_ticket(self, *, ticket_id: str, answer_text: Optional[str]) -> None:
        await self.ticket_index.resolve_ticket(
            ticket_id=ticket_id,
            answered=bool(answer_text),
            answer_text=answer_text,
            append_to_description=True,
            result_tag_prefix="result",
            embed_texts_fn=self.model_service.embed_texts
        )
        await self._emit(
            {
                "type": "ticket",
                "agent": "planner",
                "step": "ticket.resolve",
                "status": "completed",
                "title": "Ticket Resolved",
                "data": {"ticket_id": ticket_id, "answer_present": bool(answer_text)},
            }
        )

    async def _create_clarification_ticket(self, *,
                                           title: str,
                                           questions: List[str],
                                           data: dict) -> Optional[Ticket]:
        user_id, conversation_id, track_id, turn_id = (self._ctx["service"]["user"], self._ctx["conversation"]["conversation_id"],
                                                       self._ctx["conversation"]["track_id"], self._ctx["conversation"]["turn_id"])
        try:
            ticket = await self.ticket_index.open_clarification_ticket(
                track_id=track_id,
                user_id=user_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                title=title,
                questions=questions,
                tags=["clarification", "qa"],
                priority=2,
                embed_texts_fn=self.model_service.embed_texts,
                data=data
            )

            if ticket:
                await self._emit(
                    {
                        "type": "ticket",
                        "agent": "planner",
                        "step": "ticket.create",
                        "status": "completed",
                        "title": "Clarification Ticket Created",
                        "data": {"ticket_id": ticket.ticket_id, "title": title, "questions": questions},
                    }
                )
            return ticket
        except Exception:
            return None

    # -------------------- Followups --------------------
    async def persist_clarifications_suggestions(self, scratchpad: CTurnScratchpad):

        tenant, project, user, user_type, request_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"]
        conversation_id, turn_id, track_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"], self._ctx["conversation"]["track_id"]

        # (15.5) Summarize step timings → scratchpad artifact + end-of-turn log
        try:
            timings_list = [t for t in scratchpad.timings if isinstance(t.get("elapsed_ms"), int)]
            # Add to scratchpad so it’s persisted as an artifact
            scratchpad.add_artifact(kind="perf-steps", title="Turn Step Timings (ms)", content=json.dumps(timings_list, ensure_ascii=False))
        except Exception:
            # timing summary should never break the turn
            pass

        await self._store_short_artifacts(
            scratchpad,
            tenant=tenant,
            project=project,
            user=user,
            conversation_id=conversation_id,
            turn_id=turn_id,
            user_type=user_type,
            track_id=track_id,
            request_id=request_id,
        )

        # (16) persist scratchpad
        t15, ms15 = _tstart()
        await self._update_graph_with_local_memories(
            scratchpad,
            tenant=tenant,
            project=project,
            user=user,
            conversation_id=conversation_id,
            turn_id=turn_id,
            user_type=user_type,
        )
        await self._store_followups(
            scratchpad,
            tenant=tenant,
            project=project,
            user=user,
            conversation_id=conversation_id,
            turn_id=turn_id,
            user_type=user_type,
            track_id=track_id,
            request_id=request_id,
        )
        await self._store_clarification_questions(
            scratchpad,
            tenant=tenant,
            project=project,
            user=user,
            conversation_id=conversation_id,
            turn_id=turn_id,
            user_type=user_type,
            track_id=track_id,
            request_id=request_id,
        )
        timing_scratch = _tend(t15, ms15)

        # 17) follow-ups and clarifications
        if scratchpad.clarification_questions:
            await self._emit({"type": "chat.clarification_questions", "agent": "answer.generator", "step": "clarification_questions",
                              "status": "completed", "title": "Clarification Questions", "data": {"items": scratchpad.clarification_questions}})
        if scratchpad.user_shortcuts:
            await self._emit({"type": "chat.followups", "agent": "answer.generator", "step": "followups",
                              "status": "completed", "title": "Follow-ups: User Shortcuts", "data": {"items": scratchpad.user_shortcuts}})
        step_title = "Scratchpad Persisted"
        scratchpad.timings.append({
            "title": step_title,
            "elapsed_ms": timing_scratch["elapsed_ms"]
        })

    async def _store_short_artifacts(
            self,
            scratch: TurnScratchpad,
            *,
            tenant: str,
            project: str,
            user: str,
            conversation_id: str,
            turn_id: str,
            user_type: str,
            track_id: str,
            request_id: str,
    ) -> None:
        if not scratch.short_artifacts:
            return
        if not self.ctx_client:
            return
        for item in (scratch.short_artifacts or []):
            if not isinstance(item, dict):
                continue
            kind = (item.get("kind") or "").strip()
            if not kind:
                continue
            payload = {
                "title": item.get("title") or kind,
                "content": item.get("content") or "",
            }
            if item.get("structured_content"):
                payload["structured_content"] = item.get("structured_content")
            try:
                await self.ctx_browser.save_artifact(
                    kind=kind,
                    tenant=tenant,
                    project=project,
                    user_id=user,
                    conversation_id=conversation_id,
                    user_type=user_type,
                    turn_id=turn_id,
                    track_id=track_id,
                    content=payload,
                    content_str=str(payload.get("content") or ""),
                    bundle_id=self.config.ai_bundle_spec.id,
                    meta={
                        "title": payload.get("title") or kind,
                        "kind": kind,
                        "request_id": request_id,
                    },
                )
            except Exception:
                self.logger.log(traceback.format_exc(), "ERROR")

    # -------------------- Create solver --------------------
    async def run_solver(self, scratchpad: CTurnScratchpad, solver, allowed_plugins):

        tenant, project, user, user_type, request_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"]
        conversation_id, turn_id, track_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"], self._ctx["conversation"]["track_id"]

        t10, ms10 = _tstart()

        topic_hint = ", ".join((scratchpad.turn_topics_plain or []))

        policy_summary = ""
        sr = await solver.solve(
            request_id=request_id,
            user_text=scratchpad.user_text,
            policy_summary=policy_summary,                         # short, human + ranked
            topics=scratchpad.turn_topics_plain,
            topic_hint=topic_hint,
            prefs_hint=scratchpad.extracted_prefs,                 # this turn extracted prefs
            allowed_plugins=allowed_plugins,
            context_hint=scratchpad.guess_ctx_str,
            materialize_turn_ids=scratchpad.materialize_turn_ids,
            scratchpad=scratchpad
        )
        await self._emit_turn_work_status(
            [
                "closing the loop",
                "final pass",
                "wrapping up",
                "summarizing work",
                "tying it together",
            ]
        )

        timing_solve = _tend(t10, ms10)
        # scratchpad.timings.append({"title": "Solver", "elapsed_ms": timing_solve["elapsed_ms"]})
        if getattr(sr, "plan", None) and getattr(sr.plan, "mode", "") == "clarification_only":
            # carry questions forward and run your existing clarification route
            scratchpad.clarification_questions = list(sr.plan.clarification_questions or [])
            await self._clarification_only_route_setup_ticket_if_needed(track_id=track_id, scratchpad=scratchpad)
            out = await self._clarification_only_route(scratchpad)
            await self.finish_turn(scratchpad)
            return {"status": "clarification_only", "output": out}

        await sr.enrich_used_citations_with_favicons()
        if getattr(sr, "execution", None) and getattr(sr.execution, "sources_pool", None) is not None:
            scratchpad.sources_pool = sr.execution.sources_pool
        scratchpad.solver_result = sr
        codegen_run_id = sr.run_id()
        outdir, workdir = sr.outdir_workdir()

        # scratchpad.citations = sr.citations()
        scratchpad.citations = sr.citations_used_only()

        step_title =  "Solver"
        scratchpad.timings.append({"title": step_title, "elapsed_ms": timing_solve["elapsed_ms"]})

        solver_status = SolveResult.status(sr)
        scratchpad.solver_status = solver_status
        # Handle complete failure (no deliverables at all)

        step_title = f"Solver - {solver_status.replace('_', ' ').title()}"
        event_type = f"chat.step"
        step = f"solver_{solver_status}"
        # ═══════════════════════════════════════════════════════════
        # CASE 1: COMPLETE FAILURE (no deliverables at all)
        # ═══════════════════════════════════════════════════════════
        if solver_status == "failed":
            solver_failure_artifact = await self._persist_solver_failure(tenant=tenant, project=project, user=user,
                                                                         conversation_id=conversation_id, user_type=user_type,
                                                                         turn_id=turn_id, rid=request_id,
                                                                         track_id=track_id,
                                                                         sr=sr)

            await self._emit({
                "type": event_type,
                "agent": "solution_engine.solver",
                "step": step,
                "status": "completed",
                "title": step_title,
                "data": solver_failure_artifact,
                "timing": timing_solve
            })
            scratchpad.turn_artifact = solver_failure_artifact

            # snapshot tree (stores `codegen.program.exec` pointing to dirs)
            if outdir or workdir:
                await self._snapshot_execution_tree(
                    rid=request_id, outdir=outdir, workdir=workdir,
                    tenant=tenant, project=project, user=user,
                    conversation_id=conversation_id, user_type=user_type, turn_id=turn_id,
                    track_id=track_id, codegen_run_id=codegen_run_id
                )
            return  # Early exit - no deliverables to process

        # ═══════════════════════════════════════════════════════════
        # CASE 2: LLM_ONLY or NOT_SOLVABLE (no codegen/tools)
        # ═══════════════════════════════════════════════════════════
        if solver_status in ("llm_only", "not_solvable"):
            await self._emit({
                "type": event_type,
                "agent": "solver",
                "step": step,
                "status": "completed",
                "title": step_title,
                "data": _to_jsonable(sr),
                "timing": timing_solve
            })
            return   # Early exit - no artifacts to process

        # ═══════════════════════════════════════════════════════════
        # CASE 3 & 4: PARTIAL or SUCCESS (has some/all deliverables)
        # ═══════════════════════════════════════════════════════════

        # Create failure artifact for partial (shows what's missing)
        if sr.partial_failure:
            scratchpad.turn_artifact = await self._persist_solver_failure(
                tenant=tenant, project=project, user=user,
                conversation_id=conversation_id, user_type=user_type,
                turn_id=turn_id, rid=request_id, track_id=track_id, sr=sr
            )

        # Emit solver result (for both partial and success)
        await self._emit({
            "type": event_type,
            "agent": "solver",
            "step": step,
            "status": "completed",
            "title": step_title,
            "data": _to_jsonable(sr),
            "timing": timing_solve
        })

        # Process completed deliverables (works for both partial and success)
        # deliverable_out = sr.deliverables_out()
        citations = sr.citations_used_only()

        rehosted = []

        # Emit citations to chat (files already emitted by ReAct when hosted)
        t12b, ms12b = _tstart()
        await self.hosting_service.emit_solver_artifacts(files=[], citations=citations)
        timing_emit_citations = _tend(t12b, ms12b)
        scratchpad.timings.append({
            "title": "workflow.emit_citations",
            "elapsed_ms": timing_emit_citations["elapsed_ms"],
        })

        # Persist artifacts (deliverables now read from turn log; no separate artifact)
        # t12d, ms12d = _tstart()
        # (solver.program.out) persistence disabled; turn log is the source of truth
        # t12e, ms12e = _tstart()
        # (solver.program.citables/files) persistence disabled; turn log is the source of truth
        try:
            t12g, ms12g = _tstart()
            await self.persist_project_log_from_sr(
                sr=sr,
                tenant=tenant, project=project, user=user, conversation_id=conversation_id,
                user_type=user_type, turn_id=turn_id, rid=request_id, track_id=track_id
            )
            timing_project_log = _tend(t12g, ms12g)
            scratchpad.timings.append({
                "title": "workflow.persist_project_log",
                "elapsed_ms": timing_project_log["elapsed_ms"],
            })
        except Exception:
            # non-fatal
            pass

        # snapshot tree (stores `codegen.program.exec` pointing to dirs)
        if outdir or workdir:
            t12h, ms12h = _tstart()
            await self._snapshot_execution_tree(
                rid=request_id, outdir=outdir, workdir=workdir,
                tenant=tenant, project=project, user=user,
                conversation_id=conversation_id, user_type=user_type, turn_id=turn_id,
                track_id=track_id, codegen_run_id=codegen_run_id
            )
            timing_snapshot = _tend(t12h, ms12h)
            scratchpad.timings.append({
                "title": "workflow.snapshot_exec_tree",
                "elapsed_ms": timing_snapshot["elapsed_ms"],
            })

        if not solver_status == "success":
            return

        t12r, ms12r = _tstart()
        # (14) build program presentation
        program_prez_str = sr.program_presentation_ext
        presentation_artifact = await self._persist_program_presentation(
            tenant=tenant, project=project, user=user,
            conversation_id=conversation_id, user_type=user_type,
            turn_id=turn_id, rid=request_id, track_id=track_id,
            markdown=program_prez_str
        )

        timing_presentation = _tend(t12r, ms12r)
        scratchpad.turn_artifact = presentation_artifact
        scratchpad.solver_result_interpretation_instruction = sr.interpretation_instruction()

        await self._emit({
            "type": "chat.step",
            "agent": "retriever",
            "step": "chat.program_presentation",
            "status": "completed",
            "title": "Program artifact",
            "data": presentation_artifact,
            "timing": timing_presentation
        })

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
        track_id = "A"

        # bind for envelope composition
        self._ctx["service"] = {"request_id": rid, "tenant": tenant, "project": project,
                                "user": user, "user_type": user_type, "session_id": session_id}
        self._ctx["conversation"] = {"conversation_id": conversation_id,
                                     "turn_id": turn_id,
                                     "track_id": track_id,
                                     "ts": datetime.datetime.utcnow().isoformat() + "Z"}
        scratchpad = CTurnScratchpad(user=user,
                                     conversation_id=conversation_id,
                                     turn_id=turn_id,
                                     text=text.strip(),
                                     attachments=attachments)
        scratchpad.user_ts = self._ctx["conversation"].get("ts")
        # Bundles can override gate_out_class after construction if they use a custom gate contract.

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

    async def start_turn(self, scratchpad: CTurnScratchpad):

        tenant, project, user, user_type, request_id, session_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"], self._ctx["service"]["session_id"]
        conversation_id, turn_id, track_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"], self._ctx["conversation"]["track_id"]

        # (0) ensure User ↔ Conversation link (cheap + idempotent)
        t_turn0 = time.perf_counter()
        t0u, ms0u = _tstart()
        self._ctx["turn"] = {
            "t_turn0": t_turn0,
            "t0u": t0u,
            "ms0u": ms0u,
        }
        try:
            if self._graph_enabled():
                await self.graph.ensure_user_and_conversation(
                    tenant=tenant, project=project, user=user,
                    conversation=conversation_id, user_type=user_type
                )
        finally:
            timing_uconv = _tend(t0u, ms0u)
            step_title = "User↔Conversation Linked"
            status = "completed" if self._graph_enabled() else "skipped"
            await self._emit({"type": "chat.step", "agent": "graph", "step": "context.graph",
                              "status": status, "title": step_title,
                              "data": {"user": user, "conversation": conversation_id},
                              "timing": timing_uconv})
            scratchpad.timings.append({
                "title": step_title,
                "elapsed_ms": timing_uconv["elapsed_ms"]
            })

        # start of turn
        prompt_text = scratchpad.short_text
        try:
            full_text = (scratchpad.user_text or "").strip()
            if full_text and len(full_text) > len(prompt_text):
                prompt_text = f"{prompt_text} [truncated]"
        except Exception:
            pass
        scratchpad.tlog.user_prompt(prompt_text)

        await self._summarize_user_attachments(scratchpad)
        await self._persist_attachment_summaries(scratchpad)
        self._log_attachments_in_turn_log(scratchpad)

        if self.ctx_client:
            try:
                await _preload_conversation_memory_state(
                    scratchpad=scratchpad,
                    active_store=self.conv_memories,
                    _ctx=self._ctx,
                    ctx_client=self.ctx_client,
                    bundle_id=self.config.ai_bundle_spec.id,
                    assistant_signal_scope=getattr(scratchpad, "assistant_signal_scope", None) or "user",
                )
                await self._emit_turn_work_status(
                    [
                        "distilling",
                        "indexing",
                        "compacting",
                        "organizing the thread",
                    ]
                )
            except Exception:
                self.logger.log(traceback.format_exc(), "ERROR")

        # (1) user message
        await self._emit({"type": "chat.conversation.accepted", "agent": "user", "step": "chat.user.message", "status": "completed",
                          "title": "User Message", "data": {"text": scratchpad.short_text, "chars": len(scratchpad.short_text)}})
        self.logger.log_step("recv_user_message", {"len": len(scratchpad.user_text)})
        # await self.comm.delta(text=scratchpad.short_text, index=0, marker="thinking", agent="workflow", completed=False)

        self.logger.start_operation(
            "orchestrator.process",
            request_id=request_id, tenant=tenant, project=project, user=user,
            session=session_id, conversation=conversation_id, text_preview=scratchpad.tlog.user_entry,
        )

    async def finish_turn(self,
                          scratchpad: CTurnScratchpad,
                          ok: bool = True,
                          result_summary: str | None = None,
                          on_flush_completed_hook: Optional[Callable[[CTurnScratchpad], Awaitable[None]]] = None):
        # prevent double-finish from multiple branches / nested handlers
        if getattr(scratchpad, "_turn_finished", False):
            return
        scratchpad._turn_finished = True

        tenant, project, user, user_type, request_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"]
        conversation_id, turn_id, track_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"], self._ctx["conversation"]["track_id"]
        t_turn0, ms0u = self._ctx["turn"]["t_turn0"], self._ctx["turn"]["ms0u"]

        prefs = scratchpad.extracted_prefs if isinstance(scratchpad.extracted_prefs, dict) else {}
        if prefs and (prefs.get("assertions") or prefs.get("exceptions")) and isinstance(scratchpad.turn_summary, dict):
            scratchpad.turn_summary = dict(scratchpad.turn_summary)
            scratchpad.turn_summary["prefs"] = prefs

        scratchpad.tlog.turn_summary(scratchpad.turn_summary)
        # done, not done, issues
        if scratchpad.user_shortcuts:
            scratchpad.tlog.note("suggestions: " + ";".join(scratchpad.user_shortcuts))

        scratchpad.tlog.ended_at_iso = datetime.datetime.utcnow().isoformat()+"Z"

        # Mark citations used by the answer in sources_pool for this turn log.
        if scratchpad.sources_pool and scratchpad.answer_used_sids:
            used = set(int(s) for s in scratchpad.answer_used_sids if isinstance(s, (int, float)))
            for src in scratchpad.sources_pool:
                try:
                    sid = src.get("sid")
                    if isinstance(sid, (int, float)) and int(sid) in used:
                        src["used"] = True
                except Exception:
                    continue

        topic_tags = [f"topic:{_norm_topic(t)}" for t in (scratchpad.turn_topics_plain or [])]

        if ok:
            try:
                await self._persist_turn_fingerprint(scratchpad)
            except Exception:
                pass

        # Save turn log (always)
        await self.ctx_client.save_turn_log_as_artifact(
            tenant=tenant, project=project, user=user,
            conversation_id=conversation_id, user_type=user_type,
            turn_id=turn_id, track_id=track_id,
            bundle_id=self.config.ai_bundle_spec.id,
            log=scratchpad.tlog,
            payload=scratchpad.turn_log,
            extra_tags=topic_tags,
        )
        # if scratchpad.sources_pool:
        #     await self.ctx_client.save_artifact(
        #         kind="conv:sources_pool",
        #         tenant=tenant, project=project, user_id=user,
        #         conversation_id=conversation_id, user_type=user_type,
        #         turn_id=turn_id, track_id=track_id,
        #         bundle_id=self.config.ai_bundle_spec.id,
        #         content={"sources_pool": scratchpad.sources_pool},
        #         extra_tags=topic_tags,
        #     )

        if scratchpad.answer:
            await self.persist_assistant(scratchpad)
            await self.persist_clarifications_suggestions(scratchpad)

        # MEMORY management. post-answer reconciliation (cadenced). Only if turn finished w/ service error.
        if ok:
            if on_flush_completed_hook:
                await on_flush_completed_hook(scratchpad)
        # (19) done
        await self._flush_active_set(scratchpad)
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
        if show_error_in_timeline:
            await self.comm.error(message=message, data=data)
        else:
            # Keep it out of timeline; still produce an "answer" bubble
            await self.comm.delta(text=message, index=0, marker="answer", agent="turn_exception", completed=True)

        try:
            await self._flush_active_set(scratchpad)
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")

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

    async def reconcile_context(self,
                                scratchpad: CTurnScratchpad,
                                gate: dict,
                                mk_thinking_streamer_fn: Callable,
                                solver: SolverSystem):
        """
        1) Build GP v1 (no selected memories): last turns + current overlays + delta_fps (as hints).
        2) Fetch ACTIVE buckets, build cards; call ctx_reranker.
        3) Map picked buckets -> cards + timelines.
        4) Build GP v2 WITH selected memories and finalize filtered context.
        5) Persist choices on scratchpad (turn_ids, memory_bucket_ids, local_memories_turn_ids).
        """
        import json as _json
        from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_reconciler import ctx_reconciler_stream
        from kdcube_ai_app.apps.chat.sdk.context.memory.buckets import BucketStore, BucketCard, make_bucket_card, MemoryBucket, Signal, TimelineSlice

        user  = self._ctx["service"]["user"]
        conv  = self._ctx["conversation"]["conversation_id"]
        turn  = self._ctx["conversation"]["turn_id"]
        track = self._ctx["conversation"]["track_id"]

        # ---- 0) Gate inputs & light search -----------------------------------------
        targets = [t for t in gate_ctx_queries(gate)
                   if isinstance(t, dict) and t.get("where") and t.get("query")]
        try:
            def fmt_param(p, key): return key + "=" + (p.get(key, "") or "").strip()
            qts = [f"query {i}: ({fmt_param(t,'query')}; {fmt_param(t,'where')})"
                   for (i,t) in enumerate(targets) if t.get("query")]
            if qts:
                scratchpad.tlog.note(f"[gate.ctx_queries] ran queries: " + " OR ".join(qts))
        except Exception:
            pass

        def feedback_scoring(sim: float, rec: float, ts: str) -> float:
            # Prioritize similarity heavily, add small recency bias
            return 0.8 * sim + 0.2 * rec

        best_tid, hits = await self.ctx_browser.search(
            custom_score_fn=feedback_scoring,
            targets=targets,
            user=user,
            conv=conv,
            track=track,
            scoring_mode="hybrid",  # Use pre-weighted scores
            half_life_days=7.0,
            top_k=5,
            days=365,
            with_payload=True
        )

        # ✨ UNIFIED: Convert search hits to compressed view using TurnView
        search_hits_formatted = []
        for h in hits:
            try:
                tv = TurnView.from_turn_dict(h)
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
            except Exception as e:
                self.logger.log(f"Failed to format search hit: {e}", "WARNING")
                continue

        # ✨ UNIFIED: Convert search hits to minimal projections using TurnView
        # search_hits_projections = []
        # for h in hits:
        #    try:
        #        tv = TurnView.from_turn_dict(h)
        #        projection = tv.to_minimal_projection()
        #        # Keep original hit metadata
        #        projection["score"] = h.get("score")
        #        projection["source_query"] = h.get("source_query")
        #        search_hits_projections.append(projection)
        #    except Exception:
        #        continue

        hit_turn_ids: list[str] = []
        for h in hits or []:
            tid = h.get("turn_id")
            if tid and tid not in hit_turn_ids:
                hit_turn_ids.append(tid)
        scratchpad.relevant_turn_ids = hit_turn_ids

        # ---- 1) GP v1 (no selected memories) ---------------------------------------
        last_turns = 3 + int(getattr(scratchpad, "history_depth_bonus", 0) or 0)
        cur_fp  = getattr(scratchpad, "turn_fp", None)

        hit_queries_by_turn_id: dict[str, list[str]] = {}
        for h in search_hits_formatted:
            tid = h.get("turn_id")
            query = (h.get("source_query") or "").strip()
            if not tid:
                continue
            if not query:
                query = "(unspecified)"
            hit_queries_by_turn_id.setdefault(tid, [])
            if query not in hit_queries_by_turn_id[tid]:
                hit_queries_by_turn_id[tid].append(query)

        scratchpad.guess_ctx_str, scratchpad.guess_ctx = await ctx_presentation_module.retrospective_context_view(
            ctx_client=self.ctx_client,
            user_id=user, conversation_id=conv, turn_id=turn,
            last_turns=last_turns,
            recommended_turn_ids=scratchpad.relevant_turn_ids,
            context_hit_queries=hit_queries_by_turn_id,
            delta_fps=scratchpad.delta_turns_local_mem_entries,   # small newest window (hints)
            feedback_items=getattr(scratchpad, "feedback_conversation_level", None),
            scratchpad=scratchpad,
            context_bundle=getattr(scratchpad, "context_bundle", None),
            turn_view_class=TurnView
        )

        # Thin turn_memories to just {turn_id, ts, one_liner}
        raw_memories = ((scratchpad.guess_ctx or {}).get("turn_memories") or [])
        earlier_thin = []
        for it in raw_memories:
            tid = it.get("turn_id")
            if not tid:
                continue
            earlier_thin.append({
                "turn_id": tid,
                "ts": it.get("made_at") or it.get("ts") or "",
                "one_liner": "",
            })

        # ✨ UNIFIED: Convert last_turns to compressed view using TurnView
        last_turns_mate = (scratchpad.guess_ctx or {}).get("last_turns_mate", [])
        last_turns_formatted = []
        for it in last_turns_mate:
            try:
                # Build TurnView from the log_payload
                tv = TurnView.from_turn_dict(it)
                compressed_text = tv.to_compressed_search_view(include_turn_info=False)
                last_turns_formatted.append({
                    "turn_id": it.get("turn_id"),
                    "text": compressed_text,
                    "ts": it.get("ts") or "",
                })
            except Exception as e:
                self.logger.log(f"Failed to format recent turn: {e}", "WARNING")
                continue

        gp_for_reranker = {
            "turn_memories": earlier_thin,
            "last_turns_details":    last_turns_formatted,
            "current_turn_details":   (scratchpad.guess_ctx or {}).get("current_turn_details", None),
        }

        # ---- 2) ACTIVE buckets -> cards; rerank ------------------------------------
        bstore = BucketStore(self.ctx_client)
        active_bucket_docs = await bstore.list_buckets(
            user=user, conversation_id=conv, include_disabled=False
        )

        def _mk_mem_bucket(d: dict) -> MemoryBucket:
            tl = []
            for s in (d.get("timeline") or []):
                tl.append(TimelineSlice(
                    ts_from=s.get("ts_from",""), ts_to=s.get("ts_to",""),
                    objective_hint=s.get("objective_hint",""),
                    assertions=[Signal(**x) for x in (s.get("assertions") or [])],
                    exceptions=[Signal(**x) for x in (s.get("exceptions") or [])],
                    facts=[Signal(**x) for x in (s.get("facts") or [])],
                ))
            return MemoryBucket(
                version="v1",
                bucket_id=d.get("bucket_id",""),
                status=("enabled" if d.get("enabled", True) else "disabled"),
                name=d.get("name",""),
                short_desc=d.get("short_desc",""),
                topic_centroid=list(d.get("topic_centroid") or []),
                objective_text=d.get("objective_text") or d.get("name",""),
                updated_at=d.get("updated_at",""),
                timeline=tl
            )

        active_buckets = [_mk_mem_bucket(b) for b in active_bucket_docs]
        active_cards: list[BucketCard] = [make_bucket_card(b, max_per_kind=4) for b in active_buckets]
        bucket_cards_json = _json.dumps([c.model_dump() for c in active_cards], ensure_ascii=False)

        t04, ms04 = _tstart()
        track_id = self._ctx["conversation"]["track_id"]

        ctx_error = None
        try:
            async with with_accounting(self.config.ai_bundle_spec.id,
                                       track_id=track_id,
                                       agent="ctx.reconciler",
                                       metadata={
                                           "track_id": track_id,
                                           "agent": "ctx.reconciler"
                                       }):
                with scratchpad.phase("ctx.reconciler", agent="ctx.reconciler"):
                    rr = await ctx_reconciler_stream(
                        self.model_service,
                        guess_package_json=_jd(gp_for_reranker),
                        current_context_str=scratchpad.guess_ctx_str,
                        search_hits_json=_jd(search_hits_formatted),  # ✨ Now unified text format
                        bucket_cards_json=bucket_cards_json,
                        limit_ctx=10,
                        max_buckets=5,
                        gate_decision=gate,
                        on_thinking_delta=mk_thinking_streamer_fn(agent="ctx.reconciler"),
                        timezone=self.comm_context.user.timezone
                    )
                    logging_helpers.log_agent_packet("ctx.reconciler", "ctx", rr)
                    if rr and rr.get("error"):
                        err = rr["error"]
                        msg = (
                                  err.get("message") if isinstance(err, dict)
                                  else str(err)
                              ) or "Context and Local Memories Reconciler failed"
                        raise TurnPhaseError(
                            msg,
                            code="ctx.reconciler.error",
                            data={"ctx.reconciler.raw": rr},
                        )
            timing_gate = _tend(t04, ms04)
            # logging_helpers.log_agent_packet("context_and_memory_reconciler", "reconcile context", rr)
            scratchpad.timings.append({
                "title": "context.reconciler",
                "elapsed_ms": timing_gate["elapsed_ms"]
            })

            rro = rr.get("agent_response") or {}
            await self._emit({
                "type": "chat.step",
                "agent": "ctx_and_mem_reconciler",
                "step": "conversation.context_and_mem_reconciler",
                "status": "completed",
                "title": "Ctx and Local Mem Reconciler",
                "data": rro,
                "timing": timing_gate
            })

            try:
                scratchpad.register_agentic_response("ctx.reconciler",
                                                     CtxRerankOut.model_validate(rro))
            except Exception as e:
                scratchpad.register_agentic_response("ctx.reconciler", rro)

            user_input_summary = (rro.get("user_input_summary") or "").strip()
            if user_input_summary:
                scratchpad.user_input_summary = user_input_summary
            objective = (rro.get("objective") or "").strip()
            if objective:
                scratchpad.objective = objective
                scratchpad.tlog.objective(objective)
                self._update_turn_fp(
                    scratchpad,
                    objective=objective,
                )

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
                self._update_turn_fp(
                    scratchpad,
                    objective=getattr(scratchpad, "objective", None),
                    assertions=prefs_assertions,
                    exceptions=prefs_exceptions,
                    facts=prefs_facts,
                )
            elif prefs_facts:
                self._update_turn_fp(
                    scratchpad,
                    objective=getattr(scratchpad, "objective", None),
                    facts=prefs_facts,
                )
            if prefs_facts:
                scratchpad.proposed_facts = prefs_facts
        except Exception as e:
            ctx_error = e
        finally:
            await self.persist_user_message(scratchpad)
        if ctx_error is not None:
            raise ctx_error
        # Extract reranker decisions - respect empty lists!
        turn_ids = list((rro.get("turn_ids") or [])[:10])
        picked_bucket_ids: list[str] = list(rro.get("memory_bucket_ids") or [])
        picked_local_tids: list[str] = list(rro.get("local_memories_turn_ids") or [])

        # Clarifications from reranker (primary)
        # rr_qs = [q.strip() for q in (rro.get("clarification_questions") or []) if q.strip()][:2]
        rr_qs = [q.strip() for q in (rro.get("clarification_questions") or []) if q.strip()]
        if rr_qs:
            # store onto scratchpad; workflow will branch after reconcile_context()
            scratchpad.clarification_questions = rr_qs

        # Filter delta_fps by local_memories_turn_ids (fallback to original window if empty)
        if picked_local_tids:
            keep_set = set(picked_local_tids)
            kept_deltas = [d for d in (scratchpad.delta_turns_local_mem_entries or []) if d.get("turn_id") in keep_set]
        else:
            # Reranker decided no local memories are relevant - respect that
            # kept_deltas = scratchpad.delta_turns_local_mem_entries
            kept_deltas = []

        scratchpad.selected_turns_local_mem_entries = kept_deltas

        # ---- 3) Map picked buckets -> cards & timelines -----------------------------
        selected_bucket_cards = [c.model_dump() for c in active_cards if c.bucket_id in picked_bucket_ids]

        objective_memory_timelines = {}
        for b in active_buckets:
            if b.bucket_id in picked_bucket_ids:
                objective_memory_timelines[b.bucket_id] = [{
                    "ts_from": s.ts_from, "ts_to": s.ts_to, "objective_hint": s.objective_hint,
                    "assertions": [a.model_dump() for a in s.assertions],
                    "exceptions": [e.model_dump() for e in s.exceptions],
                    "facts":      [f.model_dump() for f in s.facts],
                } for s in (b.timeline or [])]

        # ---- 4) GP v2 WITH selected memories; then filter by turn_ids -------------
        # If reranker returns no turn_ids, proceed with fresh context
        # recent_turns_logs = (scratchpad.guess_ctx or {}).get("last_turns_details") or []
        # ---- CRITICAL FIX: Respect reranker's decisions ----
        # The reranker has already decided which turns are relevant.
        # Do NOT add fallback turns when turn_ids is empty!

        # Only use a minimal safety fallback if:
        # 1. Route requires context
        # 2. Reranker returned empty AND no buckets selected
        # 3. There are actually recent turns available
        # If reranker returned empty and no buckets selected, log it
        if not turn_ids and not picked_bucket_ids:
            self.logger.log(
                "[Context Reconciler] No relevant context selected - proceeding with fresh context",
                "INFO"
            )
        scratchpad.relevant_turn_ids = turn_ids
        scratchpad.materialize_turn_ids = turn_ids
        scratchpad.selected_memory_bucket_ids = picked_bucket_ids
        scratchpad.selected_local_memories_turn_ids = picked_local_tids
        scratchpad.selected_memory_bucket_cards = selected_bucket_cards

        # Materialize reconciled history once after ctx.reconciler
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.browser import ContextBrowser
            browser = ContextBrowser(
                ctx_client=self.ctx_client,
                logger=self.logger,
                turn_view_class=TurnView,
            )
            t_ctx, ms_ctx = _tstart()
            bundle = await browser.materialize(
                materialize_turn_ids=turn_ids,
                user_id=user,
                conversation_id=conv,
            )
            timing_ctx = _tend(t_ctx, ms_ctx)
            scratchpad.timings.append({
                "title": "context.gathering",
                "elapsed_ms": timing_ctx["elapsed_ms"]
            })
            scratchpad.context_bundle = bundle
            bundle_pool = getattr(bundle, "sources_pool", None)
            if bundle_pool:
                scratchpad.sources_pool = bundle_pool
        except Exception as e:
            self.logger.log(f"[Context Reconciler] Failed to materialize context bundle: {e}", "WARNING")

        scratchpad.guess_ctx_str, scratchpad.guess_ctx = await ctx_presentation_module.retrospective_context_view(
            ctx_client=self.ctx_client,
            user_id=user, conversation_id=conv, turn_id=turn,
            last_turns=last_turns,
            recommended_turn_ids=scratchpad.relevant_turn_ids, # Use reranker's filtered list
            context_hit_queries=hit_queries_by_turn_id,
            delta_fps=scratchpad.selected_turns_local_mem_entries,
            feedback_items=getattr(scratchpad, "feedback_conversation_level", None),
            scratchpad=scratchpad,
            # now pass the reranker choices:
            filter_turn_ids=(turn_ids or [])[:8],                         # ← FILTER HERE. If [] = no turns wanted
            selected_local_memories_turn_ids=picked_local_tids,           # ← FILTER HERE
            context_bundle=getattr(scratchpad, "context_bundle", None),
            turn_view_class=TurnView
        )

        # Update active set metadata
        try:
            as_ptr = scratchpad.active_set or {}
            as_ptr["picked_bucket_ids"] = picked_bucket_ids
            # optional: keep local memory picks for future heuristics/debug
            if picked_local_tids:
                as_ptr["selected_local_memories_turn_ids"] = picked_local_tids
            scratchpad.active_set = as_ptr
            scratchpad.active_set_dirty = True
        except Exception:
            # non-fatal; don't block the turn if meta write fails
            self.logger.log(traceback.format_exc(), "ERROR")
            pass

        # ---- 5) Logging -------------------------------------------------------------
        try:
            qs = list(dict.fromkeys([
                h.get("source_query", "") for h in hits
                if h.get("source_query")
            ]))[:3]
            why_hint = ", ".join([q.strip() for q in qs if q.strip()])
        except Exception:
            why_hint = ""

        ctx_trace = ""
        if why_hint:
            ctx_trace += f"[ctx.queries] {why_hint}\n"

        # Log which turns were selected (or note if none)
        if turn_ids:
            lines = []
            recent_turns_logs = (scratchpad.guess_ctx or {}).get("last_turns_details") or []
            for sn in recent_turns_logs[:6]:
                sn_tid = sn.get("turn_id") or ""
                if sn_tid not in turn_ids:
                    continue  # Skip turns not selected by reranker
                entries = (((sn.get("log_payload") or {}).get("payload") or {}).get("turn_log") or {}).get("entries") or []
                summary = next((entry for entry in entries if entry.get("area") == "summary"), None) or {}
                if not summary:
                    self.logger.log(f"Turn {sn_tid} has no summary entry in log payload", "WARNING")
                summary_content = summary.get("msg")
                if summary_content:
                    lines.append(f"{sn_tid}: {summary_content}")
            if lines:
                ctx_trace += "[ctx.used]: digest of past turns\n" + "\n".join(lines)
        else:
            ctx_trace += "[ctx.used]: no prior turns selected (fresh context)\n"
        if ctx_trace:
            scratchpad.tlog.note(ctx_trace)
