from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    from kdcube_ai_app.apps.chat.sdk.config import get_secret, get_settings, get_user_secret
    from kdcube_ai_app.apps.chat.sdk.solutions.claude_code import (
        CLAUDE_CODE_EXECUTIVE_JOURNAL_CODE_PREFIX,
        CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX,
        ClaudeCodeAgent,
        ClaudeCodeAgentConfig,
        ClaudeCodeBinding,
        ClaudeCodeWorkspaceConfig,
        run_claude_code_turn,
    )
    from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import set_skills_descriptor
except Exception:  # pragma: no cover - imported by narrow unit tests without full SDK.
    get_secret = None  # type: ignore[assignment]
    get_settings = None  # type: ignore[assignment]
    get_user_secret = None  # type: ignore[assignment]
    CLAUDE_CODE_EXECUTIVE_JOURNAL_CODE_PREFIX = "EXECUTIVE_JOURNAL_CODE"
    CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX = "EXECUTIVE_JOURNAL"
    ClaudeCodeAgent = None  # type: ignore[assignment]
    ClaudeCodeAgentConfig = None  # type: ignore[assignment]
    ClaudeCodeBinding = None  # type: ignore[assignment]
    ClaudeCodeWorkspaceConfig = None  # type: ignore[assignment]
    run_claude_code_turn = None  # type: ignore[assignment]
    set_skills_descriptor = None  # type: ignore[assignment]

from .mcp import (
    EMAIL_MCP_ALLOWED_TOOLS,
    EMAIL_MCP_SERVER_NAME,
    EmailMCPRunStore,
    create_email_mcp_run,
    safe_segment,
)


DEFAULT_EMAIL_BUNDLE_ID = "task-and-memo-app@1-0"
BUNDLE_ID = DEFAULT_EMAIL_BUNDLE_ID
logger = logging.getLogger("kdcube.integrations.email.claude")
EMAIL_CLAUDE_SKILL_ID = "productivity.email"
EMAIL_CLAUDE_SKILLS_DESCRIPTOR: Any = None
EMAIL_CLAUDE_BUNDLE_ROOT: Path | None = None


def claude_code_enabled(entrypoint: Any) -> bool:
    return bool(entrypoint.bundle_prop("integrations.email.claude_code.enabled", True))


def _secret_lookup(*keys: str) -> str:
    if get_secret is None:
        return ""
    for key in keys:
        value = get_secret(key)
        if value:
            return str(value)
    return ""


def _user_secret_lookup(*, user_id: str, bundle_id: str, key: str) -> str:
    if get_user_secret is None:
        return ""
    try:
        value = get_user_secret(key, user_id=user_id, bundle_id=bundle_id)
    except Exception:
        return ""
    return str(value or "")


def _claude_code_env(*, entrypoint: Any, user_id: str, bundle_id: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    anthropic_key = _secret_lookup("services.anthropic.api_key", "ANTHROPIC_API_KEY")
    claude_code_key = (
        _user_secret_lookup(user_id=user_id, bundle_id=bundle_id, key="anthropic.claude_code_key")
        or _secret_lookup("services.anthropic.claude_code_key", "CLAUDE_CODE_KEY")
    )
    if anthropic_key:
        env["ANTHROPIC_API_KEY"] = anthropic_key
    if claude_code_key:
        env["CLAUDE_CODE_KEY"] = claude_code_key
    env["MCP_TIMEOUT"] = str(entrypoint.bundle_prop("integrations.email.claude_code.mcp_timeout_ms", 10000) or 10000)
    env["MCP_TOOL_TIMEOUT"] = str(entrypoint.bundle_prop("integrations.email.claude_code.mcp_tool_timeout_ms", 60000) or 60000)
    env["MAX_MCP_OUTPUT_TOKENS"] = str(entrypoint.bundle_prop("integrations.email.claude_code.max_mcp_output_tokens", 50000) or 50000)
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


def _mcp_base_url(entrypoint: Any) -> str:
    configured = str(entrypoint.bundle_prop("integrations.email.claude_code.mcp_base_url", "") or "").strip().rstrip("/")
    if configured:
        return configured
    if get_settings is not None:
        try:
            settings = get_settings()
            port = int(getattr(settings, "CHAT_PROCESSOR_PORT", None) or 8020)
            return f"http://127.0.0.1:{port}"
        except Exception:
            pass
    return "http://127.0.0.1:8020"


def _mcp_url(entrypoint: Any, *, tenant: str, project: str, bundle_id: str) -> str:
    tenant = str(tenant or "").strip()
    project = str(project or "").strip()
    if not tenant or not project:
        raise ValueError("tenant/project are required to build the email MCP URL")
    base = _mcp_base_url(entrypoint)
    return f"{base}/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/email"


def _claude_session_id_for_run(run_id: str) -> str:
    raw = str(run_id or "").rsplit("_", 1)[-1]
    try:
        if len(raw) == 32:
            return str(uuid.UUID(hex=raw))
    except Exception:
        pass
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{BUNDLE_ID}:email:{run_id}"))


def _claude_workspace_config(*, mcp_url: str, token_header: str, token: str):
    if ClaudeCodeWorkspaceConfig is None:
        raise RuntimeError("Claude Code workspace SDK integration is unavailable in this runtime.")
    if set_skills_descriptor is not None and EMAIL_CLAUDE_SKILLS_DESCRIPTOR is not None:
        bundle_root = EMAIL_CLAUDE_BUNDLE_ROOT or Path.cwd()
        set_skills_descriptor(
            {
                "CUSTOM_SKILLS_ROOT": getattr(EMAIL_CLAUDE_SKILLS_DESCRIPTOR, "CUSTOM_SKILLS_ROOT", None),
                "AGENTS_CONFIG": getattr(EMAIL_CLAUDE_SKILLS_DESCRIPTOR, "AGENTS_CONFIG", None),
            },
            bundle_root=bundle_root,
        )
    return ClaudeCodeWorkspaceConfig(
        mcp_servers={
            EMAIL_MCP_SERVER_NAME: {
                "type": "http",
                "url": mcp_url,
                "headers": {
                    token_header: token,
                },
            }
        },
        enabled_mcp_servers=[EMAIL_MCP_SERVER_NAME],
        allowed_tools=list(EMAIL_MCP_ALLOWED_TOOLS),
        skill_ids=[EMAIL_CLAUDE_SKILL_ID],
        skill_allowed_tools={EMAIL_CLAUDE_SKILL_ID: list(EMAIL_MCP_ALLOWED_TOOLS)},
        denied_tools=["Bash", "Read", "Edit", "Write", "WebFetch", "WebSearch"],
        instructions_markdown="\n".join(
            [
                "# Task And Memo Email Processor",
                "",
                "Use only the configured task_memo_email MCP tools.",
                "Do not read local files, run shell commands, or access email outside the scoped MCP run.",
                "Always call task_context first.",
                "For saved or recurring tasks, call restore_current_task_state early.",
                (
                    "For saved or recurring tasks, always call store_current_task_state before "
                    "record_processing_result with compact future-run state."
                ),
                (
                    "Use compact task-specific state for noisy inboxes when the task needs future memory: "
                    "last_successful_search_query, a high-watermark timestamp, durable matching rules, and "
                    "a short prior-run summary. Store message ids only when the task explicitly needs exact "
                    "id tie-breaking; never store a full mailbox history."
                ),
                "Choose the email search scope from the task context, restored state, and user instruction; call search_messages before reading messages.",
                (
                    "Inspect messages and attachment metadata, then call record_processing_result before the final answer. "
                    "Do not download attachment bytes unless the task requires reading the attachment contents."
                ),
                (
                    "When you learn something useful before the final result, emit a one-line executive journal "
                    f"checkpoint using `{CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX} <short note>` or "
                    f"`{CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX} {{...compact JSON...}}`. "
                    f"If the useful checkpoint is code, emit `{CLAUDE_CODE_EXECUTIVE_JOURNAL_CODE_PREFIX} <code>` "
                    f"or `{CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX} {{\"channel\":\"code\",\"code\":\"...\"}}`. "
                    "Use it for recoverable insights such as search counts, matching decisions, uncertainty, "
                    "or a meaningful error. Keep entries compact and never include secrets or full email bodies."
                ),
                "",
            ]
        ),
    )


def _prompt(
    *,
    task_id: str,
    task_definition: str,
    instruction: str,
    account: Mapping[str, Any],
    mailbox: str,
    unread_only: bool,
    gmail_query: str,
    message_count: int,
) -> str:
    return "\n".join(
        [
            "Process this saved task's email goal by choosing the right scoped email search through MCP.",
            "",
            f"Task id: {task_id or 'manual'}",
            f"Account: {account.get('email') or account.get('account_id')}",
            f"Provider: {account.get('provider') or 'google'}",
            f"Mailbox: {mailbox or 'inbox'}",
            f"Unread-only: {bool(unread_only)}",
            f"Default search query: {gmail_query or '(none)'}",
            f"Initially scoped candidate message count: {message_count}",
            "",
            "Original task definition:",
            str(task_definition or "").strip() or "(not provided)",
            "",
            "Task-specific instruction:",
            str(instruction or "").strip() or "(not provided)",
            "",
            "Required process:",
            "1. Call mcp__task_memo_email__task_context.",
            (
                "2. For a saved or recurring task, call mcp__task_memo_email__restore_current_task_state "
                "and use any stored cursor/rules/summary only when relevant to this task."
            ),
            (
                "3. Decide the email search query/range from the task goal. Use the default query only if it matches the goal. "
                "For Gmail you may use Gmail search syntax; for iCloud use common filters such as from_email, to_email, subject, since, before, or query text. "
                "For recurring 'new emails' tasks, use restored task-specific state only if it still matches the task goal. "
                "The SDK does not suppress previously processed messages; you decide what counts as new for this task."
            ),
            (
                "4. Call mcp__task_memo_email__search_messages. Use returned attachment metadata directly when "
                "message_id/attachment_id/filename/mime_type is enough. Call get_message only for messages that need full body content."
            ),
            (
                f"5. If search_messages produces useful recoverable context, emit {CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX} "
                "with a short note or compact JSON. This is optional for trivial runs."
            ),
            (
                "6. Call get_message_attachment only when the task requires inspecting attachment contents. "
                "Do not download PDFs just to return or record attachment ids."
            ),
            "7. Decide which messages match the task condition.",
            (
                f"8. If classification produces useful recoverable context, emit {CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX} "
                "with a short note or compact JSON."
            ),
            (
                "9. For saved or recurring tasks, call mcp__task_memo_email__store_current_task_state with compact JSON before recording the result. "
                "Include only task-relevant future hints such as cursor.high_watermark_internal_date_ms/high_watermark_at, "
                "last_successful_search_query, total_processed_count, durable matching rules, and a short prior-run summary. "
                "Store message ids only if the task explicitly needs exact tie-breaking; never store an unbounded processed-email list."
            ),
            "10. Call mcp__task_memo_email__record_processing_result with processed_message_ids, matched_message_ids, summary, user_notification, and optional compact JSON details.",
            (
                f"11. If record_processing_result succeeds or fails in a way worth preserving, emit one final "
                f"{CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX} checkpoint before the final answer."
            ),
            "12. Final answer must be short and user-facing. Mention no internal tokens or implementation details.",
        ]
    ).strip()


async def run_email_processor_with_claude_code(
    *,
    entrypoint: Any,
    storage_root: str | Path,
    user_id: str,
    bundle_id: str,
    tenant: str,
    project: str,
    account: Mapping[str, Any],
    mailbox: str,
    unread_only: bool,
    limit: int,
    gmail_query: str,
    task_id: str,
    task_definition: str,
    instruction: str,
    messages: list[Mapping[str, Any]],
    execution_id: str = "",
    conversation_id: str = "",
    session_id: str = "",
    comm: Any = None,
) -> Dict[str, Any]:
    if (
        ClaudeCodeAgent is None
        or ClaudeCodeAgentConfig is None
        or ClaudeCodeBinding is None
        or ClaudeCodeWorkspaceConfig is None
        or run_claude_code_turn is None
    ):
        logger.warning("[email.claude] SDK unavailable | user_id=%s task_id=%s", user_id, task_id or "manual")
        return {
            "ok": False,
            "error": {"code": "claude_code_sdk_unavailable", "message": "Claude Code SDK integration is unavailable in this runtime."},
        }

    mcp_run = create_email_mcp_run(
        entrypoint=entrypoint,
        storage_root=storage_root,
        user_id=user_id,
        task_id=task_id,
        execution_id=execution_id,
        account=account,
        mailbox=mailbox,
        unread_only=unread_only,
        limit=limit,
        gmail_query=gmail_query,
        task_definition=task_definition,
        instruction=instruction,
        messages=messages,
    )
    run_doc = mcp_run["run"]
    run_id = str(run_doc["run_id"])
    claude_session_id = _claude_session_id_for_run(run_id)
    workspace = (
        Path(storage_root).resolve()
        / "email"
        / "claude_code"
        / safe_segment(user_id, fallback="anonymous")
        / safe_segment(task_id or "manual", fallback="manual")
        / safe_segment(run_id, fallback="run")
    )
    mcp_url = _mcp_url(entrypoint, tenant=tenant, project=project, bundle_id=bundle_id)
    logger.info(
        "[email.claude] run prepared | run_id=%s claude_session_id=%s user_id=%s account=%s "
        "messages=%s mcp_url=%s workspace=%s",
        run_id,
        claude_session_id,
        user_id,
        account.get("email") or account.get("account_id"),
        len(messages),
        mcp_url,
        workspace,
    )
    workspace_config = _claude_workspace_config(
        mcp_url=mcp_url,
        token_header=str(mcp_run["token_header"]),
        token=str(mcp_run["token"]),
    )

    model = str(entrypoint.bundle_prop("integrations.email.claude_code.model", "sonnet") or "sonnet").strip()
    command = str(entrypoint.bundle_prop("integrations.email.claude_code.command", "claude") or "claude").strip() or "claude"
    try:
        timeout_seconds = float(entrypoint.bundle_prop("integrations.email.claude_code.timeout_seconds", 300) or 300)
    except Exception:
        timeout_seconds = 300.0
    prompt = _prompt(
        task_id=task_id,
        task_definition=task_definition,
        instruction=instruction,
        account=account,
        mailbox=mailbox,
        unread_only=unread_only,
        gmail_query=gmail_query,
        message_count=len(messages),
    )
    conv_id = str(conversation_id or session_id or f"email_task_{run_id}").strip()
    binding = ClaudeCodeBinding(
        user_id=user_id,
        conversation_id=conv_id,
        session_id=str(session_id or conv_id),
        claude_session_id=claude_session_id,
    )
    agent = ClaudeCodeAgent(
        config=ClaudeCodeAgentConfig(
            agent_name="task-memo-email-processor",
            workspace_path=workspace,
            model=model,
            allowed_tools=list(EMAIL_MCP_ALLOWED_TOOLS),
            workspace_config=workspace_config,
            env=_claude_code_env(entrypoint=entrypoint, user_id=user_id, bundle_id=bundle_id),
            command=command,
            permission_mode="acceptEdits",
            timeout_seconds=timeout_seconds,
            step_name="email.claude_code",
            delta_marker="email_processing",
            executive_journal_prefixes=(
                CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX,
                CLAUDE_CODE_EXECUTIVE_JOURNAL_CODE_PREFIX,
            ),
            executive_journal_max_entries=100,
        ),
        binding=binding,
        comm=comm,
    )
    logger.info(
        "[email.claude] turn start | run_id=%s model=%s command=%s timeout_seconds=%s allowed_tools=%s",
        run_id,
        model,
        command,
        timeout_seconds,
        list(EMAIL_MCP_ALLOWED_TOOLS),
    )
    result = await run_claude_code_turn(agent=agent, prompt=prompt)
    recorded = None
    final_run_doc: Dict[str, Any] = {}
    try:
        final_run_doc = EmailMCPRunStore(storage_root, user_id=user_id).read_run(run_id)
        recorded = final_run_doc.get("result")
    except Exception:
        recorded = None
        final_run_doc = {}
    recorded_ok = isinstance(recorded, dict)
    mcp_ok = recorded_ok
    warnings: list[Dict[str, Any]] = []
    error_message = str(result.error_message or "").strip()
    error_code = ""
    if recorded_ok and result.status != "completed":
        warnings.append(
            {
                "code": "claude_code_mcp_result_recorded_but_process_failed",
                "message": (
                    "Claude Code recorded the email MCP result, but the Claude process "
                    "did not exit cleanly afterward."
                ),
                "status": result.status,
                "process_error": error_message,
            }
        )
        logger.warning(
            "[email.claude] MCP result recorded but Claude process failed | "
            "run_id=%s status=%s exit_code=%s error=%s",
            run_id,
            result.status,
            result.exit_code,
            error_message,
        )
    elif result.status == "completed" and not recorded_ok:
        error_code = "claude_code_mcp_result_not_recorded"
        error_message = (
            "Claude Code email MCP run completed, but it did not call "
            "record_processing_result. Treat the MCP sub-processor as failed."
        )
        logger.warning(
            "[email.claude] turn completed without recorded MCP result | "
            "run_id=%s exit_code=%s final_text_tail=%s",
            run_id,
            result.exit_code,
            str(result.final_text or "")[-500:],
        )
    elif result.status != "completed":
        error_code = "claude_code_email_processing_failed"
        error_message = error_message or "Claude Code email processing failed."

    if mcp_ok:
        logger.info(
            "[email.claude] turn completed | run_id=%s exit_code=%s recorded_result=%s usage=%s",
            run_id,
            result.exit_code,
            recorded_ok,
            result.usage or {},
        )
    else:
        logger.warning(
            "[email.claude] turn failed | run_id=%s status=%s exit_code=%s error=%s stderr_tail=%s",
            run_id,
            result.status,
            result.exit_code,
            error_message,
            result.stderr_lines[-5:],
        )
    return {
        "ok": mcp_ok,
        "run_id": run_id,
        "workspace_path": str(workspace),
        "mcp_url": mcp_url,
        "allowed_tools": list(EMAIL_MCP_ALLOWED_TOOLS),
        "status": result.status,
        "final_text": result.final_text,
        "executive_journal": list(getattr(result, "executive_journal", []) or []),
        "recorded_result": recorded if isinstance(recorded, dict) else None,
        "candidate_message_ids": list(final_run_doc.get("candidate_message_ids") or []),
        "candidate_message_count": len(final_run_doc.get("candidate_message_ids") or []),
        "messages": list(final_run_doc.get("messages") or []),
        "last_search": final_run_doc.get("last_search") or None,
        "exit_code": result.exit_code,
        "error_code": error_code,
        "error_message": result.error_message,
        "effective_error_message": error_message,
        "timed_out": bool(getattr(result, "timed_out", False)),
        "timeout_seconds": getattr(result, "timeout_seconds", None),
        "warnings": warnings,
        "stderr_tail": result.stderr_lines[-5:],
        "model": result.model,
        "requested_model": result.requested_model,
        "usage": result.usage or {},
        "cost_usd": result.cost_usd,
        "duration_ms": getattr(result, "duration_ms", None),
        "api_duration_ms": getattr(result, "api_duration_ms", None),
    }
