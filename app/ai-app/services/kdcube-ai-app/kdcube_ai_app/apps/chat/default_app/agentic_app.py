# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chatbot/default_app/agentic_app.py
# Simple bundle workflow with complex-bundle style streaming & step emissions (markdown composed)

from __future__ import annotations
import json
import asyncio
import pathlib

import time

from typing import List, Dict, Any, Optional, TypedDict, Annotated
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, AnyMessage
from langgraph.graph.message import add_messages
from datetime import datetime
import textwrap

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.infra.service_hub.inventory import Config, AgentLogger, _mid
from kdcube_ai_app.apps.chat.sdk.util import _json_schema_of
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage

# Loader decorators
from kdcube_ai_app.infra.plugin.agentic_loader import (
    agentic_initial_state,
    agentic_workflow,
)
from kdcube_ai_app.apps.chat.sdk.comm.emitters import AIBEmitters, DeltaPayload, StepPayload

# Local bundle imports
try:
    from .inventory import ThematicBotModelService, BUNDLE_ID, project_app_state, _history_to_seed_messages
    from .integrations.rag import RAGService
except ImportError:  # fallback when running as plain module
    from inventory import ThematicBotModelService, BUNDLE_ID, project_app_state, _history_to_seed_messages
    from integrations.rag import RAGService


def _now_ms() -> int:
    return int(time.time() * 1000)


# Optional summarization node
try:
    from langmem.short_term import SummarizationNode
except Exception:
    SummarizationNode = None


# =========================
# Structured outputs
# =========================

class ClassificationOutput(BaseModel):
    is_our_domain: bool
    confidence: float
    reasoning: str


class QueryWriterOutput(BaseModel):
    chain_of_thought: Optional[str]
    queries: List[Dict[str, Any]]


class RerankingOutput(BaseModel):
    reranked_docs: List[Dict[str, Any]]
    reasoning: str


# =========================
# Graph State
# =========================

class ChatGraphState(TypedDict):
    messages: Annotated[List[AnyMessage], add_messages]
    summarized_messages: List[AnyMessage]
    context: Dict[str, Any]
    user_message: str

    is_our_domain: Optional[bool]
    classification_reasoning: Optional[str]
    rag_queries: Optional[List[Dict[str, Any]]]
    retrieved_docs: Optional[List[Dict[str, Any]]]
    reranked_docs: Optional[List[Dict[str, Any]]]
    final_answer: Optional[str]

    thinking: Optional[str]
    followups: Optional[List[str]]
    internal_prelude: Optional[str]
    turn_log: Optional[Dict[str, Any]]

    error_message: Optional[str]
    format_fix_attempts: int

    search_hits: Optional[List[Dict[str, Any]]]

    execution_id: str
    start_time: float
    step_logs: List[Dict[str, Any]]
    performance_metrics: Dict[str, Any]

    turn_id: str


# =========================
# Utilities
# =========================

def add_step_log(state: ChatGraphState, step: str, data: Dict[str, Any]):
    log_entry = {
        "step": step,
        "timestamp": datetime.now().isoformat(),
        "elapsed_time": f"{time.time() - state['start_time']:.2f}s",
        "data": data
    }
    state["step_logs"].append(log_entry)


def get_execution_summary(state: ChatGraphState) -> Dict[str, Any]:
    return {
        "execution_id": state["execution_id"],
        "total_time": f"{time.time() - state['start_time']:.2f}s",
        "total_steps": len(state["step_logs"]),
        "performance_metrics": state["performance_metrics"],
        "step_logs": state["step_logs"]
    }


# ---------- Markdown helpers (parity with complex bundle) ----------

def _mk_compose(markdown: str, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Compose object used by complex bundle UIs.
    We include both `markdown` (legacy) and a `compose` block for maximum compatibility.
    """
    return {
        "markdown": markdown,
        "compose": {
            "blocks": [
                {"type": "md", "text": markdown}
            ],
            **(extra or {})
        }
    }

def _bar(value: float, width: int = 10) -> str:
    v = max(0.0, min(1.0, float(value)))
    filled = int(round(v * width))
    return "▮" * filled + "▯" * (width - filled)

def _pct(value: float) -> str:
    try:
        return f"{float(value)*100:.0f}%"
    except Exception:
        return "—"

def _truncate(s: str, n: int = 240) -> str:
    s = (s or "").strip().replace("\n", " ")
    return (s[: n - 1] + "…") if len(s) > n else s

def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows)
    return f"{head}\n{sep}\n{body}"

def _safe_md(s: str) -> str:
    return (s or "").replace("<", "&lt;").replace(">", "&gt;")

# Titles + emojis aligned with complex bundle
STEP_TITLES = {
    "workflow_start": "🚦 Kickoff",
    "summarize": "🧾 Summarize",
    "classifier": "🧭 Classifier",
    "query_writer": "🧩 Query Writer",
    "rag_retrieval": "📚 Retrieve",
    "reranking": "📊 Rerank",
    "answer_generator": "✍️ Compose Answer",
    "assistant_stream": "📡 Stream",
    "followups": "🧠 Follow-ups",
    "workflow_complete": "✅ Done",
}


# =========================
# Services / Agents
# =========================

class FormatFixerService:
    """Fixes malformed JSON responses using Claude (best-effort; safe if Anthropic SDK absent)."""

    def __init__(self, config: Config):
        self.config = config
        self.logger = AgentLogger(f"{BUNDLE_ID}.FormatFixer", config.log_level)
        try:
            import anthropic  # type: ignore
            self.claude_client = anthropic.Anthropic(api_key=config.claude_api_key)
            self.logger.log_step("claude_client_initialized", {"model": config.format_fixer_model})
        except Exception:
            self.claude_client = None
            self.logger.log_step("claude_client_unavailable", {})

    async def fix_format(self, raw_output: str, expected_format: str, input_data: str, system_prompt: str) -> Dict[str, Any]:
        op = self.logger.start_operation(
            "format_fixing",
            raw_output_length=len(raw_output),
            expected_format=expected_format,
            input_data_length=len(input_data),
        )
        if not self.claude_client:
            msg = "Claude client not available"
            self.logger.finish_operation(False, msg)
            return {"success": False, "error": msg, "raw": raw_output}

        try:
            fix_prompt = (
                "You are a JSON format fixer. You receive malformed JSON and must fix it to match the expected format.\n\n"
                f"Original system prompt:\n{system_prompt}\n\n"
                f"Original input:\n{input_data}\n\n"
                f"Expected type name: {expected_format}\n"
                f"Malformed output:\n{raw_output}\n\n"
                "Return ONLY valid JSON. No commentary."
            )
            resp = self.claude_client.messages.create(
                model=self.config.format_fixer_model,
                max_tokens=1000,
                messages=[{"role": "user", "content": fix_prompt}],
            )
            fixed_content = resp.content[0].text
            try:
                parsed = json.loads(fixed_content)
                self.logger.finish_operation(True, "Format fixing successful")
                return {"success": True, "data": parsed, "raw": fixed_content}
            except json.JSONDecodeError:
                self.logger.finish_operation(False, "Fixed content still invalid")
                return {"success": False, "error": "Fixed content is still not valid JSON", "raw": fixed_content}
        except Exception as e:
            self.logger.log_error(e, "format_fixing")
            self.logger.finish_operation(False, f"Format fixing failed: {e}")
            return {"success": False, "error": str(e), "raw": raw_output}


class ClassifierAgent:
    def __init__(self, config: Config, model_service: ThematicBotModelService):
        self.config = config
        self.logger = AgentLogger(f"{BUNDLE_ID}.ClassifierAgent", config.log_level)
        self.model_service = model_service
        self.format_fixer = FormatFixerService(config)

    async def classify(self, state: ChatGraphState) -> ChatGraphState:
        self.logger.start_operation(
            "classify_query",
            user_message=(state["user_message"][:100] + "...") if len(state["user_message"]) > 100 else state["user_message"],
            execution_id=state["execution_id"],
        )
        schema_json = _json_schema_of(ClassificationOutput)
        system_prompt = f"""You are a domain classifier for a thematic assistant.

Return a SINGLE JSON object that VALIDATES against the JSON Schema for type "{ClassificationOutput.__name__}".

JSON_SCHEMA[{ClassificationOutput.__name__}]:
{schema_json}

RULES:
- Output *JSON only* (no prose, no code fences).
- "confidence" in [0.0, 1.0].
- "reasoning" brief and concrete.
"""
        with with_accounting("chat.agentic.classifier", metadata={"message": state["user_message"]}):
            result = await self.model_service.call_model_with_structure(
                self.model_service.classifier_client,
                system_prompt,
                state["user_message"],
                ClassificationOutput,
                client_cfg=self.model_service.describe_client(self.model_service.classifier_client, role="classifier"),
            )

        usage = result.get("usage", {})
        add_step_log(state, "usage", {"step": "classifier", "usage": usage})

        if result["success"]:
            data = result["data"]
            state["is_our_domain"] = data["is_our_domain"]
            state["classification_reasoning"] = data["reasoning"]
            add_step_log(state, "classification", {
                "success": True,
                "is_our_domain": state["is_our_domain"],
                "confidence": data["confidence"],
            })
            self.logger.finish_operation(True, "classification_ok")
        else:
            if state["format_fix_attempts"] < 3:
                state["format_fix_attempts"] += 1
                fix_result = await self.format_fixer.fix_format(
                    result.get("raw", ""), "ClassificationOutput", state["user_message"], system_prompt
                )
                if fix_result["success"]:
                    validated = ClassificationOutput.model_validate(fix_result["data"])
                    state["is_our_domain"] = validated.is_our_domain
                    state["classification_reasoning"] = validated.reasoning
                    add_step_log(state, "classification_fixed", {"success": True, "attempt": state["format_fix_attempts"]})
                    self.logger.finish_operation(True, "classification_fixed")
                else:
                    state["error_message"] = fix_result["error"]
                    state["is_our_domain"] = True
                    add_step_log(state, "classification_failed", {"success": False, "fallback_used": True})
                    self.logger.finish_operation(False, "classification_failed_fallback")
            else:
                state["error_message"] = result.get("error", "classification error")
                state["is_our_domain"] = True
                add_step_log(state, "classification_failed", {"success": False, "max_attempts_reached": True})
                self.logger.finish_operation(False, "classification_failed")
        return state


class QueryWriterAgent:
    def __init__(self, config: Config, model_service: ThematicBotModelService):
        self.config = config
        self.logger = AgentLogger(f"{BUNDLE_ID}.QueryWriterAgent", config.log_level)
        self.model_service = model_service
        self.format_fixer = FormatFixerService(config)

    async def write_queries(self, state: ChatGraphState) -> ChatGraphState:
        self.logger.start_operation(
            "write_queries",
            user_message=(state["user_message"][:100] + "...") if len(state["user_message"]) > 100 else state["user_message"],
            execution_id=state["execution_id"],
        )
        schema_json = _json_schema_of(QueryWriterOutput)
        system_prompt = f"""You are a Query Writer for a RAG system.

Return a SINGLE JSON object that VALIDATES against the JSON Schema for type "{QueryWriterOutput.__name__}".

JSON_SCHEMA[{QueryWriterOutput.__name__}]:
{schema_json}

REQUIREMENTS:
- Populate "queries" with 3–6 diverse, non-overlapping items.
- Each item MUST include:
  - "query": string
  - "weight": number in [0.0, 1.0]
  - "reasoning": short, practical justification
- Sort by descending "weight".
- "chain_of_thought" optional (may be null).
"""
        with with_accounting("chat.agentic.query_writer", metadata={"message": state["user_message"]}):
            result = await self.model_service.call_model_with_structure(
                self.model_service.query_writer_client,
                system_prompt,
                state["user_message"],
                QueryWriterOutput,
                client_cfg=self.model_service.describe_client(self.model_service.query_writer_client, role="query_writer"),
            )
        if result["success"]:
            data = result["data"]
            state["rag_queries"] = data["queries"]
            add_step_log(state, "query_generation", {
                "success": True,
                "query_count": len(state["rag_queries"]),
                "total_weight": sum(q.get("weight", 0.0) for q in state["rag_queries"]),
            })
            self.logger.finish_operation(True, "queries_ok")
        else:
            if state["format_fix_attempts"] < 3:
                state["format_fix_attempts"] += 1
                fix = await self.format_fixer.fix_format(result.get("raw", ""), "QueryWriterOutput", state["user_message"], system_prompt)
                if fix["success"]:
                    validated = QueryWriterOutput.model_validate(fix["data"])
                    state["rag_queries"] = validated.queries
                    add_step_log(state, "query_generation_fixed", {"success": True, "attempt": state["format_fix_attempts"]})
                    self.logger.finish_operation(True, "queries_fixed")
                else:
                    state["rag_queries"] = [{
                        "query": state["user_message"], "weight": 1.0, "reasoning": "fallback - original message"
                    }]
                    add_step_log(state, "query_generation_failed", {"success": False, "fallback_used": True})
                    self.logger.finish_operation(False, "queries_failed_fallback")
            else:
                state["rag_queries"] = [{
                    "query": state["user_message"], "weight": 1.0, "reasoning": "fallback - original message"
                }]
                add_step_log(state, "query_generation_failed", {"success": False, "max_attempts_reached": True})
                self.logger.finish_operation(False, "queries_failed")
        return state


class RAGAgent:
    def __init__(self, config: Config, storage: AIBundleStorage):
        self.config = config
        self.logger = AgentLogger(f"{BUNDLE_ID}.RAGAgent", config.log_level)
        self.rag_service = RAGService(config, storage)

    async def retrieve(self, state: ChatGraphState) -> ChatGraphState:
        self.logger.start_operation(
            "retrieve_documents",
            execution_id=state["execution_id"],
            query_count=len(state["rag_queries"]) if state["rag_queries"] else 0,
        )
        if not state["rag_queries"]:
            state["error_message"] = "No queries available for retrieval"
            add_step_log(state, "retrieval_failed", {"success": False, "error": state["error_message"]})
            self.logger.finish_operation(False, state["error_message"])
            return state
        try:
            docs = await self.rag_service.retrieve_documents(state["rag_queries"])
            state["retrieved_docs"] = docs
            add_step_log(state, "retrieval", {"success": True, "document_count": len(docs)})
            self.logger.finish_operation(True, f"retrieved {len(docs)}")
        except Exception as e:
            state["error_message"] = f"Document retrieval failed: {e}"
            state["retrieved_docs"] = []
            add_step_log(state, "retrieval_failed", {"success": False, "error": state["error_message"]})
            self.logger.finish_operation(False, state["error_message"])
        return state


class RerankingAgent:
    def __init__(self, config: Config, model_service: ThematicBotModelService):
        self.config = config
        self.logger = AgentLogger(f"{BUNDLE_ID}.RerankingAgent", config.log_level)
        self.model_service = model_service
        self.format_fixer = FormatFixerService(config)

    async def rerank(self, state: ChatGraphState) -> ChatGraphState:
        self.logger.start_operation(
            "rerank_documents",
            execution_id=state["execution_id"],
            document_count=len(state["retrieved_docs"]) if state["retrieved_docs"] else 0,
        )
        if not state["retrieved_docs"]:
            state["reranked_docs"] = []
            add_step_log(state, "reranking_skipped", {"success": True, "reason": "no_documents"})
            self.logger.finish_operation(True, "no_docs_to_rerank")
            return state

        schema_json = _json_schema_of(RerankingOutput)
        system_prompt = f"""You are a document reranking expert.

Return a SINGLE JSON object that VALIDATES against the JSON Schema for type "{RerankingOutput.__name__}".

JSON_SCHEMA[{RerankingOutput.__name__}]:
{schema_json}

INSTRUCTIONS:
- Given the user question and the retrieved documents, assign each a "relevance_score" in [0.0, 1.0] and a "ranking_position" (1 = most relevant).
- Sort "reranked_docs" by descending "relevance_score".
- "reasoning" should summarize the key ranking factors (brief).
"""
        docs_text = json.dumps(state["retrieved_docs"], indent=2, ensure_ascii=False)
        user_msg = f"User question: {state['user_message']}\n\nDocuments to rerank:\n{docs_text}"

        with with_accounting("chat.agentic.reranking", metadata={"message": user_msg}):
            result = await self.model_service.call_model_with_structure(
                self.model_service.reranker_client,
                system_prompt,
                user_msg,
                RerankingOutput,
                client_cfg=self.model_service.describe_client(self.model_service.reranker_client, role="reranker"),
            )

        if result["success"]:
            data = result["data"]
            state["reranked_docs"] = data["reranked_docs"]
            add_step_log(state, "reranking", {
                "success": True,
                "reranked_count": len(state["reranked_docs"]),
                "avg_relevance_score": (
                    sum(d.get("relevance_score", 0.0) for d in state["reranked_docs"]) / len(state["reranked_docs"])
                    if state["reranked_docs"] else 0.0
                ),
            })
            self.logger.finish_operation(True, "rerank_ok")
        else:
            # graceful fallback: keep original order with descending pseudo-scores
            state["reranked_docs"] = []
            for i, doc in enumerate(state["retrieved_docs"]):
                dc = dict(doc)
                dc["relevance_score"] = max(0.0, 1.0 - (i * 0.1))
                dc["ranking_position"] = i + 1
                state["reranked_docs"].append(dc)
            add_step_log(state, "reranking_failed", {"success": False, "fallback_used": True})
            self.logger.finish_operation(False, "rerank_failed_fallback")
        return state


class AnswerGeneratorAgent:
    def __init__(self, config: Config, model_service: ThematicBotModelService,
                 emit: AIBEmitters, streaming: bool = True):
        self.config = config
        self.logger = AgentLogger(f"{BUNDLE_ID}.AnswerGeneratorAgent", config.log_level)
        self.model_service = model_service
        self.emit = emit
        self.streaming = streaming

    async def emit_file(self):
        """
        Emits chat events for batch files + citations + per-file.
        """
            # if files:
                # await self._emit({
                #     "type": "chat.files",
                #     "agent": "tooling",
                #     "step": "files",
                #     "status": "completed",
                #     "title": f"Files Ready ({len(files)})",
                #     "data": {"count": len(files), "items": files}
                # })
        f = {
            "slot": "pdf_file",
             "title": "Sample PDF",
            "filename": "sample.pdf",
            "key": "sample.pdf",
            "description": "A sample PDF file for testing.",
            "rn": "file_12345",
            "mime": "application/pdf",
            "tool_id": "static_resource",
        }
        await self._emit({
            "type": "artifact.saved",
            "agent": "tooling",
            "step": "file",
            "artifact_type": "file",
            "status": "completed",
            "title": f"File Ready — {pathlib.Path((f.get('key') or '')).name or '(file)'}",
            "data": f
        })
                #
                # if citations:
                #     await self._emit({
                #         "type": "chat.citable",
                #         "agent": "tooling",
                #         "step": "citations",
                #         "status": "completed",
                #         "title": f"Citations ({len(citations)})",
                #         "data": {"count": len(citations), "items": citations}
                #     })


    async def generate_answer(self, state: ChatGraphState) -> ChatGraphState:
        self.logger.start_operation(
            "generate_answer",
            execution_id=state["execution_id"],
            is_our_domain=state["is_our_domain"],
            document_count=len(state["reranked_docs"]) if state["reranked_docs"] else 0,
        )

        # Build context snippets for prompt
        context_docs = ""
        if state["reranked_docs"]:
            for i, doc in enumerate(state["reranked_docs"][:5]):
                context_docs += f"Document {i+1}:\n{doc.get('content','')}\n\n"

        summary_context = ""
        if state["context"].get("running_summary"):
            summary_context = f"Previous conversation summary: {state['context']['running_summary']}\n\n"

        SUGGESTION_RULES = """
FOLLOW-UP SUGGESTIONS (strict):
- First-person, user-side imperatives (executable as-is).
- Start with a strong verb; no questions; no "please".
- No meta-asks like "Provide more details".
- ≤ 120 chars each, end with a period.
- Mirror the user’s language and context.
- 0–3 items. If greeting/capabilities → [].
"""
        # PRIVATE internal prelude guidance (not streamed)
        INTERNAL_GUIDE = (
            "\n\nINTERNAL PRELUDE (private; must appear BEFORE '<HERE GOES THINKING PART>'):\n"
            "Write a compact turn log as JSON, then continue with the usual three sections.\n"
            "Format:\n"
            "<<< BEGIN TURN LOG >>>\n"
            "```json\n"
            "{\n"
            '  "objective": "short goal",\n'
            '  "done": [],\n'
            '  "not_done": [],\n'
            '  "assumptions": [],\n'
            '  "risks": [],\n'
            '  "notes": "",\n'
            '  "prefs": {\n'
            '    "assertions": [],\n'
            '    "exceptions": []\n'
            '  }\n'
            "}\n"
            "```\n"
            "After that, emit strictly in order:\n"
            "  <HERE GOES THINKING PART>\n"
            "  <HERE GOES ANSWER FOR USER>\n"
            "  <HERE GOES FOLLOWUP>\n"
        )

        system_prompt = (
                f"{summary_context}You are a helpful assistant. Use the provided context snippets if relevant; otherwise answer from general knowledge.\n\n"
                "STYLE:\n"
                "- Be direct, structured, and actionable. Use short sections or lists.\n"
                "- Only cite provided snippets if you actually used them.\n"
                "- The THINKING section must be high-level (key considerations), not step-by-step chain-of-thought.\n\n"
                "OUTPUT PROTOCOL (strict):\n"
                "1) Write exactly this marker on its own line:\n"
                "<HERE GOES THINKING PART>\n"
                "Then a brief high-level plan in Markdown (bullets/short lines).\n"
                "2) Then this marker on its own line:\n"
                "<HERE GOES ANSWER FOR USER>\n"
                "Then the final user-facing answer in Markdown.\n"
                "3) Then this marker on its own line:\n"
                "<HERE GOES FOLLOWUP>\n"
                '{ "followups": [ /* 0–3 concise, user-imperative actions; or [] */ ] }\n'
                "Return ONLY these three sections in this exact order.\n"
                f"{SUGGESTION_RULES}"
                + INTERNAL_GUIDE
        )

        user_content = (
            f"Context snippets (may be empty):\n{context_docs}\n"
            f"User question:\n{state.get('user_message','')}"
        )

        # Streaming parser
        import re, json as _json

        THINK_RE = re.compile(r"<\s*here\s+goes\s+thinking\s+part\s*>", re.I)
        ANS_RE   = re.compile(r"<\s*here\s+goes\s+answer\s+for\s+user\s*>", re.I)
        FUP_RE   = re.compile(r"<\s*here\s+goes\s+followup\s*>", re.I)
        LOG_RE   = re.compile(r"<<<?\s*begin\s+turn\s+log\s*>>>?", re.I)

        MAX_BASE = max(len("here goes thinking part"),
                       len("here goes answer for user"),
                       len("here goes followup"),
                       len("<<< BEGIN TURN LOG >>>"))
        HOLDBACK = MAX_BASE + 8

        buf = ""
        tail = ""
        mode = "pre"     # pre (private) -> thinking -> answer -> followup

        delta_idx = 0
        thinking_chunks: list[str] = []
        answer_chunks: list[str] = []

        emit_from = 0
        deltas = 0
        thinking_text = ""
        answer_text = ""
        internal_buf: list[str] = []

        def _skip_ws(i: int) -> int:
            while i < len(buf) and buf[i] in (" ", "\t", "\r", "\n"):
                i += 1
            return i

        async def _emit(kind: str, text: str, completed: bool = False):
            nonlocal delta_idx
            if not text and not completed:
                return
            if kind == "thinking":
                if text:
                    thinking_chunks.append(text)
                await self.emit.delta(DeltaPayload(
                    text=text or "",
                    index=delta_idx,
                    marker="thinking",
                    completed=completed
                ))
            else:
                if text:
                    answer_chunks.append(text)
                await self.emit.delta(DeltaPayload(
                    text=text or "",
                    index=delta_idx,
                    marker="answer",
                    completed=completed
                ))
            delta_idx += 1

        def _find(pat: re.Pattern, start_hint: int):
            start = max(0, start_hint - HOLDBACK)
            return pat.search(buf, start)

        async def on_delta(piece: str):
            nonlocal buf, mode, emit_from, tail
            if not piece:
                return
            prev_len = len(buf)
            if mode != "followup":
                buf += piece

            while True:

                if mode == "pre":
                    # capture PRIVATE internal prelude until THINKING marker
                    m = _find(THINK_RE, prev_len)
                    if m:
                        if m.start() > emit_from:
                            internal_buf.append(buf[emit_from:m.start()])
                        emit_from = _skip_ws(m.end())
                        mode = "thinking"
                        continue
                    # accumulate safe internal slice; do NOT stream
                    safe_end = max(emit_from, len(buf) - HOLDBACK)
                    if safe_end > emit_from:
                        internal_buf.append(buf[emit_from:safe_end])
                        emit_from = safe_end
                    break

                if mode == "thinking":
                    m = _find(ANS_RE, prev_len)
                    if m and m.start() >= emit_from:
                        chunk = buf[emit_from:m.start()].rstrip()
                        await _emit("thinking", chunk)
                        emit_from = _skip_ws(m.end())
                        mode = "answer"
                        continue
                    safe_end = max(emit_from, len(buf) - HOLDBACK)
                    if safe_end > emit_from:
                        await _emit("thinking", buf[emit_from:safe_end])
                        emit_from = safe_end
                    break

                if mode == "answer":
                    m = _find(FUP_RE, prev_len)
                    if m and m.start() >= emit_from:
                        chunk = buf[emit_from:m.start()].rstrip()
                        await _emit("answer", chunk)
                        fup_start = _skip_ws(m.end())
                        tail = buf[fup_start:]
                        mode = "followup"
                        break
                    safe_end = max(emit_from, len(buf) - HOLDBACK)
                    if safe_end > emit_from:
                        await _emit("answer", buf[emit_from:safe_end])
                        emit_from = safe_end
                    break

                if mode == "followup":
                    tail += piece
                    break

                break

        try:
            usage_out: Dict[str, Any] = {}
            with with_accounting("chat.agentic.answer_generator", metadata={"message": state["user_message"]}):
                if self.streaming:
                    await self.model_service.stream_model_text_tracked(
                        self.model_service.answer_generator_client,
                        [
                            SystemMessage(content=system_prompt, id=_mid("sys")),
                            HumanMessage(content=user_content, id=_mid("user")),
                        ],
                        on_delta=on_delta,
                        temperature=0.3,
                        max_tokens=2000,
                        client_cfg=self.model_service.describe_client(
                            self.model_service.answer_generator_client, role="answer_generator"
                        ),
                    )
                else:
                    res = await self.model_service.call_model_text(
                        self.model_service.answer_generator_client,
                        [
                            SystemMessage(content=system_prompt, id=_mid("sys")),
                            HumanMessage(content=user_content, id=_mid("user")),
                        ],
                        temperature=0.3,
                        max_tokens=2000,
                        client_cfg=self.model_service.describe_client(
                            self.model_service.answer_generator_client, role="answer_generator"
                        ),
                    )
                    await on_delta(res["text"])

            # Final flush
            if mode == "pre" and emit_from < len(buf):
                internal_buf.append(buf[emit_from:])
            elif mode == "thinking" and emit_from < len(buf):
                await _emit("thinking", buf[emit_from:].rstrip())
            elif mode == "answer" and emit_from < len(buf):
                await _emit("answer", buf[emit_from:].rstrip())

            # signal completion of THINKING stream (complex-bundle parity)
            try:
                # after final flush of thinking:
                # await self.emit.delta(DeltaPayload(text="", index=deltas, marker="thinking", completed=True))
                await self.emit.delta(DeltaPayload(text="", index=delta_idx, marker="thinking", completed=True))
                delta_idx += 1
                await self.emit.delta(DeltaPayload(text="", index=delta_idx, marker="answer", completed=True))
                delta_idx += 1
            except Exception:
                pass

            # Parse FOLLOWUPS JSON
            followups: List[str] = []
            if tail:
                raw = tail.strip().strip("`").lstrip(">")
                import re, json as _json
                m = re.search(r"\{.*\}\s*$", raw, re.S)
                if m:
                    try:
                        obj = _json.loads(m.group(0))
                        vals = obj.get("followups") or obj.get("followup") or []
                        if isinstance(vals, list):
                            followups = [str(v).strip() for v in vals if str(v).strip()]
                    except Exception:
                        pass

            # Parse TURN LOG JSON from the PRIVATE internal prelude
            internal_text = "".join(internal_buf).strip()

            def _extract_turn_log_json(text: str) -> Optional[dict]:
                if not text:
                    return None
                import re, json as _json
                LOG_RE   = re.compile(r"<<<?\s*begin\s+turn\s+log\s*>>>?", re.I)
                mm = LOG_RE.search(text)
                if not mm:
                    return None
                tail_ = text[mm.end():]
                fence = re.search(r"```json\s*(.*?)```", tail_, re.S | re.I)
                if not fence:
                    return None
                try:
                    return _json.loads(fence.group(1).strip())
                except Exception:
                    try:
                        payload = fence.group(1).strip()
                        payload = re.sub(r",\s*}", "}", payload)
                        payload = re.sub(r",\s*]", "]", payload)
                        return _json.loads(payload)
                    except Exception:
                        return None

            turn_log = _extract_turn_log_json(internal_text) or {}

            # Persist to state
            thinking_text = "".join(thinking_chunks).strip()
            answer_text   = "".join(answer_chunks).strip()

            state["thinking"] = thinking_text or None
            state["final_answer"] = answer_text or "..."

            state["followups"] = followups
            state["internal_prelude"] = internal_text or None
            state["turn_log"] = turn_log or None

            # Emit followups step with composed markdown
            if followups:
                await self.emit.step(StepPayload(
                    step="followups",
                    status="completed",
                    title="🧠 Follow-ups",
                    markdown="### Suggested next actions\n\n" + "\n".join(f"- {s}" for s in followups),
                    agent="answer_generator",
                    data={"items": followups}
                ))

            add_step_log(state, "answer_generation_usage", {"usage": usage_out, "followups": followups})
            add_step_log(state, "followups_parsed", {"count": len(followups), "tail_len": len(tail)})

            # Keep the final assistant message (the ANSWER, not the thinking)
            ai_msg = AIMessage(content=state["final_answer"], id=_mid("ai"))
            state["messages"].append(ai_msg)

            # Stream completion event
            try:
                await self.emit.step(StepPayload(
                    step="assistant_stream",
                    status="completed",
                    title="📡 Stream",
                    markdown="Streaming complete.",
                    data={"meta": {"deltas": delta_idx, "turn_id": state.get("turn_id")}},
                    agent="answer_generator",
                ))
            except Exception:
                pass

            self.logger.finish_operation(True, f"Generated answer, {len(state['final_answer'])} chars; followups={len(followups)}")
        except Exception as e:
            state["error_message"] = f"Answer generation failed: {e}"
            state["final_answer"] = "I encountered an error generating the response."
            add_step_log(state, "answer_generation_failed", {"success": False, "error": state["error_message"]})
            self.logger.finish_operation(False, state["error_message"])
        return state

# =========================
# Initial state + Workflow
# =========================

@agentic_initial_state(name=f"{BUNDLE_ID}-initial-state", priority=200)
def create_initial_state(payload: Dict[str, Any]):
    return {
        "messages": [],
        "summarized_messages": [],
        "context": {"bundle": BUNDLE_ID},
        "user_message": payload.get("user_message") or "",
        "is_our_domain": None,
        "classification_reasoning": None,
        "rag_queries": None,
        "retrieved_docs": None,
        "reranked_docs": None,
        "final_answer": None,
        "thinking": None,
        "followups": [],
        "internal_prelude": None,
        "turn_log": None,
        "error_message": None,
        "format_fix_attempts": 0,
        "search_hits": None,
        "execution_id": f"exec_{int(time.time() * 1000)}",
        "start_time": time.time(),
        "step_logs": [],
        "performance_metrics": {},
        "turn_id": payload.get("turn_id") or _mid("turn"),
    }


@agentic_workflow(name=f"{BUNDLE_ID}", version="1.0.0", priority=150)
class ChatWorkflow:
    """Main workflow orchestrator using ChatCommunicator for emissions."""

    def __init__(self,
                 config: Config,
                 communicator: ChatCommunicator,
                 pg_pool: Optional[Any] = None,
                 streaming: bool = True):

        self.storage = AIBundleStorage(
            tenant=config.tenant,
            project=config.project,
            ai_bundle_id=BUNDLE_ID,
            storage_uri=config.bundle_storage_url,
        )

        self.config = config
        # role mapping for this bundle
        config.role_models = self.configuration["role_models"]

        self.model_service = ThematicBotModelService(config)
        self.logger = AgentLogger(f"{BUNDLE_ID}.ChatWorkflow", config.log_level)

        # unified communicator
        self.comm = communicator

        self.config = config

        # db connection pool (asyncpg)
        self.pg_pool = pg_pool

        # event emitters
        self.emit = AIBEmitters(self.comm)

        # current turn id
        self._turn_id: Optional[str] = None

        # emit helpers
        async def _emit_step(step_name: str, status: str, payload: dict | None = None, title: Optional[str] = None):
            await self.comm.step(step=step_name, status=status, title=title, data=payload or {})

        async def _emit_delta(text: str, idx: int, meta: dict | None = None, completed: bool = False):
            marker = (meta or {}).get("marker", "answer")
            await self.comm.delta(text=text, index=idx, marker=marker, completed=completed)

        self.emit_step = _emit_step
        self.emit_delta = _emit_delta

        # agents
        self.classifier = ClassifierAgent(config, self.model_service)
        self.query_writer = QueryWriterAgent(config, self.model_service)
        self.rag_agent = RAGAgent(config, self.storage)
        self.reranking_agent = RerankingAgent(config, self.model_service)
        self.answer_generator = AnswerGeneratorAgent(
            config,
            model_service=self.model_service,
            emit=self.emit,
            streaming=streaming,
        )

        # memory + graph
        self.memory = MemorySaver()
        self.graph = self._build_graph()

        self.logger.log_step("workflow_initialized", {
            "role_mapping": config.role_models,
            "embedding_type": "custom" if config.custom_embedding_endpoint else "openai",
            "kb_search_available": bool(getattr(config, "kb_search_url", None)),
        })

    @property
    def configuration(self):
        # You can swap these to your preferred providers/models
        return {
            "role_models": {
                "classifier":       {"provider": "openai",    "model": "gpt-4o-mini"},
                "answer_generator": {"provider": "openai",    "model": "o3-mini"},
                "query_writer":     {"provider": "openai",    "model": "gpt-4o-mini"},
                "reranker":         {"provider": "openai",    "model": "gpt-4o-mini"},
                "format_fixer":     {"provider": "anthropic", "model": "claude-3-haiku-20240307"},
            }
        }

    def set_state(self, state: Dict[str, Any]):
        self._app_state = dict(state or {})
        self._turn_id = self._app_state.get("turn_id")

    async def run(self, **params) -> Dict[str, Any]:
        # keep the turn id coming from the handler/processor
        self._turn_id = self._turn_id or _mid("turn")

        text = (params.get("text") or self._app_state.get("text") or "").strip()
        thread_id = self._app_state.get("conversation_id") or "default"

        initial_state = create_initial_state({"user_message": text, "turn_id": self._turn_id})

        seed = _history_to_seed_messages(self._app_state.get("history"))
        if seed:
            initial_state["messages"].extend(seed)

        result = await self.graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": thread_id}},
        )
        return project_app_state(result)

    # ----- step markdown composers -----

    def _render_start_md(self, step: str, state: ChatGraphState) -> str:
        if step == "classifier":
            return "Classifying domain…"
        if step == "query_writer":
            return "Generating RAG queries…"
        if step == "rag_retrieval":
            return "Retrieving documents…"
        if step == "reranking":
            return "Reranking documents…"
        if step == "answer_generator":
            return "Composing final answer…"
        if step == "summarize":
            return "Summarizing recent turns…"
        if step == "workflow_start":
            return f"Incoming message:\n\n> {_safe_md(_truncate(state.get('user_message',''), 320))}"
        return "Working…"

    def _render_end_md(self, step: str, state: ChatGraphState) -> str:
        if step == "classifier":
            conf = state.get("classification_reasoning") or ""
            in_scope = state.get("is_our_domain")
            badge = "✅ In scope" if in_scope else "❌ Out of scope"
            # Try to grab confidence from step logs if present
            conf_val = 0.0
            for e in reversed(state.get("step_logs", [])):
                if e.get("step") in ("classification", "classification_fixed"):
                    d = e.get("data", {})
                    conf_val = float(d.get("confidence") or d.get("data", {}).get("confidence") or 0.0)
                    break
            return textwrap.dedent(f"""
            **Result:** {badge}

            **Confidence:** `{_pct(conf_val)}` {_bar(conf_val)}

            **Why:** {_safe_md(conf)}
            """).strip()

        if step == "query_writer":
            qs = state.get("rag_queries") or []
            rows = [[f"`{q.get('weight',0):.2f}`", _safe_md(_truncate(q.get("query",""), 80)), _safe_md(_truncate(q.get("reasoning",""), 60))] for q in qs]
            table = _md_table(["Weight","Query","Reason"], rows) if rows else "_No queries produced._"
            return f"**Queries ({len(qs)}):**\n\n{table}"

        if step == "rag_retrieval":
            docs = state.get("retrieved_docs") or []
            if not docs:
                return "_No documents retrieved._"
            bullets = []
            for i, d in enumerate(docs[:5], start=1):
                snippet = _safe_md(_truncate(d.get("content",""), 220))
                meta = d.get("metadata") or {}
                src = meta.get("source") or meta.get("url") or meta.get("title") or f"Doc {i}"
                bullets.append(f"- **{_safe_md(str(src))}** — `{_truncate(snippet, 160)}`")
            return f"**Retrieved:** {len(docs)} documents\n\n" + "\n".join(bullets)

        if step == "reranking":
            rer = state.get("reranked_docs") or []
            if not rer:
                return "_No reranking available; using retrieval order._"
            rows = []
            for d in rer[:6]:
                rank = str(d.get("ranking_position") or "?")
                score = float(d.get("relevance_score") or 0.0)
                snippet = _safe_md(_truncate(d.get("content",""), 70))
                rows.append([rank, f"`{score:.2f}` {_bar(score)}", snippet])
            table = _md_table(["Rank","Score","Snippet"], rows)
            return f"**Top {min(6,len(rer))} after rerank:**\n\n{table}"

        if step == "answer_generator":
            think = _safe_md(_truncate(state.get("thinking") or "", 240)) if state.get("thinking") else ""
            fups = state.get("followups") or []
            md = ""
            if think:
                md += f"**Thinking (high-level):**\n\n> {think}\n\n"
            if fups:
                md += "**Follow-ups:**\n\n" + "\n".join(f"- { _safe_md(i) }" for i in fups)
            if not md:
                md = "_Answer composed._"
            return md

        if step == "summarize":
            sm = state.get("context", {}).get("running_summary") or ""
            return _safe_md(sm) or "_No summary available._"

        if step == "workflow_start":
            return "Kickoff complete."
        return "Step complete."

    def _step_payload(self, step: str, status: str, state: ChatGraphState) -> Dict[str, Any]:
        if status == "started":
            md = self._render_start_md(step, state)
        else:
            md = self._render_end_md(step, state)
        # provide both `markdown` and `compose` for UI compatibility
        return _mk_compose(md, {"meta": {"turn_id": self._turn_id, "status": status, "step": step}})

    def _step_title(self, step: str) -> str:
        return STEP_TITLES.get(step, step.title())

    def _wrap_node(self, fn, step_name: str):
        async def _wrapped(state: ChatGraphState) -> ChatGraphState:
            await self.emit_step(step_name, "started", self._step_payload(step_name, "started", state), title=self._step_title(step_name))
            try:
                out = fn(state)
                if asyncio.iscoroutine(out):
                    out = await out
                await self.emit_step(step_name, "completed", self._step_payload(step_name, "completed", out), title=self._step_title(step_name))
                return out
            except Exception as e:
                err_md = f"**Error:** `{_safe_md(str(e))}`"
                await self.emit_step(step_name, "error", _mk_compose(err_md, {"meta": {"turn_id": self._turn_id}}), title=self._step_title(step_name))
                raise
        return _wrapped

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(ChatGraphState)

        def add_user_message(state: ChatGraphState) -> Dict[str, Any]:
            msg = HumanMessage(content=state["user_message"], id=_mid("user"))
            return {"messages": [msg]}

        if SummarizationNode:
            def simple_summarize(state: ChatGraphState) -> ChatGraphState:
                state["summarized_messages"] = state["messages"][-10:]
                return state
            workflow.add_node("summarize", simple_summarize)
        else:
            def simple_summarize(state: ChatGraphState) -> ChatGraphState:
                state["summarized_messages"] = state["messages"][-10:]
                return state
            workflow.add_node("summarize", simple_summarize)

        workflow.add_node("classifier", self._wrap_node(self.classifier.classify, "classifier"))
        workflow.add_node("query_writer", self._wrap_node(self.query_writer.write_queries, "query_writer"))
        workflow.add_node("rag_retrieval", self._wrap_node(self.rag_agent.retrieve, "rag_retrieval"))
        workflow.add_node("reranking", self._wrap_node(self.reranking_agent.rerank, "reranking"))
        workflow.add_node("answer_generator", self._wrap_node(self.answer_generator.generate_answer, "answer_generator"))

        workflow.add_node("workflow_start", self._wrap_node(add_user_message, "workflow_start"))
        workflow.add_edge(START, "workflow_start")
        workflow.add_edge("workflow_start", "summarize")
        workflow.add_edge("summarize", "classifier")
        workflow.add_edge("classifier", "query_writer")
        workflow.add_edge("query_writer", "rag_retrieval")
        workflow.add_edge("rag_retrieval", "reranking")
        workflow.add_edge("reranking", "answer_generator")

        async def _emit_workflow_complete(state: ChatGraphState) -> ChatGraphState:
            md = "All steps completed successfully."
            await self.emit_step(
                "workflow_complete",
                "completed",
                _mk_compose(md, {"meta": {"turn_id": self._turn_id, "followups": state.get("followups") or []}}),
                title=self._step_title("workflow_complete"),
            )
            return state

        workflow.add_node("workflow_complete", _emit_workflow_complete)
        workflow.add_edge("answer_generator", "workflow_complete")
        workflow.add_edge("workflow_complete", END)


        return workflow.compile(checkpointer=self.memory)

    # Convenience
    async def get_conversation_history(self, thread_id: str = "default") -> List[AnyMessage]:
        try:
            state = await self.graph.aget_state(config={"configurable": {"thread_id": thread_id}})
            return state.values.get("messages", []) if state.values else []
        except Exception as e:
            self.logger.log_error(e, f"Failed to get conversation history for thread {thread_id}")
            return []

    async def get_conversation_summary(self, thread_id: str = "default") -> str:
        try:
            state = await self.graph.aget_state(config={"configurable": {"thread_id": thread_id}})
            if state.values and state.values.get("context"):
                running_summary = state.values["context"].get("running_summary")
                return str(running_summary) if running_summary else ""
            return ""
        except Exception as e:
            self.logger.log_error(e, f"Failed to get conversation summary for thread {thread_id}")
            return ""

    async def get_execution_logs(self, thread_id: str = "default") -> List[Dict[str, Any]]:
        try:
            state = await self.graph.aget_state(config={"configurable": {"thread_id": thread_id}})
            return state.values.get("step_logs", []) if state.values else []
        except Exception as e:
            self.logger.log_error(e, f"Failed to get execution logs for thread {thread_id}")
            return []

    def _get_workflow_node_names(self) -> List[str]:
        nodes = ["workflow_start", "summarize", "query_writer", "rag_retrieval", "reranking", "answer_generator"]
        nodes.insert(2, "classifier")
        return nodes

    def suggestions(self, **kwargs):
        return [
            "What light, watering, and soil do my common houseplants need?",
            "Why are my leaves yellow/brown/curling, and how do I fix it?",
            "How can I prevent and treat pests like spider mites and fungus gnats?",
            "When should I repot, and what potting mix should I use?",
        ]
