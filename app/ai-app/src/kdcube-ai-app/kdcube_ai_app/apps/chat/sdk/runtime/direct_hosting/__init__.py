"""Reusable SDK support for hosting agent cores in a direct Python process."""

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.configuration import (
    ConfiguredAgentInput,
    ConfiguredSkills,
    activate_configured_skills,
    configured_agent_tool_config,
    configured_agent_input,
    agent_instructions,
    configured_run_directory,
    configured_skills,
    configured_tool_connections,
    configured_tool_ids,
    configured_tool_settings,
    configured_web_search,
    verify_docker_image,
    verify_playwright_chromium,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.channels import (
    DirectInputAttachment,
    DirectTurnRequest,
    DirectTurnResult,
    add_direct_input_attachments,
    completed_direct_turn_result,
    prompt_with_attachment_manifest,
    run_terminal_chat,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.evidence import (
    ConsoleEmitter,
    print_evidence_summary,
    recorded_tool_items,
    utc_now,
    write_evidence_index,
    xlsx_contains_tool_evidence,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.infrastructure import (
    activate_platform_descriptors,
    direct_harness_config,
    platform_exec_profile,
    postgres_label,
    postgres_url,
    redis_url,
    storage_uri,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.instructions import (
    DirectInstructionSelection,
    PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE,
    compose_provider_native_instructions,
    configured_instruction_selection,
    native_react_instruction_blocks,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.local_setup import (
    configure,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.lifecycle import (
    direct_host_process_lifespan,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.model_service import (
    DirectModelSelection,
    build_model_service,
    configured_model_selection,
    embedding_service_if_configured,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.tool_runtime import (
    DirectToolRuntime,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.workspace import (
    DirectTurnWorkspace,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.telegram import (
    DirectTelegramConfig,
    DirectTelegramCredentials,
    DirectTelegramWebhook,
    configured_direct_telegram,
    serve_direct_telegram,
)

__all__ = [
    "ConfiguredAgentInput",
    "ConfiguredSkills",
    "ConsoleEmitter",
    "DirectInputAttachment",
    "DirectTelegramConfig",
    "DirectTelegramCredentials",
    "DirectTelegramWebhook",
    "DirectToolRuntime",
    "DirectTurnRequest",
    "DirectTurnResult",
    "DirectTurnWorkspace",
    "DirectInstructionSelection",
    "DirectModelSelection",
    "add_direct_input_attachments",
    "activate_configured_skills",
    "configured_agent_tool_config",
    "configured_agent_input",
    "configured_direct_telegram",
    "activate_platform_descriptors",
    "agent_instructions",
    "build_model_service",
    "configure",
    "configured_run_directory",
    "configured_skills",
    "configured_tool_connections",
    "configured_tool_ids",
    "configured_tool_settings",
    "configured_web_search",
    "configured_instruction_selection",
    "configured_model_selection",
    "embedding_service_if_configured",
    "compose_provider_native_instructions",
    "completed_direct_turn_result",
    "direct_harness_config",
    "direct_host_process_lifespan",
    "platform_exec_profile",
    "print_evidence_summary",
    "recorded_tool_items",
    "postgres_label",
    "postgres_url",
    "PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE",
    "redis_url",
    "native_react_instruction_blocks",
    "prompt_with_attachment_manifest",
    "run_terminal_chat",
    "serve_direct_telegram",
    "storage_uri",
    "utc_now",
    "verify_docker_image",
    "verify_playwright_chromium",
    "write_evidence_index",
    "xlsx_contains_tool_evidence",
]
