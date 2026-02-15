# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
from typing import Literal

# chat/sdk/tools/tools_insights.py
# web_tools
# rendering_tools
BUILTIN_TOOLS = {
    "llm_tools.generate_content_llm",
    "web_tools.web_search",
    "web_tools.web_fetch",
    "rendering_tools.write_pdf",
    "rendering_tools.write_pptx",
    "rendering_tools.write_docx",
    "rendering_tools.write_html",
    "rendering_tools.write_png",
    "rendering_tools.write_xlsx",
    "exec_tools.execute_code_python",
}

# Infrastructure tools (all infra helpers).
INFRA_TOOL_IDS = {
    "io_tools.save_ret",
    "io_tools.tool_call",
    "ctx_tools.fetch_turn_artifacts",
    "ctx_tools.fetch_ctx",
    "ctx_tools.merge_sources",
}

PY_EXEC_ONLY_TOOL_IDS = {
    "io_tools.tool_call",
    "ctx_tools.fetch_ctx",
}
# Codegen-only infra tools (hidden from decision/common tool list).
CODEGEN_ONLY_TOOL_IDS = {
    "io_tools.save_ret",
    "ctx_tools.fetch_turn_artifacts",
    "ctx_tools.merge_sources",
}

EXEC_TOOLS = {
    "exec_tools.execute_code_python"
}

# Tools that accept/need citations on input
CITATION_AWARE_TOOL_IDS = {
    "llm_tools.generate_content_llm",
}

CITATION_AWARE_WANT_SOURCES_LIST_TOOL_IDS = {
    "llm_tools.generate_content_llm",
}

WRITE_TOOLS = {
    "rendering_tools.write_pdf",
    "rendering_tools.write_pptx",
    "rendering_tools.write_docx",
    "rendering_tools.write_html",
    "rendering_tools.write_png",
    "infra.write"
}

CODEGEN_TOOLS = {
    "codegen_tools.codegen_python",
}

# Which tools produce *raw source lists* that must be merged into the pool and re-SID'd
SEARCH_TOOL_IDS = {
    "web_tools.web_search",
}

FETCH_URI_TOOL_IDS = {
    "web_tools.web_fetch",
}
GENERATIVE_TOOL_IDS = {
    "llm_tools.generate_content_llm",
}

CITABLE_TOOL_IDS = {
    "web_tools.web_search",
    "web_tools.web_fetch",
    "sdk_tools.kb_search",
    "ctx_tools.merge_sources",
}

def is_write_tool(tool_id: str) -> bool:
    return tool_id in WRITE_TOOLS

def is_codegen_tool(tool_id: str) -> bool:
    return tool_id in CODEGEN_TOOLS

def is_exec_tool(tool_id: str) -> bool:
    return tool_id in EXEC_TOOLS

def is_code_tool(tool_id: str) -> bool:
    return is_exec_tool(tool_id) or is_codegen_tool(tool_id)

def does_tool_accept_sources(tool_id: str) -> bool:
    return tool_id in CITATION_AWARE_TOOL_IDS

def wants_sources_list(tool_id: str) -> bool:
    return tool_id in CITATION_AWARE_WANT_SOURCES_LIST_TOOL_IDS

def is_search_tool(tool_id: str) -> bool|None:
    # None means "we do not know"
    return tool_id in SEARCH_TOOL_IDS if tool_id in BUILTIN_TOOLS else None

def is_fetch_uri_content_tool(tool_id: str) -> bool|None:
    # None means "we do not know"
    return tool_id in FETCH_URI_TOOL_IDS if tool_id in BUILTIN_TOOLS else None

def is_exploration_tool(tool_id: str) -> bool:
    return tool_id in CITABLE_TOOL_IDS

def is_generative_tool(tool_id: str) -> bool|None:
    # None means "we do not know"
    return tool_id in GENERATIVE_TOOL_IDS if tool_id in BUILTIN_TOOLS else None

def is_builtin_tool(tool_id: str) -> bool:
    return tool_id in BUILTIN_TOOLS

def should_isolate_tool_execution(tool_id: str) -> bool:
    # Isolate write + web tools to protect main process from native crashes.
    return should_isolate_in_docker(tool_id) or is_write_tool(tool_id) or is_search_tool(tool_id) or is_fetch_uri_content_tool(tool_id)

def should_isolate_in_docker(tool_id: str) -> bool:
    return False

def is_source_producing_tool(tool_id: str) -> bool:
    return is_search_tool(tool_id) or is_fetch_uri_content_tool(tool_id)

def tool_isolation(tool_id: str) -> Literal["none", "docker", "local"]:
    if should_isolate_in_docker(tool_id):
        return "docker"
    elif should_isolate_tool_execution(tool_id):
        return "local"
    else:
        return "none"


def default_mime_for_write_tool(tool_id: str) -> str:
    return {
        "rendering_tools.write_pdf":  "application/pdf",
        "rendering_tools.write_pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "rendering_tools.write_docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "rendering_tools.write_html": "text/html",
        "rendering_tools.write_png":  "image/png",
        "rendering_tools.write_xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(tool_id, "application/octet-stream")
