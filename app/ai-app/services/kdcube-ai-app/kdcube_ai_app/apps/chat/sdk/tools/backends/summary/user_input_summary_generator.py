# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# sdk/tools/backends/summary/user_input_summary_generator.py

from __future__ import annotations

def user_input_summary_instruction() -> str:
    """
    Instruction block for producing a compact, contextual, embedding-friendly
    summary of the current user input (and attachments).
    """
    return (
        "USER_INPUT_SUMMARY (inventoristic; telegraphic; embedding-friendly; contextual):\n"
        "Summarize USER_PROMPT and any ATTACHMENT_SUMMARIES. Interpret meaning IN CONTEXT using\n"
        "selected turns/memories; resolve deixis or references (e.g., 'that table') when possible.\n"
        "Resolve references like \"that\", \"I said\", \"in my example\", \"my data\" against cross-turn context\n"
        "(recent turns + semantic matches). Treat these as targets that may live in prior turns, even if\n"
        "the user did not name them precisely. \"I said\" always refers to user messages/attachments.\n"
        "Make it clear what is in the user prompt vs what is in attachments.\n"
        "\n"
        "Output a TELEGRAPHIC, SECTIONED TEXT (NO JSON).\n"
        "Each section MUST start with the source path as a header:\n"
        "- user.prompt\n"
        "- user.attachments.<artifact_name> (one section per attachment)\n"
        "\n"
        "For EACH section include telegraphic fields:\n"
        "  semantic:<...>; structural:<...>; inventory:<...>; anomalies:<...>; safety:<...>.\n"
        "\n"
        "Field meaning:\n"
        "- semantic: intent type, topics/domains, scope, constraints, deliverables, key facts.\n"
        "- structural: format/layout (plain text, bullets, tables/CSV/JSON/YAML/XML, code blocks, URLs).\n"
        "- inventory: short list of notable content fragments (schemas, samples, tables, code, ids).\n"
        "- anomalies: contradictions, missing fields, ambiguities, garbled content, security flags.\n"
        "- safety: benign or suspicious, with short reason if suspicious.\n"
        "\n"
        "Rules:\n"
        "- Keep it compact; telegraphic phrases, no prose.\n"
        "- Mention attachment metadata (mime/filename/size) inside that attachment's section.\n"
        "- If context resolves an ambiguous reference, note the resolved target in semantic/inventory.\n"
    )
