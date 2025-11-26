# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/tools_insights.py

# TODO full list of built-ins
BUILTIN_TOOLS = {
    "llm_tools.generate_content_llm",
    "generic_tools.web_search",
    "generic_tools.fetch_url_contents",
    "generic_tools.write_pdf",
    "generic_tools.write_pptx",
    "generic_tools.write_docx",
    "generic_tools.write_html",
    "generic_tools.write_png",
    "generic_tools.write_xlsx",
    "generic_tools.write_file",
}

EXEC_TOOLS = {
    "exec_tools.execute_code_python"
}

# Tools that accept/need citations on input
CITATION_AWARE_TOOL_IDS = {
    "llm_tools.generate_content_llm",
    "generic_tools.write_pdf",
    "generic_tools.write_docx",
    "generic_tools.write_html",
    "generic_tools.write_pptx",
}

CITATION_AWARE_WANT_SOURCES_JSON_TOOL_IDS = {
    "llm_tools.generate_content_llm"
}
CITATION_AWARE_WANT_SOURCES_PARAM_TOOL_IDS = {
    "generic_tools.write_pdf",
    "generic_tools.write_docx",
    "generic_tools.write_html",
    "generic_tools.write_pptx"
}

WRITE_TOOLS = {
    "generic_tools.write_pdf",
    "generic_tools.write_pptx",
    "generic_tools.write_docx",
    "generic_tools.write_html",
    "generic_tools.write_png",
    "generic_tools.write_xlsx",
    "generic_tools.write_file",
}

# Which tools produce *raw source lists* that must be merged into the pool and re-SID'd
SEARCH_TOOL_IDS = {
    "generic_tools.web_search",
}

FETCH_URI_TOOL_IDS = {
    "generic_tools.fetch_url_contents",
}
GENERATIVE_TOOL_IDS = {
    "llm_tools.generate_content_llm",
}

def is_write_tool(tool_id: str) -> bool:
    return tool_id in WRITE_TOOLS

def is_code_exec_tool(tool_id: str) -> bool:
    return tool_id in WRITE_TOOLS

def does_tool_accept_sources(tool_id: str) -> bool:
    return tool_id in CITATION_AWARE_TOOL_IDS

def wants_sources_json(tool_id: str) -> bool:
    return tool_id in CITATION_AWARE_WANT_SOURCES_JSON_TOOL_IDS

def wants_sources_param(tool_id: str) -> bool:
    return tool_id in CITATION_AWARE_WANT_SOURCES_PARAM_TOOL_IDS

def is_search_tool(tool_id: str) -> bool|None:
    # None means "we do not know"
    return tool_id in SEARCH_TOOL_IDS if tool_id in BUILTIN_TOOLS else None

def is_fetch_uri_content_tool(tool_id: str) -> bool|None:
    # None means "we do not know"
    return tool_id in FETCH_URI_TOOL_IDS if tool_id in BUILTIN_TOOLS else None

def is_generative_tool(tool_id: str) -> bool|None:
    # None means "we do not know"
    return tool_id in GENERATIVE_TOOL_IDS if tool_id in BUILTIN_TOOLS else None

def should_isolate_tool_execution(tool_id: str) -> bool:
    # For now, only write tools are isolated
    return is_write_tool(tool_id) or is_code_exec_tool(tool_id)

def default_mime_for_write_tool(tool_id: str) -> str:
    return {
        "generic_tools.write_pdf":  "application/pdf",
        "generic_tools.write_pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "generic_tools.write_docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "generic_tools.write_html": "text/html",
        "generic_tools.write_png":  "image/png",
        "generic_tools.write_xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(tool_id, "application/octet-stream")