# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""LinkedIn tool declarations for the productivity MCP surface.

Text posting and commenting only. Image publishing needs bytes, which reach
KDCube through the `linkedin` named-service namespace (staged or inline files)
or through the ReAct tools (turn workspace paths).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from pydantic import Field

from kdcube_ai_app.apps.chat.sdk.integrations.linkedin.tools import (
    LINKEDIN_POST_CLAIM,
    LINKEDIN_PROFILE_CLAIM,
    LINKEDIN_PROVIDER_ID,
    LinkedInTools,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata import (
    read_only_annotations,
    write_annotations,
)

EnforceTool = Callable[[str, str, str], Awaitable[dict[str, Any] | None]]


LINKEDIN_PRODUCTIVITY_TOOLS: dict[str, dict[str, Any]] = {
    # No connected_accounts requirement: this reads KDCube's own connection
    # records and calls no LinkedIn API, matching object.list on the linkedin
    # namespace. Declaring a claim here would resolve an account before the
    # caller has one to name, and the tool takes no account_id to name it with.
    "productivity_linkedin_accounts": {
        "label": "List LinkedIn accounts",
        "description": "List the user's connected LinkedIn accounts.",
    },
    "productivity_linkedin_profile": {
        "label": "Read LinkedIn profile",
        "description": "Read the connected LinkedIn member's own profile.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": LINKEDIN_PROVIDER_ID, "claims": [LINKEDIN_PROFILE_CLAIM]},
                ],
            },
        },
    },
    "productivity_linkedin_post": {
        "label": "Publish a LinkedIn post",
        "description": "Publish a text post as the connected LinkedIn member.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": LINKEDIN_PROVIDER_ID, "claims": [LINKEDIN_POST_CLAIM]},
                ],
            },
        },
    },
    "productivity_linkedin_comment": {
        "label": "Comment on a LinkedIn post",
        "description": "Add a comment to a LinkedIn post as the connected member.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": LINKEDIN_PROVIDER_ID, "claims": [LINKEDIN_POST_CLAIM]},
                ],
            },
        },
    },
    "productivity_linkedin_post_image": {
        "label": "Publish a LinkedIn post with images",
        "description": "Publish a post with images staged through a signed upload slot.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": LINKEDIN_PROVIDER_ID, "claims": [LINKEDIN_POST_CLAIM]},
                ],
            },
        },
    },
}


def register_linkedin_tools(
    *,
    mcp: Any,
    tool_annotations_type: Any,
    enforce: EnforceTool,
) -> None:
    """Register LinkedIn account reads and bounded write actions."""

    ToolAnnotations = tool_annotations_type
    _enforce = enforce
    linkedin = LinkedInTools()

    @mcp.tool(
        name="productivity_linkedin_accounts",
        title="List LinkedIn accounts",
        description=(
            "List the approving user's connected LinkedIn accounts. Use this to pick an "
            "account_id before publishing when several accounts are connected. Returns "
            "{ok, error, ret}; ret={accounts:[{account_id,display_name,email,status,claims,"
            "author_urn}],count}."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="List LinkedIn accounts"),
        structured_output=False,
    )
    async def _productivity_linkedin_accounts() -> dict[str, Any]:
        denial = await _enforce("productivity_linkedin_accounts", "list", "")
        if denial is not None:
            return denial
        return await linkedin.list_linkedin_accounts()

    @mcp.tool(
        name="productivity_linkedin_profile",
        title="Read LinkedIn profile",
        description=(
            "Read the connected LinkedIn member's own profile. LinkedIn exposes no other "
            "members' profiles and no people search to this integration. Returns "
            "{ok, error, ret}; ret={subject,name,email,account_id,author_urn}."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Read LinkedIn profile"),
        structured_output=False,
    )
    async def _productivity_linkedin_profile(
        account_id: Annotated[
            str,
            Field(description="Optional connected account id when several are connected."),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_linkedin_profile", "get", account_id)
        if denial is not None:
            return denial
        return await linkedin.get_linkedin_profile(account_id=account_id)

    @mcp.tool(
        name="productivity_linkedin_post",
        title="Publish a LinkedIn post",
        description=(
            "Publish a text post to the approving user's LinkedIn feed. Markdown is "
            "stripped and the text is truncated to 3000 characters. Returns "
            "{ok, error, ret}; ret={post_urn,permalink,account_id,author,image_count}. "
            "Pass post_urn to productivity_linkedin_comment to comment on it."
        ),
        annotations=write_annotations(ToolAnnotations, title="Publish a LinkedIn post"),
        structured_output=False,
    )
    async def _productivity_linkedin_post(
        text: Annotated[
            str,
            Field(description="Post body. LinkedIn renders plain text only."),
        ],
        visibility: Annotated[
            str,
            Field(description="PUBLIC or CONNECTIONS."),
        ] = "PUBLIC",
        account_id: Annotated[
            str,
            Field(description="Optional connected account id when several are connected."),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_linkedin_post", "action", account_id)
        if denial is not None:
            return denial
        return await linkedin.post_linkedin_update(
            text=text, visibility=visibility, account_id=account_id
        )

    @mcp.tool(
        name="productivity_linkedin_post_image",
        title="Publish a LinkedIn post with images",
        description=(
            "Publish a post with images to the approving user's LinkedIn feed. Images "
            "must already be staged: obtain each staged_ref from the linkedin namespace "
            "request_upload action and POST the bytes to its upload_url. Raw image bytes "
            "are never accepted here. One image is attached inline, several become a "
            "multi-image post. Returns {ok, error, ret}; ret={post_urn,permalink,"
            "account_id,author,image_count}."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Publish a LinkedIn post with images"
        ),
        structured_output=False,
    )
    async def _productivity_linkedin_post_image(
        text: Annotated[
            str,
            Field(description="Post body. LinkedIn renders plain text only."),
        ],
        staged_refs: Annotated[
            list[str],
            Field(description="Staged image refs from request_upload, in display order."),
        ],
        alt_texts: Annotated[
            list[str],
            Field(description="Alt text per image, positional."),
        ] = [],
        visibility: Annotated[str, Field(description="PUBLIC or CONNECTIONS.")] = "PUBLIC",
        account_id: Annotated[
            str,
            Field(description="Optional connected account id when several are connected."),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_linkedin_post_image", "action", account_id)
        if denial is not None:
            return denial
        return await linkedin.publish_staged(
            text=text,
            staged_refs=staged_refs or [],
            alt_texts=alt_texts or [],
            visibility=visibility,
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_linkedin_comment",
        title="Comment on a LinkedIn post",
        description=(
            "Add a comment to a LinkedIn post as the approving user. post_urn is the value "
            "returned by productivity_linkedin_post, for example urn:li:share:7123456789. "
            "Returns {ok, error, ret}; ret={comment_id,comment_urn,post_urn,account_id}."
        ),
        annotations=write_annotations(ToolAnnotations, title="Comment on a LinkedIn post"),
        structured_output=False,
    )
    async def _productivity_linkedin_comment(
        post_urn: Annotated[
            str,
            Field(description="Target post URN, e.g. urn:li:share:7123456789."),
        ],
        text: Annotated[str, Field(description="Comment text.")],
        account_id: Annotated[
            str,
            Field(description="Optional connected account id when several are connected."),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_linkedin_comment", "action", account_id)
        if denial is not None:
            return denial
        return await linkedin.comment_on_linkedin_post(
            post_urn=post_urn, text=text, account_id=account_id
        )


__all__ = ["LINKEDIN_PRODUCTIVITY_TOOLS", "register_linkedin_tools"]
