# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/llm_tools.py


import json
from typing import Annotated, Optional, List, Dict, Any

import semantic_kernel as sk
try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

# Bound at runtime by ToolManager (__init__ calls mod.bind_service(self.svc))
_SERVICE = None

def bind_service(svc):  # ToolManager will call this
    global _SERVICE
    _SERVICE = svc


class LLMTools:
    """
    LLM-backed summarizer with TWO explicit modes:

    1) input_mode="text"    → Summarize the `text` argument. Ignores sources_list and cite_sources.
    2) input_mode="sources" → Summarize a list of sources passed via `sources_list`
                              and (optionally) insert inline citation tokens [[S:<sid>]].

    Source schema (each item):
      {
        "sid": int,                 # local source id (1..N)
        "title": str,               # short title (used for grounding)
        "url": str,                 # optional, not emitted; used by downstream to link
        "text": str                 # main text/body to summarize (prefer this field)
      }

    Return:
      Markdown string. If input_mode="sources" and cite_sources=true, the string may contain tokens
      like [[S:1]] or [[S:1,3]] at the end of sentences/bullets. These are easy to post-process via
      regex:  r'\\[\\[S:(\\d+(?:,\\d+)*)\\]\\]'.
    """

    @kernel_function(
        name="summarize_llm",
        description=(
                "Summarize either free text (input_mode='text') or a list of sources (input_mode='sources'). "
                "In sources mode, may add inline citation tokens [[S:<sid>]] to mark provenance."
        )
    )
    async def summarize_llm(
            self,
            input_mode: Annotated[str, "text|sources", {"enum": ["text", "sources"]}] = "text",
            text: Annotated[str, "When input_mode='text': the text to summarize (≤10k chars)."] = "",
            sources_list: Annotated[List[Dict[str, Any]], "When input_mode='sources': list of {sid,int; title,str; url,str; text,str}."] = None,
            style: Annotated[str, "brief|bullets|one_line", {"enum": ["brief","bullets","one_line"]}] = "brief",
            cite_sources: Annotated[bool, "In sources mode: insert [[S:<sid>]] tokens after claims."] = False,
            max_tokens: Annotated[int, "LLM output cap.", {"min": 64, "max": 800}] = 300,
    ) -> Annotated[str, "Markdown summary (string, may include [[S:<sid>]] tokens)."]:
        if _SERVICE is None:
            return "ERROR: summarizer not bound to service."

        from langchain_core.messages import SystemMessage, HumanMessage

        # --- normalize inputs ---
        if style not in ("brief", "bullets", "one_line"):
            style = "brief"
        mode = "sources" if input_mode == "sources" else "text"

        # ----- Build prompt -----
        # Base rules apply to both modes
        sys_lines = [
            "You are a professional summarizer. Always output properly formatted Markdown text.",
            "Summarize for a busy reader. Be factual and non-speculative.",
            "OUTPUT FORMAT REQUIREMENTS:",
            "- ALWAYS format your response as clean Markdown",
            "- Use proper Markdown syntax for headings, lists, emphasis, etc.",
            "- style=brief   → one well-formatted paragraph (3–5 concise sentences)",
            "- style=bullets → 3–6 compact bullet points using proper Markdown list syntax (-)",
            "- style=one_line→ single sentence ≤ 28 words",
            "- Never include explanatory text, prefaces, or meta-commentary",
            "- Output ONLY the Markdown-formatted summary content",
        ]

        # Construct user payload depending on mode
        if mode == "text":
            # Summarize the provided text; ignore citation features entirely
            src_block = ""
            content = (text or "")[:10000]
            user = f"mode=text; style={style}\n\nSummarize the following text in Markdown format:\n\n{content}"

        else:
            # Summarize provided sources; optionally emit [[S:<sid>]] tokens
            # Parse sources_list and build a compact, bounded digest
            raw_sources = sources_list or []

            # Normalize source rows
            rows: List[Dict[str, Any]] = []
            for s in raw_sources or []:
                if not isinstance(s, dict):  # skip garbage
                    continue
                sid = s.get("sid")
                title = s.get("title") or ""
                url = s.get("url") or s.get("href") or ""
                body = s.get("text") or s.get("body") or s.get("content") or ""
                if sid is None:
                    continue
                rows.append({"sid": sid, "title": title, "url": url, "text": body})

            # Bound total budget to 10k chars; distribute fairly across sources
            total_budget = 10000
            per = max(600, total_budget // max(1, len(rows)))  # ≥ 600 chars each if few sources
            parts = []
            for r in rows:
                t = (r["text"] or "")[:per]
                # Add a sid tag in the digest so the model can anchor claims
                parts.append(f"[sid:{r['sid']}] {r['title']}\n{t}".strip())
            digest = "\n\n---\n\n".join(parts)[:total_budget]

            # Add explicit citation rules only in sources mode
            if cite_sources:
                sys_lines += [
                    "",
                    "CITATION REQUIREMENTS:",
                    "- Insert inline citation tokens at the end of sentences/bullets: [[S:<sid>]]",
                    "- Multiple sources allowed: [[S:1,3]]. Use only provided sid values; never invent.",
                    "- Citations are part of the Markdown output - include them naturally in the text",
                    "- If a claim is general, you may omit a token.",
                ]

            # Provide a compact sid→title map to reduce hallucination
            compact_map = "\n".join([f"- {r['sid']}: {r['title'][:80]}" for r in rows]) if rows else ""
            src_block = f"SOURCE IDS:\n{compact_map}\n" if compact_map else ""
            user = f"mode=sources; style={style}; cite_sources={bool(cite_sources)}\n{src_block}\nSummarize these sources in Markdown format:\n\n{digest}"

        sys_prompt = "\n".join(sys_lines)

        # ----- stream infer -----
        buf: List[str] = []

        async def on_delta(piece: str):
            if piece:
                buf.append(piece)

        async def on_complete(_):  # noqa: ARG001
            pass

        await _SERVICE.stream_model_text_tracked(
            _SERVICE.get_client("tool.summarizer"),
            [SystemMessage(content=sys_prompt), HumanMessage(content=user)],
            on_delta=on_delta,
            on_complete=on_complete,
            temperature=0.2,
            max_tokens=max_tokens,
            client_cfg=_SERVICE.describe_client(_SERVICE.answer_generator_client, role="answer_generator"),
            role="answer_generator",
        )
        return "".join(buf).strip()

    @kernel_function(
        name="edit_text_llm",
        description=(
                "Edit or transform text per an instruction while preserving facts/structure. If sources are provided, any NEW or CHANGED facts must be grounded and cited with [[S:n]]."
        )
    )
    async def edit_text_llm(
            self,
            text: Annotated[str, "Original content to edit (≤15k chars)."],
            instruction: Annotated[str, "Editing goal, e.g., 'add security section and shorten intro'"],
            tone: Annotated[str, "Optional tone/style, e.g., 'professional'"] = "",
            keep_formatting: Annotated[bool, "Keep Markdown structure (headings, lists, code blocks)."] = True,
            sources_list: Annotated[List[Dict[str, Any]], "List of sources: {sid:int, title:str, url:str, text:str}"] = None,
            cite_sources: Annotated[bool, "If true, add tokens [[S:<sid>]] after NEW/CHANGED claims only."] = True,
            forbid_new_facts_without_sources: Annotated[bool, "If true, any NEW claim MUST be grounded in provided sources."] = True,
            max_tokens: Annotated[int, "LLM output cap.", {"min": 64, "max": 1600}] = 900,
    ) -> Annotated[str, "Edited Markdown; may include [[S:<sid>]] tokens if cite_sources=true"]:
        if _SERVICE is None:
            return "ERROR: editor not bound to service."

        from langchain_core.messages import SystemMessage, HumanMessage
        import json as _json

        # ---- Parse/normalize sources (bounded) ----
        raw = sources_list or []
        rows = []
        total_budget = 10000
        per = max(600, total_budget // max(1, len(raw))) if raw else 0
        for s in (raw or []):
            if not isinstance(s, dict) or "sid" not in s:
                continue
            rows.append({
                "sid": int(s["sid"]),
                "title": str(s.get("title",""))[:160],
                "url": str(s.get("url",""))[:800],
                "text": str(s.get("text") or s.get("body") or "")[:per]
            })
        sid_map = "\n".join([f"- {r['sid']}: {r['title']}" for r in rows]) if rows else ""

        # ---- System rules ----
        rules = [
            "You are a professional content editor. Always output properly formatted Markdown text.",
            "",
            "OUTPUT REQUIREMENTS:",
            "- Return ONLY the edited text in clean, well-formatted Markdown",
            "- Use proper Markdown syntax for all formatting (headings, lists, emphasis, code, etc.)",
            "- Never include explanations, commentary, or meta-text",
            "- The entire response should be valid Markdown content",
            "",
            "EDITING RULES:",
            "- Edit deterministically. Preserve meaning unless the instruction requires changes.",
            "- Do NOT invent facts, numbers, or events.",
            "- If tone is specified, apply consistently.",
            "- If keep_formatting=true: preserve Markdown structure and section order; you may rewrite sentences inside blocks.",
        ]
        rules += [
            "- Do not alter or renumber existing [[S:n]] tokens. Remove a token only if you delete the entire supported claim/sentence."
        ]
        rules += [
            "- If the input contains <!--GUIDANCE_START--> ... <!--GUIDANCE_END-->, treat it as internal instructions: apply them, then REMOVE the entire block from the output."
        ]
        if forbid_new_facts_without_sources:
            rules.append("- Any NEW or materially CHANGED factual claim must be grounded in provided sources.")
        if cite_sources and rows:
            rules += [
                "",
                "CITATION REQUIREMENTS:",
                "- Insert [[S:<sid>]] after sentences/bullets that contain NEW or materially CHANGED factual claims,",
                "- Use provided sid values only; never invent sids.",
                "- Citations are part of the Markdown output - include them naturally in the text",
                "- If a sentence is rearranged but unchanged factually, do not cite it.",
            ]

        sys_prompt = "\n".join(rules)

        # ---- User payload ----
        header = f"INSTRUCTION: {instruction}\nTONE: {tone or 'as-is'}\nkeep_formatting={bool(keep_formatting)}; cite_sources={bool(cite_sources)}; forbid_new_facts_without_sources={bool(forbid_new_facts_without_sources)}"
        if rows:
            header += f"\nSOURCE IDS:\n{sid_map}\n"

        body = (text or "")[:15000]
        if rows:
            digest = "\n\n---\n\n".join([f"[sid:{r['sid']}] {r['title']}\n{r['text']}" for r in rows])[:total_budget]
            ask = f"{header}\n---\nEDIT THE FOLLOWING MARKDOWN TEXT:\n{body}\n\n---\nSOURCES DIGEST:\n{digest}"
        else:
            ask = f"{header}\n---\nEDIT THE FOLLOWING MARKDOWN TEXT:\n{body}"

        buf = []
        async def on_delta(d):
            if d: buf.append(d)
        async def on_complete(_):
            pass

        await _SERVICE.stream_model_text_tracked(
            _SERVICE.get_client("tool.editor"),
            [SystemMessage(content=sys_prompt), HumanMessage(content=ask)],
            on_delta=on_delta, on_complete=on_complete,
            temperature=0.15, max_tokens=max_tokens,
            client_cfg=_SERVICE.describe_client(_SERVICE.answer_generator_client, role="answer_generator"),
            role="answer_generator",
        )
        return "".join(buf).strip()


kernel = sk.Kernel()
tools = LLMTools()
kernel.add_plugin(tools, "agent_llm_tools")

print()
