# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""LinkedIn named-service integration.

The ``linkedin`` namespace is a thin adapter over
:class:`~kdcube_ai_app.apps.chat.sdk.integrations.linkedin.tools.LinkedInTools`.
Provider calls and connected-account claim checks stay there.

The namespace is write-centric. LinkedIn gates content reads behind
``r_member_social``, which is restricted to approved applications, so there is
no ``object.search`` and ``object.get`` performs no provider read.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import (
    delete_staged,
    staging_root,
)
from kdcube_ai_app.apps.chat.sdk.integrations.inline_files import (
    InlineFileError,
    inline_files_workspace,
    materialize_inline_files,
    resolve_payload_file_entries,
)
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import rest_api
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin.tools import (
    LINKEDIN_ORG_POST_CLAIM,
    LINKEDIN_ORG_READ_CLAIM,
    LINKEDIN_POST_CLAIM,
    LINKEDIN_PROFILE_CLAIM,
    LINKEDIN_PROVIDER_ID,
    LinkedInTools,
    bind_integrations as bind_linkedin_integrations,
    bind_service as bind_linkedin_service,
    connected_linkedin_accounts,
    load_file_artifact,
    load_image_artifact,
)
from kdcube_ai_app.apps.chat.sdk.integrations.named_service_consent import (
    ACCOUNT_SELECTION_CONTRACT,
    CONSENT_ERROR_CONTRACT,
    account_credential_status,
    consent_error_response,
    resolution_consent_payload,
    tool_error_response,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connection_edges import (
    DEFAULT_CONNECTION_HUB_BUNDLE_ID,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connector_app_resolution import (
    resolve_connector_app_id,
)
from connection_hub.delegated_to_kdcube.models import (
    REASON_CONNECT_REQUIRED,
    ClaimResolution,
    ConnectedAccount,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceProvider,
    NamedServiceProviderSpec,
    NamedServiceRequest,
    NamedServiceResponse,
    named_service_provider,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    OBJECT_ACTION,
    OBJECT_GET,
    OBJECT_LIST,
    OBJECT_SCHEMA,
    PROVIDER_ABOUT,
    PROVIDER_CAPABILITIES,
    TRANSPORT_API,
    TRANSPORT_LOCAL,
)

LOGGER = logging.getLogger(__name__)

LINKEDIN_NAMESPACE = "linkedin"
PROVIDER_ID = "sdk.integrations.linkedin"
LINKEDIN_ACCOUNT_KIND = "linkedin.account"
LINKEDIN_POST_KIND = "linkedin.post"
LINKEDIN_TRANSPORTS = (TRANSPORT_LOCAL, TRANSPORT_API)

# LinkedIn covers text and media with one scope (w_member_social). Text and
# media publishing are separate actions here, so each carries its own grant.
ACTION_PUBLISH_POST = "publish_post"
ACTION_PUBLISH_IMAGE_POST = "publish_image_post"
ACTION_PUBLISH_ARTICLE_POST = "publish_article_post"
ACTION_PUBLISH_POLL = "publish_poll"
ACTION_PUBLISH_DOCUMENT_POST = "publish_document_post"
ACTION_PUBLISH_VIDEO_POST = "publish_video_post"
ACTION_ADD_COMMENT = "add_comment"
# Mutations on already-published content. LinkedIn's w_member_social scope
# covers create, modify, and delete of posts, comments, and reactions.
ACTION_UPDATE_POST_TEXT = "update_post_text"
ACTION_DELETE_POST = "delete_post"
ACTION_DELETE_COMMENT = "delete_comment"
ACTION_REQUEST_UPLOAD = "request_upload"
ACTION_DISCARD_UPLOAD = "discard_upload"
# Organization lane (Community Management API connector): post as the page,
# and the only reads this namespace has anywhere — the page's own posts,
# comments, and reaction counts. Member content stays write-only.
ACTION_PUBLISH_ORG_POST = "publish_org_post"
ACTION_LIST_ORG_POSTS = "list_org_posts"
ACTION_READ_POST_ENGAGEMENT = "read_post_engagement"

LINKEDIN_ACTIONS = (
    ACTION_PUBLISH_POST,
    ACTION_PUBLISH_IMAGE_POST,
    ACTION_PUBLISH_ARTICLE_POST,
    ACTION_PUBLISH_POLL,
    ACTION_PUBLISH_DOCUMENT_POST,
    ACTION_PUBLISH_VIDEO_POST,
    ACTION_ADD_COMMENT,
    ACTION_UPDATE_POST_TEXT,
    ACTION_DELETE_POST,
    ACTION_DELETE_COMMENT,
    ACTION_REQUEST_UPLOAD,
    ACTION_DISCARD_UPLOAD,
    ACTION_PUBLISH_ORG_POST,
    ACTION_LIST_ORG_POSTS,
    ACTION_READ_POST_ENGAGEMENT,
)

LINKEDIN_MEMBER_ACTIONS = (
    ACTION_PUBLISH_POST,
    ACTION_PUBLISH_IMAGE_POST,
    ACTION_PUBLISH_ARTICLE_POST,
    ACTION_PUBLISH_POLL,
    ACTION_PUBLISH_DOCUMENT_POST,
    ACTION_PUBLISH_VIDEO_POST,
    ACTION_ADD_COMMENT,
    ACTION_UPDATE_POST_TEXT,
    ACTION_DELETE_POST,
    ACTION_DELETE_COMMENT,
    ACTION_REQUEST_UPLOAD,
    ACTION_DISCARD_UPLOAD,
)

LINKEDIN_GRANT_HINTS = {
    # object.list and object.get read KDCube connection records or parse the
    # ref; neither calls LinkedIn.
    "object.list": [],
    "object.get": [],
    **{f"object.action.{action}": [LINKEDIN_POST_CLAIM] for action in LINKEDIN_MEMBER_ACTIONS},
    f"object.action.{ACTION_PUBLISH_ORG_POST}": [LINKEDIN_ORG_POST_CLAIM],
    f"object.action.{ACTION_LIST_ORG_POSTS}": [LINKEDIN_ORG_READ_CLAIM],
    f"object.action.{ACTION_READ_POST_ENGAGEMENT}": [LINKEDIN_ORG_READ_CLAIM],
}

LINKEDIN_CONNECTED_ACCOUNT_CLAIMS = {
    "profile": LINKEDIN_PROFILE_CLAIM,
    "post": LINKEDIN_POST_CLAIM,
    "org_post": LINKEDIN_ORG_POST_CLAIM,
    "org_read": LINKEDIN_ORG_READ_CLAIM,
}

LINKEDIN_CONNECTED_ACCOUNT_REQUIREMENTS = [
    {
        "provider_id": LINKEDIN_PROVIDER_ID,
        "provider_label": "LinkedIn",
        "claims": [LINKEDIN_POST_CLAIM],
        "claim_labels": {
            LINKEDIN_PROFILE_CLAIM: "read your profile",
            LINKEDIN_POST_CLAIM: "post and comment as you",
            LINKEDIN_ORG_POST_CLAIM: "post as your organization page",
            LINKEDIN_ORG_READ_CLAIM: "read your organization page's posts and engagement",
        },
        "claims_by_operation": {
            f"object.action.{ACTION_PUBLISH_POST}": [LINKEDIN_POST_CLAIM],
            f"object.action.{ACTION_PUBLISH_IMAGE_POST}": [LINKEDIN_POST_CLAIM],
            f"object.action.{ACTION_PUBLISH_ARTICLE_POST}": [LINKEDIN_POST_CLAIM],
            f"object.action.{ACTION_PUBLISH_POLL}": [LINKEDIN_POST_CLAIM],
            f"object.action.{ACTION_PUBLISH_DOCUMENT_POST}": [LINKEDIN_POST_CLAIM],
            f"object.action.{ACTION_PUBLISH_VIDEO_POST}": [LINKEDIN_POST_CLAIM],
            f"object.action.{ACTION_ADD_COMMENT}": [LINKEDIN_POST_CLAIM],
            f"object.action.{ACTION_UPDATE_POST_TEXT}": [LINKEDIN_POST_CLAIM],
            f"object.action.{ACTION_DELETE_POST}": [LINKEDIN_POST_CLAIM],
            f"object.action.{ACTION_DELETE_COMMENT}": [LINKEDIN_POST_CLAIM],
            f"object.action.{ACTION_PUBLISH_ORG_POST}": [LINKEDIN_ORG_POST_CLAIM],
            f"object.action.{ACTION_LIST_ORG_POSTS}": [LINKEDIN_ORG_READ_CLAIM],
            f"object.action.{ACTION_READ_POST_ENGAGEMENT}": [LINKEDIN_ORG_READ_CLAIM],
        },
    }
]

# Copied into every projected schema view by _DEFAULT_GLOBAL_SECTIONS.
LINKEDIN_ACCOUNT_SELECTION = {
    "refs": (
        "Every linkedin ref embeds its account id. Call object.list and pass the "
        "chosen ref; named_services_action and the ReAct action tool require one, "
        "the generic named_services_call does not."
    ),
    "action": ACCOUNT_SELECTION_CONTRACT["action"],
    "discovery": (
        "object.list returns {account_id, display_name, email, status, claims, "
        "author_urn}."
    ),
    "no_search": (
        "There is no object.search here — LinkedIn exposes no feed, post-content "
        "or people search to this integration."
    ),
    "no_account": (
        "No connected account fails with connect_required and carries the "
        "Connection Hub link; LinkedIn is not called."
    ),
    "unbound_account": (
        "Naming an account this caller is not bound to fails with "
        "agent_account_binding_required, even when the account itself holds the "
        "claim. The fix is the caller's own grant card, not the connection."
    ),
}

LINKEDIN_INTRO = (
    "Use namespace `linkedin` to publish on behalf of a user-connected LinkedIn "
    "member. Start with object.list to see connected accounts, then object.action "
    "publish_post for a text post or publish_image_post for a post with images, "
    "and add_comment on a post ref either returns. LinkedIn does not expose feed, "
    "message, or post-content reads to this integration, so there is no search "
    "and object.get returns identity and link metadata only."
)

LINKEDIN_PRESENTATION = {
    "about": "Publish posts and comments on the LinkedIn account you connect.",
    "third_party": "Works with your LinkedIn account through your connected LinkedIn profile.",
    "operations": {
        "provider.about": {"label": "Service overview", "description": "What this LinkedIn service does and how to use it."},
        "provider.capabilities": {"label": "Capabilities", "description": "The operations and behaviors this service declares."},
        "object.list": {"label": "List accounts", "description": "List your connected LinkedIn accounts."},
        "object.get": {"label": "Inspect a ref", "description": "Read a connected account record or a published post's link."},
        "object.schema": {"label": "Object reference", "description": "The shapes and refs of this service's objects."},
    },
    "actions": {
        ACTION_PUBLISH_POST: {"label": "Publish a post", "description": "Publish a text post to your LinkedIn feed."},
        ACTION_PUBLISH_IMAGE_POST: {"label": "Publish a post with images", "description": "Publish a post with one or more images to your LinkedIn feed."},
        ACTION_PUBLISH_ARTICLE_POST: {"label": "Publish a post with a link card", "description": "Publish a post carrying a link-preview card, with an optional thumbnail image."},
        ACTION_PUBLISH_POLL: {"label": "Publish a poll", "description": "Publish a poll post: a question with 2-4 options open for a fixed duration."},
        ACTION_PUBLISH_DOCUMENT_POST: {"label": "Publish a document post", "description": "Publish a post carrying a PDF/DOC/PPT document."},
        ACTION_PUBLISH_VIDEO_POST: {"label": "Publish a video post", "description": "Publish a post carrying an MP4 video."},
        ACTION_ADD_COMMENT: {"label": "Comment on a post", "description": "Add a comment to a LinkedIn post as you."},
        ACTION_UPDATE_POST_TEXT: {"label": "Edit a post's text", "description": "Replace the text of a post you published; the attached card or media stay as they are."},
        ACTION_DELETE_POST: {"label": "Delete a post", "description": "Permanently delete a post you published, with its comments and reactions."},
        ACTION_DELETE_COMMENT: {"label": "Delete a comment", "description": "Permanently delete a comment you added to a post."},
        ACTION_REQUEST_UPLOAD: {"label": "Attach an image", "description": "Stage one image for an upcoming post."},
        ACTION_DISCARD_UPLOAD: {"label": "Discard staged image", "description": "Remove a staged image before it is used."},
        ACTION_PUBLISH_ORG_POST: {"label": "Publish as your organization", "description": "Publish a text post as the organization page you administer."},
        ACTION_LIST_ORG_POSTS: {"label": "List organization posts", "description": "List posts authored by your organization page."},
        ACTION_READ_POST_ENGAGEMENT: {"label": "Read post engagement", "description": "Read comment and reaction counts, and comments, of an organization post."},
    },
}

LINKEDIN_SCHEMA = {
    "namespace": LINKEDIN_NAMESPACE,
    "refs": {
        "account": "linkedin:<account_id>",
        "post": "linkedin:<account_id>:post:<post_urn>",
    },
    "object_kinds": {
        LINKEDIN_ACCOUNT_KIND: {
            "description": "A LinkedIn account the current user connected in Connection Hub.",
            "fields": ["account_id", "display_name", "email", "status", "claims", "author_urn"],
            "source": "KDCube connection record; no LinkedIn call.",
        },
        LINKEDIN_POST_KIND: {
            "description": "A post published through this namespace.",
            "fields": ["post_urn", "permalink", "account_id", "author_urn"],
            "object_get": (
                "Checks the ref's account against the connection records and "
                "derives the permalink from the urn; it returns urn_verified "
                "false because LinkedIn exposes no read that could confirm the "
                "post exists."
            ),
            "post_urn_types": (
                "LinkedIn returns urn:li:share:<id> for a post with no media or "
                "one image, and urn:li:ugcPost:<id> for a multi-image post. Both "
                "are carried verbatim in the ref; do not match on either form."
            ),
            "source": (
                "Derived from the ref. Post content cannot be read back: LinkedIn "
                "gates that behind r_member_social, restricted to approved apps."
            ),
        },
    },
    "actions": {
        ACTION_PUBLISH_POST: {
            # The pointer to the image action lives in the rejection message,
            # not here: this text is indexed for capability search.
            "description": "Publish a post with no media. Post text is required.",
            "object_ref": (
                "linkedin:<account_id> selecting the publishing account; take it "
                "from object.list. Required by named_services_action."
            ),
            "payload": {
                "text": {"type": "string", "required": True, "description": "Post body. Markdown is stripped; max 3000 characters."},
                "visibility": {"type": "string", "enum": ["PUBLIC", "CONNECTIONS"], "default": "PUBLIC"},
                "account_id": {"type": "string", "description": "Connected account id when several are connected."},
            },
            "returns": "post_urn, permalink, account_id, author_urn, image_count",
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_PUBLISH_IMAGE_POST: {
            "description": (
                "Publish a post whose media are the given images. Post text is "
                "required too: a LinkedIn post always carries commentary. One "
                "image is attached inline, several become a multi-image post."
            ),
            "object_ref": (
                "linkedin:<account_id> selecting the publishing account; take it "
                "from object.list. Required by named_services_action."
            ),
            "payload": {
                "text": {"type": "string", "required": True, "description": "Post body. Markdown is stripped; max 3000 characters."},
                "visibility": {"type": "string", "enum": ["PUBLIC", "CONNECTIONS"], "default": "PUBLIC"},
                "account_id": {"type": "string", "description": "Connected account id when several are connected."},
                "files": {
                    "type": "array",
                    "required": True,
                    "description": (
                        "Images as [{staged_ref}]: call request_upload, POST the bytes "
                        "to upload_url, pass the ref back — bytes never enter model "
                        "context. [{filename, content_base64, mime}] is a small inline "
                        "fallback. JPEG, PNG or GIF; at most 20."
                    ),
                },
                "alt_texts": {"type": "array", "description": "Alt text per image, positional."},
            },
            "returns": "post_urn, permalink, account_id, author_urn, image_count",
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_PUBLISH_ARTICLE_POST: {
            "description": (
                "Publish a post carrying a LINK-PREVIEW CARD (source URL + title, "
                "optional description and thumbnail image). A post holds a card OR "
                "images, never both — with a card, files[] may carry at most one "
                "image, which becomes the card's thumbnail."
            ),
            "object_ref": (
                "linkedin:<account_id> selecting the publishing account; take it "
                "from object.list. Required by named_services_action."
            ),
            "payload": {
                "text": {"type": "string", "required": True, "description": "Post body. Markdown is stripped; max 3000 characters (UTF-16 units — emoji cost 2)."},
                "article": {
                    "type": "object",
                    "required": True,
                    "description": (
                        "The card: {source (URL, required), title (required, <=400), "
                        "description (<=4086), thumbnail_alt (<=4086)}. The thumbnail "
                        "itself travels in files[]."
                    ),
                },
                "files": {
                    "type": "array",
                    "description": (
                        "At most ONE image — the card thumbnail — as [{staged_ref}] "
                        "or the inline/workspace forms publish_image_post takes."
                    ),
                },
                "visibility": {"type": "string", "enum": ["PUBLIC", "CONNECTIONS"], "default": "PUBLIC"},
                "account_id": {"type": "string", "description": "Connected account id when several are connected."},
            },
            "returns": "post_urn, permalink, account_id, author_urn, image_urns",
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_PUBLISH_POLL: {
            "description": (
                "Publish a poll post: a question with 2-4 options, open for a "
                "fixed duration. Non-sponsored only; results are visible to the "
                "author on LinkedIn."
            ),
            "object_ref": "linkedin:<account_id> selecting the publishing account; take it from object.list.",
            "payload": {
                "text": {"type": "string", "required": True, "description": "Post body. Max 3000 characters (UTF-16 units)."},
                "question": {"type": "string", "required": True, "description": "Poll question; max 140 characters."},
                "options": {"type": "array", "required": True, "description": "2-4 option strings, each max 30 characters."},
                "duration": {"type": "string", "enum": ["ONE_DAY", "THREE_DAYS", "SEVEN_DAYS", "FOURTEEN_DAYS"], "default": "SEVEN_DAYS"},
                "visibility": {"type": "string", "enum": ["PUBLIC", "CONNECTIONS"], "default": "PUBLIC"},
                "account_id": {"type": "string", "description": "Connected account id when several are connected."},
            },
            "returns": "post_urn, permalink, account_id, author, question, option_count",
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_PUBLISH_DOCUMENT_POST: {
            "description": (
                "Publish a post carrying ONE document (PDF, DOC/DOCX, PPT/PPTX; "
                "up to 100 MB). LinkedIn renders it as a swipeable document. A "
                "title is required."
            ),
            "object_ref": "linkedin:<account_id> selecting the publishing account; take it from object.list.",
            "payload": {
                "text": {"type": "string", "required": True, "description": "Post body. Max 3000 characters (UTF-16 units)."},
                "title": {"type": "string", "required": True, "description": "Document title shown on the post."},
                "files": {"type": "array", "required": True, "description": "Exactly ONE document, in the same forms publish_image_post takes ([{staged_ref}] preferred)."},
                "visibility": {"type": "string", "enum": ["PUBLIC", "CONNECTIONS"], "default": "PUBLIC"},
                "account_id": {"type": "string", "description": "Connected account id when several are connected."},
            },
            "returns": "post_urn, permalink, account_id, author, document_urn, title",
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_PUBLISH_VIDEO_POST: {
            "description": (
                "Publish a post carrying ONE MP4 video (75 KB - 500 MB, 3 s - 30 "
                "min). The upload is multipart; LinkedIn processes the video "
                "asynchronously and publishes the post when processing completes."
            ),
            "object_ref": "linkedin:<account_id> selecting the publishing account; take it from object.list.",
            "payload": {
                "text": {"type": "string", "required": True, "description": "Post body. Max 3000 characters (UTF-16 units)."},
                "title": {"type": "string", "description": "Optional video title."},
                "files": {"type": "array", "required": True, "description": "Exactly ONE MP4, in the same forms publish_image_post takes ([{staged_ref}] preferred)."},
                "visibility": {"type": "string", "enum": ["PUBLIC", "CONNECTIONS"], "default": "PUBLIC"},
                "account_id": {"type": "string", "description": "Connected account id when several are connected."},
            },
            "returns": "post_urn, permalink, account_id, author, video_urn, processing",
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_REQUEST_UPLOAD: {
            "description": (
                "Reserve an upload slot for one image. Returns {upload_url, "
                "staged_ref, expires_at, max_bytes}: POST the raw bytes to "
                f"upload_url, then pass staged_ref in {ACTION_PUBLISH_IMAGE_POST} "
                "files[]. The slot is signed and needs no Authorization header."
            ),
            "object_ref": "none",
            "payload": {
                "filename": {"type": "string", "required": True, "description": "Image filename; the extension sets the mime type when mime is absent."},
                "mime": {"type": "string", "description": "image/jpeg, image/png or image/gif."},
            },
            "returns": "upload_url, staged_ref, expires_at, max_bytes, how",
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_DISCARD_UPLOAD: {
            "description": (
                "Remove one staged image before it is used. Staged bytes are "
                "single-use and are removed automatically once a post consumes them."
            ),
            "object_ref": "none",
            "payload": {
                "staged_ref": {"type": "string", "required": True, "description": "Ref returned by request_upload."},
            },
            "returns": "staged_ref, removed",
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_ADD_COMMENT: {
            "description": (
                "Add a comment to a LinkedIn post. Uses the unversioned "
                "/v2/socialActions endpoint; the versioned one requires "
                "Community Management partner access."
            ),
            "object_ref": "linkedin:<account_id>:post:<post_urn>, or pass post_urn in the payload.",
            "payload": {
                "text": {"type": "string", "required": True, "description": "Comment text."},
                "post_urn": {"type": "string", "description": "Target post URN when no object_ref is given."},
                "account_id": {"type": "string", "description": "Connected account id when several are connected."},
            },
            "returns": (
                "comment_id, comment_urn, post_urn, account_id. comment_urn is "
                "empty when the response carries no thread to key it on."
            ),
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_UPDATE_POST_TEXT: {
            "description": (
                "Replace the TEXT of a post published through a connected "
                "account (with as_organization, a post the organization page "
                "authored). LinkedIn permits editing a post's commentary only; "
                "the attached card or media can never change."
            ),
            "object_ref": "linkedin:<account_id>:post:<post_urn>, or pass post_urn in the payload.",
            "payload": {
                "text": {"type": "string", "required": True, "description": "New post body. Markdown is stripped; max 3000 characters (UTF-16 units)."},
                "post_urn": {"type": "string", "description": "Target post URN when no object_ref is given."},
                "as_organization": {"type": "boolean", "default": False, "description": "Edit a post authored by the org-lane account's organization page."},
                "account_id": {"type": "string", "description": "Connected account id when several are connected."},
            },
            "returns": "post_urn, updated",
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_DELETE_POST: {
            "description": (
                "Delete a post published through a connected account (with "
                "as_organization, a post the organization page authored). "
                "Deletion is PERMANENT: the post disappears with its comments "
                "and reactions. Idempotent — deleting an already-deleted post "
                "still succeeds."
            ),
            "object_ref": "linkedin:<account_id>:post:<post_urn>, or pass post_urn in the payload.",
            "payload": {
                "post_urn": {"type": "string", "description": "Target post URN when no object_ref is given."},
                "as_organization": {"type": "boolean", "default": False, "description": "Delete a post authored by the org-lane account's organization page."},
                "account_id": {"type": "string", "description": "Connected account id when several are connected."},
            },
            "returns": "post_urn, deleted",
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_DELETE_COMMENT: {
            "description": (
                "Delete a comment the connected member authored on a LinkedIn "
                "post. Deletion is permanent. Uses the unversioned "
                "/v2/socialActions endpoint, like add_comment."
            ),
            "object_ref": "linkedin:<account_id>:post:<post_urn> — the comment's post — or pass post_urn in the payload.",
            "payload": {
                "comment_id": {"type": "string", "required": True, "description": "Comment id returned by add_comment (comment_id, not the full comment URN)."},
                "post_urn": {"type": "string", "description": "The comment's post URN when no object_ref is given."},
                "account_id": {"type": "string", "description": "Connected account id when several are connected."},
            },
            "returns": "post_urn, comment_id, deleted",
            "grants": [LINKEDIN_POST_CLAIM],
        },
        ACTION_PUBLISH_ORG_POST: {
            "description": (
                "Publish a text post AS THE ORGANIZATION PAGE the connected "
                "org-lane account administers. Post text is required."
            ),
            "object_ref": (
                "linkedin:<account_id> selecting the ORG-LANE account (its record "
                "carries the organization); take it from object.list."
            ),
            "payload": {
                "text": {"type": "string", "required": True, "description": "Post body. Markdown is stripped; max 3000 characters."},
                "visibility": {"type": "string", "enum": ["PUBLIC", "CONNECTIONS"], "default": "PUBLIC"},
                "account_id": {"type": "string", "description": "Connected org-lane account id when several are connected."},
            },
            "returns": "post_urn, permalink, account_id, author (urn:li:organization:*), image_count",
            "grants": [LINKEDIN_ORG_POST_CLAIM],
        },
        ACTION_LIST_ORG_POSTS: {
            "description": (
                "List posts authored by the connected org-lane account's "
                "organization page, newest first — the only post listing this "
                "namespace has anywhere. Member posts stay unlistable."
            ),
            "object_ref": "linkedin:<account_id> selecting the org-lane account, or pass account_id in the payload.",
            "payload": {
                "count": {"type": "integer", "default": 10, "description": "How many posts (1-100)."},
                "start": {"type": "integer", "default": 0, "description": "Pagination offset."},
                "account_id": {"type": "string", "description": "Connected org-lane account id when several are connected."},
            },
            "returns": "posts[{post_urn, permalink, commentary, published_at, ...}], count, author",
            "grants": [LINKEDIN_ORG_READ_CLAIM],
        },
        ACTION_READ_POST_ENGAGEMENT: {
            "description": (
                "Read comment/reaction counts — and optionally the comments — of "
                "one post authored by the org-lane account's organization page."
            ),
            "object_ref": "linkedin:<account_id>:post:<post_urn>, or pass post_urn in the payload.",
            "payload": {
                "post_urn": {"type": "string", "description": "Target post URN when no object_ref is given."},
                "include_comments": {"type": "boolean", "default": True},
                "comments_count": {"type": "integer", "default": 25, "description": "How many comments when included (1-100)."},
                "account_id": {"type": "string", "description": "Connected org-lane account id when several are connected."},
            },
            "returns": "post_urn, like_count, comment_count, comments[{comment_urn, actor, text, created_at}]",
            "grants": [LINKEDIN_ORG_READ_CLAIM],
        },
    },
    "limits": {
        "post_text_chars": rest_api.LINKEDIN_POST_MAX_CHARS,
        "images_per_post": rest_api.MULTI_IMAGE_MAX,
        # Strictly fewer pixels (width x height) than this; no byte limit.
        "image_max_pixels_exclusive": rest_api.MAX_IMAGE_PIXELS,
        "gif_max_frames": rest_api.MAX_GIF_FRAMES,
        "image_mime": list(rest_api.SUPPORTED_IMAGE_MIME),
    },
    # LinkedIn's post content universe (Posts API `content` members) and what
    # this namespace covers. A post renders ONE content shape; the doc implies
    # rather than states exclusivity, so the guard here refuses combinations
    # with an actionable error instead of letting LinkedIn fail opaquely.
    "post_formats": {
        "text": f"wired — {ACTION_PUBLISH_POST} (a post with commentary and no content block).",
        "image": f"wired — {ACTION_PUBLISH_IMAGE_POST} with one file (content.media).",
        "multiImage": f"wired — {ACTION_PUBLISH_IMAGE_POST} with 2-{rest_api.MULTI_IMAGE_MAX} files (non-sponsored only).",
        "article": f"wired — {ACTION_PUBLISH_ARTICLE_POST} (link-preview card, optional thumbnail).",
        "video": f"wired — {ACTION_PUBLISH_VIDEO_POST} (MP4, multipart Videos API upload; LinkedIn processes asynchronously).",
        "document": f"wired — {ACTION_PUBLISH_DOCUMENT_POST} (PDF/DOC/PPT via the Documents API; title required).",
        "poll": f"wired — {ACTION_PUBLISH_POLL} (question + 2-4 options; non-sponsored only).",
        "carousel": "out of scope — LinkedIn allows carousel posts only as sponsored content.",
        "celebration": "not creatable — LinkedIn does not allow creating celebration posts through the external API.",
        "reference": "not wired — event/appreciation reference posts.",
    },
    "not_supported": {
        "object.search": "LinkedIn exposes no content search to this integration.",
        "post_content_read": (
            "Reading MEMBER post bodies, feeds or reactions requires "
            "r_member_social (approved apps only). Organization-page posts and "
            f"engagement ARE readable — through {ACTION_LIST_ORG_POSTS} and "
            f"{ACTION_READ_POST_ENGAGEMENT} on an org-lane account."
        ),
        "other_members": "Only the connected member's own account is reachable.",
    },
    "account_selection": LINKEDIN_ACCOUNT_SELECTION,
    "connected_account_claims": LINKEDIN_CONNECTED_ACCOUNT_CLAIMS,
    "grant_hints": LINKEDIN_GRANT_HINTS,
    "consent_errors": CONSENT_ERROR_CONTRACT,
}

# In-chat action contracts: images travel by workspace path and the service
# reads the bytes. staged_ref and content_base64 stay in the turn-less (MCP)
# contract, where callers hold the bytes themselves.
_LINKEDIN_FILES_IN_CHAT = (
    "Images as [{file_path}]: the logical (conv:fi:...) or physical path a "
    "pull/exec returned — the service reads the bytes itself. [{staged_ref}] "
    "carries an image staged earlier via request_upload. JPEG, PNG or GIF; at "
    "most 20."
)


def linkedin_schema_for_surface() -> dict[str, Any]:
    """LINKEDIN_SCHEMA with the image contract phrased for the calling surface.

    Inside a chat turn the images description teaches the workspace-path form.
    On turn-less transports the base schema applies unchanged.
    """
    try:
        from kdcube_ai_app.apps.chat.sdk.integrations.inline_files import has_turn_workspace

        in_chat = has_turn_workspace()
    except Exception:
        in_chat = False
    if not in_chat:
        return dict(LINKEDIN_SCHEMA)
    schema = dict(LINKEDIN_SCHEMA)
    actions = {name: dict(spec) for name, spec in schema["actions"].items()}
    image_post = dict(actions[ACTION_PUBLISH_IMAGE_POST])
    payload = {key: dict(value) for key, value in image_post["payload"].items()}
    payload["files"] = {**payload["files"], "description": _LINKEDIN_FILES_IN_CHAT}
    image_post["payload"] = payload
    actions[ACTION_PUBLISH_IMAGE_POST] = image_post
    schema["actions"] = actions
    return schema


LINKEDIN_SCHEMA_PROJECTION = {
    "catalog": {
        "id": "linkedin",
        "label": "LinkedIn",
        "description": "Publish posts and comments as a connected LinkedIn member.",
        # Keywords are matched with token.startswith(query_term). "images"
        # therefore covers "image", but "galleries" does not cover "gallery" —
        # when the plural is not a bare +s, both forms are declared. Keywords
        # only reach search from the node that owns the operations.
        "children": [
            {
                "id": "accounts",
                "label": "Connected accounts",
                "object_kind": LINKEDIN_ACCOUNT_KIND,
                "keywords": ["accounts", "profiles", "authors", "member", "members", "who"],
                "children": [
                    {
                        "id": "list",
                        "label": "List connected accounts",
                        "description": "Accounts this user connected in Connection Hub, with their author URNs.",
                        "keywords": ["accounts", "connected", "available", "which", "choose", "listing",
                                     "listings", "profiles", "authors", "member", "members", "who"],
                        "operations": ["object.list"],
                    },
                    {
                        "id": "inspect",
                        "label": "Inspect one account",
                        "description": "Metadata and claim status of a single account. No credentials are returned.",
                        "keywords": ["metadata", "status", "statuses", "claims", "details", "single"],
                        "operations": ["object.get"],
                    },
                ],
            },
            {
                "id": "publishing",
                "label": "Publishing",
                "object_kind": LINKEDIN_ACCOUNT_KIND,
                "children": [
                    {
                        "id": "text",
                        "label": "Publish a text post",
                        "description": "Publish a post with no media to the member's feed.",
                        "keywords": ["posts", "publishing", "sharing", "updates", "text", "texts", "feed", "feeds", "commentary", "commentaries"],
                        "operations": [f"object.action:{ACTION_PUBLISH_POST}"],
                    },
                    {
                        "id": "images",
                        "label": "Publish a post with images",
                        "description": "Publish a post carrying one image or a multi-image gallery.",
                        "keywords": ["images", "photos", "pictures", "media", "gallery", "galleries", "attachments", "illustrated"],
                        "operations": [f"object.action:{ACTION_PUBLISH_IMAGE_POST}"],
                    },
                    {
                        "id": "link",
                        "label": "Publish a post with a link card",
                        "description": "Publish a post carrying a link-preview card (URL + title, optional description and thumbnail).",
                        "keywords": ["links", "cards", "articles", "previews", "thumbnails", "url", "urls"],
                        "operations": [f"object.action:{ACTION_PUBLISH_ARTICLE_POST}"],
                    },
                    {
                        "id": "poll",
                        "label": "Publish a poll",
                        "description": "A question with 2-4 options, open for a fixed duration.",
                        "keywords": ["polls", "questions", "votes", "voting", "surveys", "options"],
                        "operations": [f"object.action:{ACTION_PUBLISH_POLL}"],
                    },
                    {
                        "id": "document",
                        "label": "Publish a document post",
                        "description": "A post carrying a PDF/DOC/PPT rendered as a swipeable document.",
                        "keywords": ["documents", "pdf", "pdfs", "slides", "decks", "presentations", "carousels"],
                        "operations": [f"object.action:{ACTION_PUBLISH_DOCUMENT_POST}"],
                    },
                    {
                        "id": "video",
                        "label": "Publish a video post",
                        "description": "A post carrying an MP4 video, processed asynchronously by LinkedIn.",
                        "keywords": ["videos", "mp4", "clips", "recordings", "reels"],
                        "operations": [f"object.action:{ACTION_PUBLISH_VIDEO_POST}"],
                    },
                    {
                        "id": "staging",
                        "label": "Stage images for a post",
                        "description": "Reserve and release signed upload slots so image bytes bypass model context.",
                        "keywords": ["uploads", "attaching", "staged", "staging", "slots"],
                        "operations": [
                            f"object.action:{ACTION_REQUEST_UPLOAD}",
                            f"object.action:{ACTION_DISCARD_UPLOAD}",
                        ],
                    },
                ],
            },
            {
                "id": "engagement",
                "label": "Engagement",
                "object_kind": LINKEDIN_POST_KIND,
                "children": [
                    {
                        "id": "posts",
                        "label": "Inspect a published post",
                        "description": "Permalink and identity of a post this namespace published. Post content is not readable.",
                        "keywords": ["permalink", "permalinks", "links", "published"],
                        "operations": ["object.get"],
                    },
                    {
                        "id": "comment",
                        "label": "Comment on a post",
                        "description": "Add a comment to a LinkedIn post as the connected member.",
                        "keywords": ["comments", "commenting", "reply", "replies", "responding"],
                        "operations": [f"object.action:{ACTION_ADD_COMMENT}"],
                    },
                    {
                        "id": "edit-text",
                        "label": "Edit a post's text",
                        "description": "Replace the text of a published post; the attached card or media never change.",
                        "keywords": ["editing", "edits", "updating", "rewriting", "rewording", "revising",
                                     "corrections", "correcting", "typos", "fixing"],
                        "operations": [f"object.action:{ACTION_UPDATE_POST_TEXT}"],
                    },
                    {
                        "id": "delete",
                        "label": "Delete a post",
                        "description": "Permanently delete a published post, with its comments and reactions.",
                        "keywords": ["deleting", "deletes", "deletion", "deletions", "removing", "removal",
                                     "removals", "retracting", "unpublishing", "takedowns"],
                        "operations": [f"object.action:{ACTION_DELETE_POST}"],
                    },
                    {
                        "id": "delete-comment",
                        "label": "Delete a comment",
                        "description": "Permanently delete a comment the connected member added to a post.",
                        "keywords": ["deleting", "removing", "retracting", "moderating", "moderation"],
                        "operations": [f"object.action:{ACTION_DELETE_COMMENT}"],
                    },
                ],
            },
            {
                "id": "organization",
                "label": "Organization page",
                "object_kind": LINKEDIN_ACCOUNT_KIND,
                "keywords": ["organization", "organizations", "org", "orgs", "company", "companies", "page", "pages", "brand", "brands"],
                "children": [
                    {
                        "id": "publish",
                        "label": "Publish as the organization",
                        "description": "Publish a text post as the organization page the org-lane account administers.",
                        "keywords": ["posts", "publishing", "sharing", "updates", "organization", "organizations",
                                     "org", "orgs", "company", "companies", "page", "pages", "brand", "brands"],
                        "operations": [f"object.action:{ACTION_PUBLISH_ORG_POST}"],
                    },
                    {
                        "id": "posts",
                        "label": "List organization posts",
                        "description": "Posts authored by the organization page, newest first — the namespace's only post listing.",
                        "keywords": ["listing", "listings", "history", "published", "reading", "reads",
                                     "organization", "organizations", "org", "orgs", "company", "companies",
                                     "page", "pages", "brand", "brands"],
                        "operations": [f"object.action:{ACTION_LIST_ORG_POSTS}"],
                    },
                    {
                        "id": "engagement",
                        "label": "Read post engagement",
                        "description": "Comment and reaction counts, and comments, of one organization post.",
                        # The target is a POST ref; this overrides the group's
                        # account kind so the operation resolves on its owner.
                        "object_kind": LINKEDIN_POST_KIND,
                        "keywords": ["engagement", "reactions", "likes", "counts", "metrics", "analytics"],
                        "operations": [f"object.action:{ACTION_READ_POST_ENGAGEMENT}"],
                    },
                ],
            },
        ],
    },
    "kinds": {
        LINKEDIN_ACCOUNT_KIND: {
            "refs": ["account"],
            "related_kinds": [LINKEDIN_POST_KIND],
            "operations": {
                "object.list": {},
                "object.get": {},
            },
            "actions": [
                ACTION_PUBLISH_POST,
                ACTION_PUBLISH_IMAGE_POST,
                ACTION_PUBLISH_ARTICLE_POST,
                ACTION_PUBLISH_POLL,
                ACTION_PUBLISH_DOCUMENT_POST,
                ACTION_PUBLISH_VIDEO_POST,
                ACTION_REQUEST_UPLOAD,
                ACTION_DISCARD_UPLOAD,
                ACTION_PUBLISH_ORG_POST,
                ACTION_LIST_ORG_POSTS,
            ],
        },
        LINKEDIN_POST_KIND: {
            "refs": ["post"],
            "related_kinds": [LINKEDIN_ACCOUNT_KIND],
            "operations": {"object.get": {}},
            "actions": [
                ACTION_ADD_COMMENT,
                ACTION_UPDATE_POST_TEXT,
                ACTION_DELETE_POST,
                ACTION_DELETE_COMMENT,
                ACTION_READ_POST_ENGAGEMENT,
            ],
        },
    },
}


def _operations() -> dict[str, Any]:
    return {
        PROVIDER_ABOUT: {"transports": LINKEDIN_TRANSPORTS},
        PROVIDER_CAPABILITIES: {"transports": LINKEDIN_TRANSPORTS},
        OBJECT_LIST: {"transports": LINKEDIN_TRANSPORTS},
        OBJECT_GET: {"transports": LINKEDIN_TRANSPORTS},
        OBJECT_SCHEMA: {"transports": LINKEDIN_TRANSPORTS},
        OBJECT_ACTION: {"transports": LINKEDIN_TRANSPORTS},
    }


def linkedin_named_service_spec(*, bundle_id: str | None = None) -> NamedServiceProviderSpec:
    return NamedServiceProviderSpec(
        provider_id=PROVIDER_ID,
        bundle_id=bundle_id,
        namespace=LINKEDIN_NAMESPACE,
        refs=("linkedin:*",),
        object_kinds=(LINKEDIN_ACCOUNT_KIND, LINKEDIN_POST_KIND),
        operations=_operations(),
        label="LinkedIn",
        description="LinkedIn namespace over user-connected LinkedIn accounts.",
        intro=LINKEDIN_INTRO,
        metadata={
            "grant_hints": LINKEDIN_GRANT_HINTS,
            "connected_account_claims": LINKEDIN_CONNECTED_ACCOUNT_CLAIMS,
            "connected_accounts": LINKEDIN_CONNECTED_ACCOUNT_REQUIREMENTS,
            "actions": {
                name: str((meta or {}).get("description") or "").strip()
                for name, meta in (LINKEDIN_SCHEMA.get("actions") or {}).items()
            },
            "presentation": LINKEDIN_PRESENTATION,
            "object_kinds": {
                kind: str((meta or {}).get("description") or "").strip()
                for kind, meta in (LINKEDIN_SCHEMA.get("object_kinds") or {}).items()
            },
            "canonical_refs": LINKEDIN_SCHEMA["refs"],
        },
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def account_ref(account_id: str) -> str:
    return f"{LINKEDIN_NAMESPACE}:{_text(account_id)}"


def post_ref(account_id: str, post_urn: str) -> str:
    return f"{account_ref(account_id)}:post:{_text(post_urn)}"


def parse_linkedin_ref(ref: str) -> dict[str, str]:
    """Split a linkedin ref. The post URN tail keeps its own colons."""
    parts = _text(ref).split(":")
    if len(parts) < 2 or parts[0] != LINKEDIN_NAMESPACE:
        return {}
    parsed = {"account_id": parts[1], "kind": "account"}
    if len(parts) >= 4 and parts[2] == "post":
        parsed.update({"kind": "post", "post_urn": ":".join(parts[3:])})
    return parsed


def _account_object(account: ConnectedAccount) -> dict[str, Any]:
    label = account.display_name or account.email or account.external_subject or account.account_id
    return {
        "ref": account_ref(account.account_id),
        "object_ref": account_ref(account.account_id),
        "object_kind": LINKEDIN_ACCOUNT_KIND,
        "account_id": account.account_id,
        "title": label,
        "display_name": account.display_name,
        "email": account.email,
        "status": account.status,
        "credential_status": account_credential_status(account),
        "claims": list(account.claims or []),
        "author_urn": (
            rest_api.person_urn(account.external_subject) if account.external_subject else ""
        ),
    }


def _post_object(account_id: str, post_urn: str) -> dict[str, Any]:
    return {
        "ref": post_ref(account_id, post_urn),
        "object_ref": post_ref(account_id, post_urn),
        "object_kind": LINKEDIN_POST_KIND,
        "post_urn": post_urn,
        "permalink": rest_api.post_permalink(post_urn),
        "account_id": account_id,
        "content_available": False,
        "content_note": (
            "LinkedIn does not expose post content to this integration; "
            "r_member_social is restricted to approved applications."
        ),
    }


def _error_from_tool(
    result: Mapping[str, Any],
    *,
    request: NamedServiceRequest,
    default_code: str,
    fallback_message: str,
) -> NamedServiceResponse:
    return tool_error_response(
        result,
        request=request,
        namespace=LINKEDIN_NAMESPACE,
        provider_identity={"provider_id": PROVIDER_ID, "namespace": LINKEDIN_NAMESPACE},
        default_code=default_code,
        fallback_message=fallback_message,
    )


@named_service_provider(
    provider_id=PROVIDER_ID,
    namespace=LINKEDIN_NAMESPACE,
    refs=("linkedin:*",),
    object_kinds=(LINKEDIN_ACCOUNT_KIND, LINKEDIN_POST_KIND),
    operations=_operations(),
    label="LinkedIn",
    description="LinkedIn namespace over user-connected LinkedIn accounts.",
    intro=LINKEDIN_INTRO,
    metadata={
        "grant_hints": LINKEDIN_GRANT_HINTS,
        "connected_account_claims": LINKEDIN_CONNECTED_ACCOUNT_CLAIMS,
        "connected_accounts": LINKEDIN_CONNECTED_ACCOUNT_REQUIREMENTS,
        "presentation": LINKEDIN_PRESENTATION,
        "canonical_refs": LINKEDIN_SCHEMA["refs"],
    },
)
class LinkedInNamedServiceProvider(NamedServiceProvider):
    schema_projection_index = LINKEDIN_SCHEMA_PROJECTION

    def __init__(
        self,
        *,
        entrypoint: Any = None,
        bundle_id: str | None = None,
        connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
        staging_root_factory: Any = None,
        upload_slot_factory: Any = None,
    ) -> None:
        super().__init__(linkedin_named_service_spec(bundle_id=bundle_id))
        self._entrypoint = entrypoint
        self._connection_hub_bundle_id = connection_hub_bundle_id
        self._staging_root_factory = staging_root_factory
        self._upload_slot_factory = upload_slot_factory
        self._linkedin = LinkedInTools()
        if entrypoint is not None:
            bind_linkedin_service(entrypoint)
            bind_linkedin_integrations({"comm_context": getattr(entrypoint, "comm_context", None)})

    def _provider_identity(self) -> dict[str, Any]:
        return {"provider_id": PROVIDER_ID, "namespace": LINKEDIN_NAMESPACE}

    def schema_object_kind_from_ref(self, object_ref: str) -> str | None:
        kind = parse_linkedin_ref(object_ref).get("kind")
        return {
            "account": LINKEDIN_ACCOUNT_KIND,
            "post": LINKEDIN_POST_KIND,
        }.get(kind)

    def _staging_root(self):
        if callable(self._staging_root_factory):
            return self._staging_root_factory()
        storage = str(getattr(getattr(self._entrypoint, "settings", None), "STORAGE_PATH", "") or "")
        try:
            return staging_root(storage)
        except OSError:
            return None

    async def _accounts(self, ctx: NamedServiceContext) -> list[ConnectedAccount]:
        return await connected_linkedin_accounts(
            tenant=ctx.tenant,
            project=ctx.project,
            hub_bundle_id=self._connection_hub_bundle_id,
        )

    def _connect_hint(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> dict[str, Any]:
        payload = resolution_consent_payload(
            resolution=ClaimResolution(
                ok=False,
                provider_id=LINKEDIN_PROVIDER_ID,
                claim="",
                connector_app_id=resolve_connector_app_id(LINKEDIN_PROVIDER_ID),
                error=REASON_CONNECT_REQUIRED,
                message="Connect a LinkedIn account in Connection Hub.",
                retry_hint=True,
            ),
            ctx=ctx,
            connection_hub_bundle_id=self._connection_hub_bundle_id,
            tool_name=f"named_services.{LINKEDIN_NAMESPACE}.{request.operation}",
        )
        return dict(payload.get("consent") or {})

    async def provider_about(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            extra={
                "title": "KDCube LinkedIn",
                "description": "LinkedIn namespace over user-connected LinkedIn accounts.",
                "workflow": [
                    "Call object.list to see connected LinkedIn accounts and their author URNs.",
                    "Call object.action publish_post with text for a post with no media.",
                    "For images call request_upload, POST the bytes, then publish_image_post with the staged_ref.",
                    "Keep the returned linkedin:<account_id>:post:<post_urn> ref.",
                    "Call object.action add_comment with that ref to comment on the post.",
                    "Call object.get on a post ref for its permalink; post content is not readable.",
                ],
                "schema": linkedin_schema_for_surface(),
            },
        )

    async def provider_capabilities(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            capabilities={
                "list": True,
                "search": False,
                "get": True,
                "upsert": False,
                "delete": False,
                "actions": list(LINKEDIN_ACTIONS),
                "grant_hints": LINKEDIN_GRANT_HINTS,
                "connected_account_claims": LINKEDIN_CONNECTED_ACCOUNT_CLAIMS,
                "not_supported": LINKEDIN_SCHEMA["not_supported"],
            },
        )

    async def object_schema(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            extra={"schema": linkedin_schema_for_surface()},
        )

    async def object_list(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        accounts = await self._accounts(ctx)
        extra: dict[str, Any] = {"kind": "accounts", "count": len(accounts)}
        if not accounts:
            extra["consent"] = self._connect_hint(ctx, request)
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            items=[_account_object(account) for account in accounts],
            extra=extra,
        )

    async def object_get(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        parsed = parse_linkedin_ref(request.object_ref or "")
        if not parsed:
            return NamedServiceResponse.error_response(
                code="linkedin_invalid_ref",
                message="Expected linkedin:<account_id> or linkedin:<account_id>:post:<post_urn>.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
                object_ref=request.object_ref,
            )
        # Both ref kinds carry an account, so both are checked against the
        # connection records before anything is returned. Reads no LinkedIn API.
        accounts = await self._accounts(ctx)
        account = next((item for item in accounts if item.account_id == parsed["account_id"]), None)
        if account is None:
            return NamedServiceResponse.error_response(
                code="linkedin_account_not_found",
                message="Connected LinkedIn account was not found.",
                status=404,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
                object_ref=request.object_ref,
            )
        if parsed["kind"] == "post":
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
                object_ref=request.object_ref,
                object={
                    **_post_object(parsed["account_id"], parsed["post_urn"]),
                    # The permalink is derived from the ref. LinkedIn exposes no
                    # read that could confirm the post urn itself exists.
                    "urn_verified": False,
                },
            )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            object_ref=request.object_ref,
            object=_account_object(account),
        )

    async def object_action(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        action = _text(request.action)
        if action == ACTION_PUBLISH_POST:
            return await self._publish_post(ctx, request)
        if action == ACTION_PUBLISH_IMAGE_POST:
            return await self._publish_image_post(ctx, request)
        if action == ACTION_PUBLISH_ARTICLE_POST:
            return await self._publish_article_post(ctx, request)
        if action == ACTION_PUBLISH_POLL:
            return await self._publish_poll(ctx, request)
        if action == ACTION_PUBLISH_DOCUMENT_POST:
            return await self._publish_media_post(ctx, request, kind="document")
        if action == ACTION_PUBLISH_VIDEO_POST:
            return await self._publish_media_post(ctx, request, kind="video")
        if action == ACTION_ADD_COMMENT:
            return await self._add_comment(ctx, request)
        if action == ACTION_UPDATE_POST_TEXT:
            return await self._update_post_text(ctx, request)
        if action == ACTION_DELETE_POST:
            return await self._delete_post(ctx, request)
        if action == ACTION_DELETE_COMMENT:
            return await self._delete_comment(ctx, request)
        if action == ACTION_REQUEST_UPLOAD:
            return await self._request_upload(ctx, request)
        if action == ACTION_DISCARD_UPLOAD:
            return self._discard_upload(ctx, request)
        if action == ACTION_PUBLISH_ORG_POST:
            return await self._publish_org_post(ctx, request)
        if action == ACTION_LIST_ORG_POSTS:
            return await self._list_org_posts(ctx, request)
        if action == ACTION_READ_POST_ENGAGEMENT:
            return await self._read_post_engagement(ctx, request)
        return NamedServiceResponse.error_response(
            code="linkedin_unknown_action",
            message=f"Unknown LinkedIn action {action!r}.",
            status=400,
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            details={"actions": list(LINKEDIN_ACTIONS)},
        )

    async def _request_upload(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        payload = dict(request.payload or {})
        filename = _text(payload.get("filename"))
        if not filename:
            return NamedServiceResponse.error_response(
                code="filename_required",
                message="request_upload needs payload.filename.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
            )
        slot = None
        if self._upload_slot_factory is not None:
            try:
                slot = self._upload_slot_factory(
                    ctx, {"filename": filename, "mime": _text(payload.get("mime"))}
                )
                if hasattr(slot, "__await__"):
                    slot = await slot
            except Exception:
                LOGGER.exception("linkedin upload slot factory failed")
                slot = None
        if not isinstance(slot, Mapping) or not slot.get("upload_url"):
            return NamedServiceResponse.error_response(
                code="upload_not_configured",
                message=(
                    "This deployment has no upload path configured; use tiny inline "
                    "content_base64 instead."
                ),
                status=503,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
            )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            extra={
                "action": ACTION_REQUEST_UPLOAD,
                **dict(slot),
                "how": (
                    "POST the raw image bytes to upload_url (body = file, no form "
                    f"encoding), then pass staged_ref in {ACTION_PUBLISH_IMAGE_POST} "
                    "files[]. No Authorization header: the slot URL is signed."
                ),
            },
        )

    def _discard_upload(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        staged_ref = _text(dict(request.payload or {}).get("staged_ref"))
        if not staged_ref:
            return NamedServiceResponse.error_response(
                code="staged_ref_required",
                message="discard_upload needs payload.staged_ref.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
            )
        root = self._staging_root()
        if root is not None:
            delete_staged(root, staged_ref)
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            extra={"action": ACTION_DISCARD_UPLOAD, "staged_ref": staged_ref, "removed": True},
        )

    def _account_id_for(self, request: NamedServiceRequest) -> str:
        payload = dict(request.payload or {})
        explicit = _text(payload.get("account_id"))
        if explicit:
            return explicit
        parsed = parse_linkedin_ref(request.object_ref or "")
        return parsed.get("account_id", "")

    # Entry keys that carry image content in any of the three lanes.
    _FILE_KEYS = ("files", "file_path", "staged_ref", "content_base64", "alt_texts")

    def _resolve_image_files(
        self,
        entries: list[Any],
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
        """Resolve ``files[]`` to ``[{filename, mime_type, data}]`` in input order.

        Entry forms: ``file_path`` (chat workspace), ``staged_ref`` (signed
        upload slot), ``content_base64`` (inline fallback). Returns
        ``(files, consumed_staged_refs, error)``; ``error`` is a provider error
        mapping from the workspace lane. Raises :class:`InlineFileError` for a
        malformed staged or inline entry.
        """
        slots: list[dict[str, Any] | None] = [None] * len(entries)
        deferred: list[tuple[int, Any]] = []
        for index, entry in enumerate(entries):
            path = _text(entry.get("file_path")) if isinstance(entry, Mapping) else ""
            if not path:
                deferred.append((index, entry))
                continue
            file_obj, error = load_image_artifact(path)
            if error is not None:
                return [], [], error
            slots[index] = file_obj
        consumed: list[str] = []
        if deferred:
            # resolve_payload_file_entries normalizes but does not decode: an
            # inline entry still carries content_base64. materialize handles
            # both that and staged bytes through one decoder.
            resolved, consumed = resolve_payload_file_entries(
                [entry for _index, entry in deferred],
                staging_root=self._staging_root(),
            )
            with inline_files_workspace() as artifact_root:
                staged = materialize_inline_files(artifact_root, resolved)
                for (index, _entry), row in zip(deferred, staged):
                    slots[index] = {
                        "filename": row["filename"],
                        "mime_type": row["mime"],
                        "data": (pathlib.Path(artifact_root) / row["relpath"]).read_bytes(),
                    }
        return [slot for slot in slots if slot is not None], consumed, None

    async def _publish(
        self,
        request: NamedServiceRequest,
        *,
        action: str,
        files: list[dict[str, Any]],
        consumed: list[str],
        as_organization: bool = False,
        article: dict[str, Any] | None = None,
    ) -> NamedServiceResponse:
        payload = dict(request.payload or {})
        alt_texts = [str(item or "") for item in (payload.get("alt_texts") or [])]
        result = await self._linkedin.publish(
            text=_text(payload.get("text")),
            files=files,
            alt_texts=alt_texts,
            account_id=self._account_id_for(request),
            visibility=_text(payload.get("visibility")).upper() or "PUBLIC",
            as_organization=as_organization,
            article=article,
            where=f"named_services.{LINKEDIN_NAMESPACE}.{action}",
        )
        if not isinstance(result, Mapping) or not result.get("ok"):
            return _error_from_tool(
                result if isinstance(result, Mapping) else {},
                request=request,
                default_code="linkedin_publish_failed",
                fallback_message="LinkedIn post could not be published.",
            )
        # Staged bytes are single-use: the post owns them now.
        root = self._staging_root()
        if root is not None:
            for staged_ref in consumed:
                delete_staged(root, staged_ref)

        ret = dict(result.get("ret") or {})
        account_id = _text(ret.get("account_id"))
        post_urn = _text(ret.get("post_urn"))
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            object_ref=post_ref(account_id, post_urn) if post_urn else None,
            object={**_post_object(account_id, post_urn), **ret} if post_urn else ret,
            extra={"action": action},
        )

    async def _publish_post(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        # A text post carries no media. Anything file-shaped in the payload
        # must not vanish silently — name it and point at the image action.
        present = [key for key in self._FILE_KEYS if payload.get(key)]
        if present:
            return NamedServiceResponse.error_response(
                code="linkedin_post_carries_no_images",
                message=(
                    f"This action publishes text only; {', '.join(present)} would be "
                    f"dropped. Use the {ACTION_PUBLISH_IMAGE_POST} action, which takes "
                    "the same text plus files."
                ),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
                details={"action": ACTION_PUBLISH_IMAGE_POST, "rejected_keys": present},
            )
        return await self._publish(request, action=ACTION_PUBLISH_POST, files=[], consumed=[])

    async def _publish_image_post(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        entries = list(payload.get("files") or [])
        if not entries:
            return NamedServiceResponse.error_response(
                code="linkedin_images_required",
                message=(
                    f"This action needs files[]. Publish text without media with "
                    f"the {ACTION_PUBLISH_POST} action."
                ),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
                details={"action": ACTION_PUBLISH_POST},
            )
        try:
            files, consumed, error = self._resolve_image_files(entries)
        except InlineFileError as exc:
            return NamedServiceResponse.error_response(
                code="linkedin_invalid_files",
                message=str(exc),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
            )
        if error is not None:
            return NamedServiceResponse.error_response(
                code=str(error.get("code") or "linkedin_invalid_files"),
                message=str(error.get("message") or "Image could not be read."),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
                details=dict(error),
            )
        return await self._publish(
            request, action=ACTION_PUBLISH_IMAGE_POST, files=files, consumed=consumed
        )

    async def _publish_article_post(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        raw_article = payload.get("article")
        if not isinstance(raw_article, Mapping) or not _text(raw_article.get("source")):
            return NamedServiceResponse.error_response(
                code="article_required",
                message=(
                    "publish_article_post needs payload.article with at least "
                    "{source, title}. Publish without a link card via "
                    f"{ACTION_PUBLISH_POST} or {ACTION_PUBLISH_IMAGE_POST}."
                ),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
            )
        entries = list(payload.get("files") or [])
        if len(entries) > 1:
            return NamedServiceResponse.error_response(
                code="article_takes_one_thumbnail",
                message=(
                    "An article post carries at most one image — the card's "
                    f"thumbnail. Publish galleries via {ACTION_PUBLISH_IMAGE_POST}, "
                    "without a link card."
                ),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
                details={"action": ACTION_PUBLISH_IMAGE_POST},
            )
        files: list[dict[str, Any]] = []
        consumed: list[str] = []
        if entries:
            try:
                files, consumed, error = self._resolve_image_files(entries)
            except InlineFileError as exc:
                return NamedServiceResponse.error_response(
                    code="linkedin_invalid_files",
                    message=str(exc),
                    status=400,
                    provider=self._provider_identity(),
                    namespace=request.namespace or LINKEDIN_NAMESPACE,
                )
            if error is not None:
                return NamedServiceResponse.error_response(
                    code=str(error.get("code") or "linkedin_invalid_files"),
                    message=str(error.get("message") or "Thumbnail image could not be read."),
                    status=400,
                    provider=self._provider_identity(),
                    namespace=request.namespace or LINKEDIN_NAMESPACE,
                    details=dict(error),
                )
        article = {
            "source": _text(raw_article.get("source")),
            "title": _text(raw_article.get("title")),
            "description": _text(raw_article.get("description")),
            "thumbnail_alt": _text(raw_article.get("thumbnail_alt") or raw_article.get("thumbnailAltText")),
        }
        return await self._publish(
            request,
            action=ACTION_PUBLISH_ARTICLE_POST,
            files=files,
            consumed=consumed,
            article=article,
        )

    async def _publish_poll(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        result = await self._linkedin.publish_poll(
            text=_text(payload.get("text")),
            question=_text(payload.get("question")),
            options=[str(item or "") for item in (payload.get("options") or [])],
            duration=_text(payload.get("duration")) or "SEVEN_DAYS",
            account_id=self._account_id_for(request),
            visibility=_text(payload.get("visibility")).upper() or "PUBLIC",
        )
        return self._post_action_response(result, request, action=ACTION_PUBLISH_POLL,
                                          default_code="linkedin_poll_failed",
                                          fallback_message="LinkedIn poll could not be published.")

    async def _publish_media_post(
        self, ctx: NamedServiceContext, request: NamedServiceRequest, *, kind: str
    ) -> NamedServiceResponse:
        """document/video posts: exactly ONE file, resolved from any file lane."""
        del ctx
        payload = dict(request.payload or {})
        action = ACTION_PUBLISH_DOCUMENT_POST if kind == "document" else ACTION_PUBLISH_VIDEO_POST
        entries = list(payload.get("files") or [])
        if len(entries) != 1:
            return NamedServiceResponse.error_response(
                code=f"{kind}_takes_one_file",
                message=f"A {kind} post carries exactly one file; got {len(entries)}.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
            )
        try:
            files, consumed, error = self._resolve_media_files(entries)
        except InlineFileError as exc:
            return NamedServiceResponse.error_response(
                code="linkedin_invalid_files",
                message=str(exc),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
            )
        if error is not None:
            return NamedServiceResponse.error_response(
                code=str(error.get("code") or "linkedin_invalid_files"),
                message=str(error.get("message") or "File could not be read."),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
                details=dict(error),
            )
        common = {
            "text": _text(payload.get("text")),
            "file": files[0],
            "account_id": self._account_id_for(request),
            "visibility": _text(payload.get("visibility")).upper() or "PUBLIC",
        }
        if kind == "document":
            result = await self._linkedin.publish_document(title=_text(payload.get("title")), **common)
        else:
            result = await self._linkedin.publish_video(title=_text(payload.get("title")), **common)
        response = self._post_action_response(
            result, request, action=action,
            default_code=f"linkedin_{kind}_failed",
            fallback_message=f"LinkedIn {kind} post could not be published.",
        )
        if response.ok:
            root = self._staging_root()
            if root is not None:
                for staged_ref in consumed:
                    delete_staged(root, staged_ref)
        return response

    def _resolve_media_files(
        self, entries: list[Any]
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
        """Like _resolve_image_files but WITHOUT image typing: the file_path
        lane loads any workspace file; per-shape validation happens in the
        tools layer (document/video limits differ from images)."""
        slots: list[dict[str, Any] | None] = [None] * len(entries)
        deferred: list[tuple[int, Any]] = []
        for index, entry in enumerate(entries):
            path = _text(entry.get("file_path")) if isinstance(entry, Mapping) else ""
            if not path:
                deferred.append((index, entry))
                continue
            file_obj, error = load_file_artifact(path)
            if error is not None:
                return [], [], error
            slots[index] = file_obj
        consumed: list[str] = []
        if deferred:
            resolved, consumed = resolve_payload_file_entries(
                [entry for _index, entry in deferred],
                staging_root=self._staging_root(),
            )
            with inline_files_workspace() as artifact_root:
                staged = materialize_inline_files(artifact_root, resolved)
                for (index, _entry), row in zip(deferred, staged):
                    slots[index] = {
                        "filename": row["filename"],
                        "mime_type": row["mime"],
                        "data": (pathlib.Path(artifact_root) / row["relpath"]).read_bytes(),
                    }
        return [slot for slot in slots if slot is not None], consumed, None

    def _post_action_response(
        self,
        result: Any,
        request: NamedServiceRequest,
        *,
        action: str,
        default_code: str,
        fallback_message: str,
    ) -> NamedServiceResponse:
        """Shared ok/error shaping for post-producing actions."""
        if not isinstance(result, Mapping) or not result.get("ok"):
            return _error_from_tool(
                result if isinstance(result, Mapping) else {},
                request=request,
                default_code=default_code,
                fallback_message=fallback_message,
            )
        ret = dict(result.get("ret") or {})
        account_id = _text(ret.get("account_id"))
        post_urn = _text(ret.get("post_urn"))
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            object_ref=post_ref(account_id, post_urn) if post_urn else None,
            object={**_post_object(account_id, post_urn), **ret} if post_urn else ret,
            extra={"action": action},
        )

    async def _publish_org_post(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        # Text-only, like publish_post: anything file-shaped must not vanish
        # silently. Org image posts are not offered until they are real.
        present = [key for key in self._FILE_KEYS if payload.get(key)]
        if present:
            return NamedServiceResponse.error_response(
                code="linkedin_post_carries_no_images",
                message=(
                    f"This action publishes text only; {', '.join(present)} would be dropped."
                ),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
                details={"rejected_keys": present},
            )
        return await self._publish(
            request, action=ACTION_PUBLISH_ORG_POST, files=[], consumed=[], as_organization=True
        )

    async def _list_org_posts(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        result = await self._linkedin.list_linkedin_org_posts(
            count=int(payload.get("count") or 10),
            start=int(payload.get("start") or 0),
            account_id=self._account_id_for(request),
        )
        if not isinstance(result, Mapping) or not result.get("ok"):
            return _error_from_tool(
                result if isinstance(result, Mapping) else {},
                request=request,
                default_code="linkedin_org_posts_failed",
                fallback_message="LinkedIn organization posts could not be listed.",
            )
        ret = dict(result.get("ret") or {})
        account_id = _text(ret.get("account_id"))
        items = [
            {**_post_object(account_id, _text(row.get("post_urn"))), **dict(row)}
            for row in (ret.get("posts") or [])
            if _text(dict(row or {}).get("post_urn"))
        ]
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            items=items,
            extra={
                "action": ACTION_LIST_ORG_POSTS,
                "count": len(items),
                "author": _text(ret.get("author")),
                "account_id": account_id,
            },
        )

    async def _read_post_engagement(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        post_urn = _text(payload.get("post_urn"))
        if not post_urn:
            parsed = parse_linkedin_ref(request.object_ref or "")
            post_urn = _text(parsed.get("post_urn"))
        if not post_urn:
            return NamedServiceResponse.error_response(
                code="post_urn_required",
                message=(
                    "read_post_engagement needs a post: pass payload.post_urn or a "
                    "linkedin:<account_id>:post:<post_urn> object_ref."
                ),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
            )
        result = await self._linkedin.read_linkedin_post_engagement(
            post_urn=post_urn,
            include_comments=bool(payload.get("include_comments", True)),
            comments_count=int(payload.get("comments_count") or 25),
            account_id=self._account_id_for(request),
        )
        if not isinstance(result, Mapping) or not result.get("ok"):
            return _error_from_tool(
                result if isinstance(result, Mapping) else {},
                request=request,
                default_code="linkedin_engagement_failed",
                fallback_message="LinkedIn post engagement could not be read.",
            )
        ret = dict(result.get("ret") or {})
        account_id = _text(ret.get("account_id"))
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            object_ref=post_ref(account_id, post_urn) if account_id else None,
            object={**_post_object(account_id, post_urn), **ret},
            extra={"action": ACTION_READ_POST_ENGAGEMENT},
        )

    async def _add_comment(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        parsed = parse_linkedin_ref(request.object_ref or "")
        post_urn = _text(payload.get("post_urn")) or parsed.get("post_urn", "")
        if not post_urn:
            return NamedServiceResponse.error_response(
                code="linkedin_post_ref_required",
                message="Provide a linkedin:<account_id>:post:<post_urn> object_ref or payload.post_urn.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
            )
        result = await self._linkedin.comment_on_linkedin_post(
            post_urn=post_urn,
            text=_text(payload.get("text")),
            account_id=self._account_id_for(request),
        )
        if not isinstance(result, Mapping) or not result.get("ok"):
            return _error_from_tool(
                result if isinstance(result, Mapping) else {},
                request=request,
                default_code="linkedin_comment_failed",
                fallback_message="LinkedIn comment could not be added.",
            )
        ret = dict(result.get("ret") or {})
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            object_ref=post_ref(_text(ret.get("account_id")), post_urn),
            object=ret,
            extra={"action": ACTION_ADD_COMMENT},
        )

    # --- Mutations on published content -------------------------------------

    def _target_post_urn(self, request: NamedServiceRequest) -> str:
        """Target post URN from payload.post_urn or the post object_ref."""
        payload = dict(request.payload or {})
        post_urn = _text(payload.get("post_urn"))
        if post_urn:
            return post_urn
        return _text(parse_linkedin_ref(request.object_ref or "").get("post_urn"))

    def _post_ref_required(self, request: NamedServiceRequest, *, action: str) -> NamedServiceResponse:
        return NamedServiceResponse.error_response(
            code="linkedin_post_ref_required",
            message=(
                f"{action} needs a post: provide a "
                "linkedin:<account_id>:post:<post_urn> object_ref or payload.post_urn."
            ),
            status=400,
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
        )

    async def _update_post_text(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        post_urn = self._target_post_urn(request)
        if not post_urn:
            return self._post_ref_required(request, action=ACTION_UPDATE_POST_TEXT)
        result = await self._linkedin.update_linkedin_post_text(
            post_urn=post_urn,
            text=_text(payload.get("text")),
            account_id=self._account_id_for(request),
            as_organization=bool(payload.get("as_organization")),
        )
        if not isinstance(result, Mapping) or not result.get("ok"):
            return _error_from_tool(
                result if isinstance(result, Mapping) else {},
                request=request,
                default_code="linkedin_update_failed",
                fallback_message="LinkedIn post text could not be updated.",
            )
        ret = dict(result.get("ret") or {})
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            object_ref=post_ref(_text(ret.get("account_id")), post_urn),
            object=ret,
            extra={"action": ACTION_UPDATE_POST_TEXT},
        )

    async def _delete_post(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        post_urn = self._target_post_urn(request)
        if not post_urn:
            return self._post_ref_required(request, action=ACTION_DELETE_POST)
        result = await self._linkedin.delete_linkedin_post(
            post_urn=post_urn,
            account_id=self._account_id_for(request),
            as_organization=bool(payload.get("as_organization")),
        )
        if not isinstance(result, Mapping) or not result.get("ok"):
            return _error_from_tool(
                result if isinstance(result, Mapping) else {},
                request=request,
                default_code="linkedin_delete_failed",
                fallback_message="LinkedIn post could not be deleted.",
            )
        ret = dict(result.get("ret") or {})
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            object_ref=post_ref(_text(ret.get("account_id")), post_urn),
            object=ret,
            extra={"action": ACTION_DELETE_POST},
        )

    async def _delete_comment(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        post_urn = self._target_post_urn(request)
        if not post_urn:
            return self._post_ref_required(request, action=ACTION_DELETE_COMMENT)
        comment_id = _text(payload.get("comment_id"))
        if not comment_id:
            return NamedServiceResponse.error_response(
                code="comment_id_required",
                message=f"{ACTION_DELETE_COMMENT} needs payload.comment_id (the id add_comment returned).",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or LINKEDIN_NAMESPACE,
            )
        result = await self._linkedin.delete_linkedin_comment(
            post_urn=post_urn,
            comment_id=comment_id,
            account_id=self._account_id_for(request),
        )
        if not isinstance(result, Mapping) or not result.get("ok"):
            return _error_from_tool(
                result if isinstance(result, Mapping) else {},
                request=request,
                default_code="linkedin_comment_delete_failed",
                fallback_message="LinkedIn comment could not be deleted.",
            )
        ret = dict(result.get("ret") or {})
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or LINKEDIN_NAMESPACE,
            object_ref=post_ref(_text(ret.get("account_id")), post_urn),
            object=ret,
            extra={"action": ACTION_DELETE_COMMENT},
        )


def make_linkedin_named_service_provider(
    *,
    entrypoint: Any = None,
    bundle_id: str | None = None,
    connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    staging_root_factory: Any = None,
    upload_slot_factory: Any = None,
) -> LinkedInNamedServiceProvider:
    return LinkedInNamedServiceProvider(
        entrypoint=entrypoint,
        bundle_id=bundle_id,
        connection_hub_bundle_id=connection_hub_bundle_id,
        staging_root_factory=staging_root_factory,
        upload_slot_factory=upload_slot_factory,
    )


__all__ = [
    "ACTION_ADD_COMMENT",
    "ACTION_DELETE_COMMENT",
    "ACTION_DELETE_POST",
    "ACTION_DISCARD_UPLOAD",
    "ACTION_PUBLISH_IMAGE_POST",
    "ACTION_PUBLISH_POST",
    "ACTION_REQUEST_UPLOAD",
    "ACTION_UPDATE_POST_TEXT",
    "LINKEDIN_ACCOUNT_KIND",
    "LINKEDIN_ACTIONS",
    "LINKEDIN_CONNECTED_ACCOUNT_REQUIREMENTS",
    "LINKEDIN_GRANT_HINTS",
    "LINKEDIN_NAMESPACE",
    "LINKEDIN_POST_KIND",
    "LINKEDIN_SCHEMA",
    "LINKEDIN_SCHEMA_PROJECTION",
    "LinkedInNamedServiceProvider",
    "account_ref",
    "linkedin_named_service_spec",
    "linkedin_schema_for_surface",
    "make_linkedin_named_service_provider",
    "parse_linkedin_ref",
    "post_ref",
]
