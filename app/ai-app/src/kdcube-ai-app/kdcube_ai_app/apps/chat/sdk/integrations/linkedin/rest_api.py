# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""LinkedIn versioned REST API (``/rest``) protocol layer.

Request/response shapes only: no credential resolution, no consent checks, no
tool envelopes. Callers map failures onto the shared provider-failure contract.

`/rest/posts` and `/rest/images` supersede the `/v2/ugcPosts` and `/v2/assets`
mechanics in ``accounts.py``, which remain in use by the bundle-owned OAuth
integration.
https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Mapping, Sequence

import httpx

LINKEDIN_REST_BASE = "https://api.linkedin.com/rest"
LINKEDIN_POSTS_URL = f"{LINKEDIN_REST_BASE}/posts"
LINKEDIN_IMAGES_URL = f"{LINKEDIN_REST_BASE}/images"
# Comments use the pre-versioning endpoint: /rest/socialActions answers 403
# `partnerApiSocialActions.CREATE.<version>` without Community Management
# partner access, which w_member_social does not grant.
LINKEDIN_SOCIAL_ACTIONS_URL = "https://api.linkedin.com/v2/socialActions"
LINKEDIN_RESTLI_PROTOCOL_VERSION = "2.0.0"

# Shipped default for the descriptor template. LinkedIn sunsets dated
# versions; deployments override via the bundle prop
# `integrations.linkedin.api_version`.
DEFAULT_LINKEDIN_API_VERSION = "202601"

LINKEDIN_POST_MAX_CHARS = 3000
# One image goes under content.media; 2..20 under content.multiImage. Same
# endpoint and permission for both.
MULTI_IMAGE_MIN = 2
MULTI_IMAGE_MAX = 20
MAX_IMAGE_BYTES = 36_152_320
SUPPORTED_IMAGE_MIME = ("image/jpeg", "image/png", "image/gif")


class LinkedInPayloadError(ValueError):
    """Caller-side payload violates a documented LinkedIn constraint."""


def person_urn(subject: str) -> str:
    value = str(subject or "").strip()
    if not value:
        raise LinkedInPayloadError("LinkedIn member subject is required")
    return value if value.startswith("urn:li:") else f"urn:li:person:{value}"


def post_permalink(post_urn: str) -> str:
    value = str(post_urn or "").strip()
    return f"https://www.linkedin.com/feed/update/{value}" if value else ""


def rest_headers(*, access_token: str, api_version: str, json_body: bool = False) -> dict[str, str]:
    version = str(api_version or "").strip()
    if not version:
        raise LinkedInPayloadError(
            "LinkedIn API version is required; set integrations.linkedin.api_version"
        )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": version,
        "X-Restli-Protocol-Version": LINKEDIN_RESTLI_PROTOCOL_VERSION,
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def legacy_headers(*, access_token: str, json_body: bool = False) -> dict[str, str]:
    """Headers for the pre-versioning `/v2` endpoints: no LinkedIn-Version."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": LINKEDIN_RESTLI_PROTOCOL_VERSION,
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def build_image_upload_body(*, owner_urn: str) -> dict[str, Any]:
    return {"initializeUploadRequest": {"owner": owner_urn}}


def parse_image_upload_init(body: Mapping[str, Any] | None) -> dict[str, Any]:
    value = (dict(body or {}).get("value") or {}) if isinstance(body, Mapping) else {}
    return {
        "upload_url": str(value.get("uploadUrl") or "").strip(),
        "image_urn": str(value.get("image") or "").strip(),
        "expires_at": int(value.get("uploadUrlExpiresAt") or 0),
    }


def build_post_content(images: Sequence[Mapping[str, Any]] | None) -> dict[str, Any] | None:
    """Content block for the given images, or None for a text-only post.

    One image emits `content.media`; several emit `content.multiImage`.
    """
    rows = [dict(item or {}) for item in (images or [])]
    entries = []
    for row in rows:
        urn = str(row.get("image_urn") or row.get("id") or "").strip()
        if not urn:
            raise LinkedInPayloadError("image entry has no image URN")
        entry: dict[str, Any] = {"id": urn}
        alt_text = str(row.get("alt_text") or row.get("altText") or "").strip()
        if alt_text:
            entry["altText"] = alt_text
        entries.append(entry)

    if not entries:
        return None
    if len(entries) == 1:
        return {"media": entries[0]}
    if len(entries) > MULTI_IMAGE_MAX:
        raise LinkedInPayloadError(
            f"LinkedIn accepts at most {MULTI_IMAGE_MAX} images per post, got {len(entries)}"
        )
    return {"multiImage": {"images": entries}}


def build_post_body(
    *,
    author_urn: str,
    commentary: str,
    images: Sequence[Mapping[str, Any]] | None = None,
    visibility: str = "PUBLIC",
    feed_distribution: str = "MAIN_FEED",
    reshare_disabled: bool = False,
) -> dict[str, Any]:
    text = str(commentary or "")
    if not text.strip():
        raise LinkedInPayloadError("LinkedIn post commentary is required")
    if len(text) > LINKEDIN_POST_MAX_CHARS:
        raise LinkedInPayloadError(
            f"LinkedIn post commentary exceeds {LINKEDIN_POST_MAX_CHARS} characters"
        )
    body: dict[str, Any] = {
        "author": author_urn,
        "commentary": text,
        "visibility": visibility,
        "distribution": {
            "feedDistribution": feed_distribution,
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": bool(reshare_disabled),
    }
    content = build_post_content(images)
    if content is not None:
        body["content"] = content
    return body


def build_comment_body(*, actor_urn: str, object_urn: str, text: str) -> dict[str, Any]:
    message = str(text or "")
    if not message.strip():
        raise LinkedInPayloadError("LinkedIn comment text is required")
    return {
        "actor": actor_urn,
        "object": object_urn,
        "message": {"text": message},
    }


def created_urn_from_response(response: Any) -> str:
    """Created entity id, read from the `x-restli-id` response header.

    `/rest/posts` and `/rest/socialActions/.../comments` answer 201 with an
    empty or partial body; the id is not in the body.
    """
    headers = getattr(response, "headers", {}) or {}
    return str(headers.get("x-restli-id") or headers.get("X-RestLi-Id") or "").strip()


def comment_urn_from_body(body: Mapping[str, Any] | None, *, comment_id: str = "") -> str:
    """Comment URN, or "" when the response carries nothing to build it from.

    Prefers a URN LinkedIn returned. The composite fallback is keyed on the
    response's own `object`, never on the request target: the two can differ,
    and a URN built from the target is not a valid comment key.
    """
    data = dict(body or {})
    returned = str(data.get("commentUrn") or data.get("$URN") or "").strip()
    if returned:
        return returned
    thread = str(data.get("object") or "").strip()
    comment = str(comment_id or "").strip()
    return f"urn:li:comment:({thread},{comment})" if thread and comment else ""


def social_actions_comments_url(object_urn: str) -> str:
    quoted = urllib.parse.quote(str(object_urn or "").strip(), safe="")
    if not quoted:
        raise LinkedInPayloadError("LinkedIn post URN is required")
    return f"{LINKEDIN_SOCIAL_ACTIONS_URL}/{quoted}/comments"


async def initialize_image_upload(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    owner_urn: str,
) -> httpx.Response:
    return await client.post(
        LINKEDIN_IMAGES_URL,
        params={"action": "initializeUpload"},
        json=build_image_upload_body(owner_urn=owner_urn),
        headers=rest_headers(access_token=access_token, api_version=api_version, json_body=True),
    )


async def upload_image_bytes(
    client: httpx.AsyncClient,
    *,
    upload_url: str,
    access_token: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> httpx.Response:
    if not str(upload_url or "").strip():
        raise LinkedInPayloadError("upload_url is required")
    return await client.put(
        upload_url,
        content=data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": content_type,
        },
    )


async def create_post(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    author_urn: str,
    commentary: str,
    images: Sequence[Mapping[str, Any]] | None = None,
    visibility: str = "PUBLIC",
) -> httpx.Response:
    return await client.post(
        LINKEDIN_POSTS_URL,
        json=build_post_body(
            author_urn=author_urn,
            commentary=commentary,
            images=images,
            visibility=visibility,
        ),
        headers=rest_headers(access_token=access_token, api_version=api_version, json_body=True),
    )


async def create_comment(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    actor_urn: str,
    object_urn: str,
    text: str,
) -> httpx.Response:
    """Create a comment on `/v2/socialActions`.

    Unversioned by design: see LINKEDIN_SOCIAL_ACTIONS_URL.
    """
    return await client.post(
        social_actions_comments_url(object_urn),
        json=build_comment_body(actor_urn=actor_urn, object_urn=object_urn, text=text),
        headers=legacy_headers(access_token=access_token, json_body=True),
    )


__all__ = [
    "DEFAULT_LINKEDIN_API_VERSION",
    "LINKEDIN_IMAGES_URL",
    "LINKEDIN_POSTS_URL",
    "LINKEDIN_POST_MAX_CHARS",
    "LINKEDIN_REST_BASE",
    "LINKEDIN_SOCIAL_ACTIONS_URL",
    "MAX_IMAGE_BYTES",
    "MULTI_IMAGE_MAX",
    "MULTI_IMAGE_MIN",
    "SUPPORTED_IMAGE_MIME",
    "LinkedInPayloadError",
    "build_comment_body",
    "build_image_upload_body",
    "build_post_body",
    "build_post_content",
    "comment_urn_from_body",
    "create_comment",
    "create_post",
    "created_urn_from_response",
    "initialize_image_upload",
    "legacy_headers",
    "parse_image_upload_init",
    "person_urn",
    "post_permalink",
    "rest_headers",
    "social_actions_comments_url",
    "upload_image_bytes",
]
