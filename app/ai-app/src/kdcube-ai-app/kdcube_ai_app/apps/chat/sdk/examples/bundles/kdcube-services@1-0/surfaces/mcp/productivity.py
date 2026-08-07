"""Productivity MCP surface: plain @mcp tools over connected accounts.

This is the reference PURE-MCP door: no named-service registration behind it.
Each tool declares which connected-account provider claims it needs (the
``ToolClaimPolicy`` shape under ``connections.delegated_to_kdcube.
connected_accounts``) and enforces them at execution time with
``enforce_tool_requirements`` - so a plain MCP tool answers with the SAME
demand ordering and consent envelopes as the named-services door:

- zero accounts on the backing provider -> connect-first denial (the guided
  connect plan, ending in the agent-grant hand-off);
- account present but unusable for the call -> the account-level consent
  (claim upgrade / agent grant / reconnect / account pick);
- everything resolves -> the wrapped provider tool runs.

Copy this module as the template for your own pure-MCP door: declare the
tool's requirements in ``PRODUCTIVITY_TOOLS``, call ``_prepare()`` +
``enforce_tool_requirements`` first in every tool body, then run the real
provider work.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Mapping

from pydantic import Field

from kdcube_ai_app.apps.chat.sdk.integrations.google.gmail_tools import GmailTools
from kdcube_ai_app.apps.chat.sdk.integrations.slack.tools import SlackTools
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.credential_view import (
    delegated_credential_view,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    ToolClaimPolicy,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata import (
    kdcube_mcp_icons,
    kdcube_website_url,
    read_only_annotations,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_tool_enforcement import (
    bind_service_connector_apps_from_config,
    enforce_tool_requirements,
)

from ...services.named_services.request_scope import set_public_base_url_from_request
from ...services.productivity.google_docs import GoogleDocsService
from .productivity_docs import (
    DOCS_PRODUCTIVITY_TOOLS,
    register_google_docs_tools,
)
from .productivity_linkedin import (
    LINKEDIN_PRODUCTIVITY_TOOLS,
    register_linkedin_tools,
)
from .productivity_sheets import (
    SHEETS_PRODUCTIVITY_TOOLS,
    register_google_sheets_tools,
)

ConfigFactory = Callable[[], Mapping[str, Any]]

PRODUCTIVITY_MCP_INSTRUCTIONS = """\
This MCP server exposes productivity tools that run on the approving user's
connected accounts (Slack, mail, Google Sheets, Google Docs, LinkedIn). For
Sheets, use search when the spreadsheet id is unknown, describe before
structural changes, and pass the returned stable ids to read or write tools. For
Docs, use search when the document id is unknown, get before editing, and pass
the returned document id and text indices to the edit and comment tools. For
LinkedIn, list accounts first when several may be connected, then publish; pass
the returned post_urn to the comment tool. LinkedIn exposes no feed, message or
post-content reads here. Each tool names the account access it needs. When a call reports a consent requirement, relay the reason
and connection_hub_url to the user instead of retrying blindly:
connect_required, claim_upgrade_required, and reconnect_required are fixed by
the user at connection_hub_url; account_required is fixed by resending the
same call with account_id set from candidates.
"""

# Declarative per-tool requirements, in the SAME shape application tool
# configs use (ToolClaimPolicy.from_tool_config). ``claims`` speak the
# PROVIDER's claim vocabulary - what a connected account of that provider
# row can hold.
PRODUCTIVITY_TOOLS: dict[str, dict[str, Any]] = {
    "productivity_slack_search": {
        "label": "Search Slack",
        "description": "Search Slack messages through the user's connected Slack account.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": "slack", "claims": ["slack:search"]},
                ],
            },
        },
    },
    "productivity_mail_search": {
        "label": "Search mail",
        "description": "Search the user's connected mail account.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": "google", "claims": ["gmail:read"]},
                ],
            },
        },
    },
    "productivity_mail_get": {
        "label": "Read mail message",
        "description": "Read one message from the user's connected mail account.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": "google", "claims": ["gmail:read"]},
                ],
            },
        },
    },
    **SHEETS_PRODUCTIVITY_TOOLS,
    **DOCS_PRODUCTIVITY_TOOLS,
    **LINKEDIN_PRODUCTIVITY_TOOLS,
}


def tool_requirements(tool_name: str) -> list[dict[str, Any]]:
    """The tool's declared connected-account requirements, parsed through the
    canonical ToolClaimPolicy reader (so the declaration and the enforcement
    can never drift on shape)."""
    policy = ToolClaimPolicy.from_tool_config(
        tool_name, PRODUCTIVITY_TOOLS.get(tool_name) or {}
    )
    return [item.to_dict() for item in policy.connected_accounts]


def build_productivity_mcp_app(
    *,
    name: str,
    config_factory: ConfigFactory,
    tenant_factory: Callable[[], str],
    project_factory: Callable[[], str],
    request: Any = None,
):
    """Build the managed productivity MCP surface (plain tools, stateless)."""

    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.mcp.server import KDCubeMCPServer
        from mcp.types import Icon, ToolAnnotations
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise ImportError("mcp server SDK is not installed") from exc

    icons = kdcube_mcp_icons(Icon, request=request)
    mcp = KDCubeMCPServer(
        name,
        instructions=PRODUCTIVITY_MCP_INSTRUCTIONS,
        icons=icons,
        website_url=kdcube_website_url(request=request),
    )

    slack = SlackTools()
    gmail = GmailTools()

    def _prepare() -> None:
        """Per-call request binding, mirroring the named-services bridge:

        - the public origin the client connected to (absolute out-of-band
          URLs);
        - the surface's connector-app declaration (which connector app serves
          each provider - never a user pick);
        - the calling client's delegated identity + per-account claim scope,
          so connected-account resolution is default-CLOSED for delegated
          callers (empty binding = nothing granted -> agent-grant consent).
        """
        set_public_base_url_from_request(request)
        bind_service_connector_apps_from_config(dict(config_factory() or {}))
        from kdcube_ai_app.apps.chat.sdk.solutions.connections.agent_account_scope import (
            set_agent_account_scope,
            set_agent_identity,
        )

        view = delegated_credential_view(request)
        set_agent_account_scope(view.account_scope)
        set_agent_identity(
            client_id=view.client_id,
            resource=view.resources[0] if view.resources else "",
        )

    async def _enforce(
        tool_name: str, operation: str, account_id: str = ""
    ) -> dict[str, Any] | None:
        _prepare()
        return await enforce_tool_requirements(
            request,
            tool_name=tool_name,
            operation=operation,
            requirements=tool_requirements(tool_name),
            account_id=account_id,
            tenant=tenant_factory(),
            project=project_factory(),
        )

    @mcp.tool(
        name="productivity_slack_search",
        title="Search Slack",
        description=(
            "Search Slack messages visible to the approving user's connected "
            "Slack account. Returns {ok, error, ret}; ret contains matching "
            "messages with channel, author, text, permalink."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Search Slack"),
        structured_output=False,
    )
    async def _productivity_slack_search(
        query: Annotated[str, Field(description="Slack search query.")],
        count: Annotated[
            int,
            Field(ge=1, le=20, description="Maximum results to return, 1-20."),
        ] = 10,
        account_id: Annotated[
            str,
            Field(
                description=(
                    "Optional connected account id when the user has several "
                    "Slack workspaces."
                )
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_slack_search", "search", account_id)
        if denial is not None:
            return denial
        return await slack.search_slack(query=query, count=count, account_id=account_id)

    @mcp.tool(
        name="productivity_mail_search",
        title="Search mail",
        description=(
            "Search the approving user's connected mail account. Returns "
            "{ok, error, ret}; ret contains message ids, subjects, senders, "
            "dates, snippets."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Search mail"),
        structured_output=False,
    )
    async def _productivity_mail_search(
        query: Annotated[
            str,
            Field(
                description=(
                    "Mail search query, for example "
                    "'from:alice@example.com newer_than:7d'."
                )
            ),
        ] = "",
        max_results: Annotated[
            int,
            Field(ge=1, le=10, description="Maximum messages to return, 1-10."),
        ] = 5,
        account_id: Annotated[
            str,
            Field(
                description=(
                    "Optional connected account id when the user has several "
                    "mail accounts."
                )
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_mail_search", "search", account_id)
        if denial is not None:
            return denial
        return await gmail.search_gmail(
            query=query, max_results=max_results, account_id=account_id
        )

    @mcp.tool(
        name="productivity_mail_get",
        title="Read mail message",
        description=(
            "Read one message body and attachment metadata from the approving "
            "user's connected mail account. Use productivity_mail_search first "
            "to get the message id. Returns {ok, error, ret}."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Read mail message"),
        structured_output=False,
    )
    async def _productivity_mail_get(
        message_id: Annotated[
            str,
            Field(description="Mail message id returned by productivity_mail_search."),
        ],
        include_html: Annotated[
            bool, Field(description="Include the HTML body in the result.")
        ] = False,
        account_id: Annotated[
            str,
            Field(
                description=(
                    "Optional connected account id when the user has several "
                    "mail accounts."
                )
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_mail_get", "get", account_id)
        if denial is not None:
            return denial
        return await gmail.read_gmail_message(
            message_id=message_id,
            include_html=include_html,
            account_id=account_id,
        )

    register_google_sheets_tools(
        mcp=mcp,
        tool_annotations_type=ToolAnnotations,
        enforce=_enforce,
    )

    register_google_docs_tools(
        mcp,
        service=GoogleDocsService(),
        enforce_tool=_enforce,
    )

    register_linkedin_tools(
        mcp=mcp,
        tool_annotations_type=ToolAnnotations,
        enforce=_enforce,
    )

    return mcp
