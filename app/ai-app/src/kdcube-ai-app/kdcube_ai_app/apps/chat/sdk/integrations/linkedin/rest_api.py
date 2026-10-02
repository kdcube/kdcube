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
LINKEDIN_DOCUMENTS_URL = f"{LINKEDIN_REST_BASE}/documents"
LINKEDIN_VIDEOS_URL = f"{LINKEDIN_REST_BASE}/videos"
# Comments use the pre-versioning endpoint: /rest/socialActions answers 403
# `partnerApiSocialActions.CREATE.<version>` without Community Management
# partner access, which w_member_social does not grant.
LINKEDIN_SOCIAL_ACTIONS_URL = "https://api.linkedin.com/v2/socialActions"
# Organization lane (Community Management API app): versioned endpoints. The
# member lane cannot reach these; the org token can, including the VERSIONED
# socialActions reads that a w_member_social token is denied.
LINKEDIN_ORGANIZATION_ACLS_URL = f"{LINKEDIN_REST_BASE}/organizationAcls"
LINKEDIN_ORGANIZATIONS_URL = f"{LINKEDIN_REST_BASE}/organizations"
LINKEDIN_REST_SOCIAL_ACTIONS_URL = f"{LINKEDIN_REST_BASE}/socialActions"
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
# Images API: fewer than 36,152,320 pixels (width x height), JPG/PNG/GIF, a GIF
# of at most 250 frames. LinkedIn publishes no byte limit for images.
MAX_IMAGE_PIXELS = 36_152_320
MAX_GIF_FRAMES = 250
SUPPORTED_IMAGE_MIME = ("image/jpeg", "image/png", "image/gif")
# ArticleContent (the link-preview card): source + title required, the rest
# optional. A post carries an article card OR images, never both.
# Strict upper bounds: the doc says LESS THAN 400 / 4,086 characters.
ARTICLE_TITLE_MAX_CHARS = 400
ARTICLE_DESCRIPTION_MAX_CHARS = 4086
# PollContent: question <= 140 chars, 2..4 options of <= 30 chars each,
# duration from the fixed enum. Polls are non-sponsored only; LinkedIn rejects
# isVoterVisibleToAuthor=false, so it is not offered.
POLL_QUESTION_MAX_CHARS = 140
POLL_OPTION_MAX_CHARS = 30
POLL_OPTIONS_MIN = 2
POLL_OPTIONS_MAX = 4
POLL_DURATIONS = ("ONE_DAY", "THREE_DAYS", "SEVEN_DAYS", "FOURTEEN_DAYS")
# Documents API: PPT/PPTX/DOC/DOCX/PDF up to 100 MB and 300 pages; the post's
# content.media carries the document URN and a REQUIRED title.
MAX_DOCUMENT_BYTES = 104_857_600
SUPPORTED_DOCUMENT_MIME = (
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
)
# Videos API: MP4, 75 KB .. 500 MB, 3 s .. 30 min; multipart upload in the
# byte ranges initializeUpload dictates, each part's ETag feeds finalizeUpload.
MIN_VIDEO_BYTES = 75 * 1024
MAX_VIDEO_BYTES = 500 * 1024 * 1024
SUPPORTED_VIDEO_MIME = ("video/mp4",)


def utf16_length(text: str) -> int:
    """Length as LinkedIn counts it: UTF-16 code units.

    An astral-plane character (most emoji) costs 2; ZWJ emoji sequences cost
    the sum of their parts. Python's len() counts code points and undercounts
    exactly those, so every LinkedIn limit here measures with this.
    """
    value = str(text or "")
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in value)


class LinkedInPayloadError(ValueError):
    """Caller-side payload violates a documented LinkedIn constraint."""


def person_urn(subject: str) -> str:
    value = str(subject or "").strip()
    if not value:
        raise LinkedInPayloadError("LinkedIn member subject is required")
    return value if value.startswith("urn:li:") else f"urn:li:person:{value}"


def organization_urn(organization: str) -> str:
    """Organization author URN from a page id or an already-full URN."""
    value = str(organization or "").strip()
    if not value:
        raise LinkedInPayloadError("LinkedIn organization is required")
    return value if value.startswith("urn:li:") else f"urn:li:organization:{value}"


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


def build_article_content(
    *,
    source: str,
    title: str,
    description: str = "",
    thumbnail_urn: str = "",
    thumbnail_alt: str = "",
) -> dict[str, Any]:
    """content.article — the link-preview card. source + title required."""
    url = str(source or "").strip()
    if not url:
        raise LinkedInPayloadError("LinkedIn article source URL is required")
    heading = str(title or "").strip()
    if not heading:
        raise LinkedInPayloadError("LinkedIn article title is required")
    if utf16_length(heading) >= ARTICLE_TITLE_MAX_CHARS:
        raise LinkedInPayloadError(
            f"LinkedIn article title must be under {ARTICLE_TITLE_MAX_CHARS} characters"
        )
    article: dict[str, Any] = {"source": url, "title": heading}
    summary = str(description or "").strip()
    if summary:
        if utf16_length(summary) >= ARTICLE_DESCRIPTION_MAX_CHARS:
            raise LinkedInPayloadError(
                f"LinkedIn article description must be under {ARTICLE_DESCRIPTION_MAX_CHARS} characters"
            )
        article["description"] = summary
    urn = str(thumbnail_urn or "").strip()
    if urn:
        article["thumbnail"] = urn
        alt = str(thumbnail_alt or "").strip()
        if alt:
            if utf16_length(alt) >= ARTICLE_DESCRIPTION_MAX_CHARS:
                raise LinkedInPayloadError(
                    f"LinkedIn article thumbnail alt text must be under {ARTICLE_DESCRIPTION_MAX_CHARS} characters"
                )
            article["thumbnailAltText"] = alt
    return article


def build_poll_content(
    *,
    question: str,
    options: Sequence[str],
    duration: str = "SEVEN_DAYS",
) -> dict[str, Any]:
    """content.poll — question, 2..4 options, duration from the fixed enum."""
    text = str(question or "").strip()
    if not text:
        raise LinkedInPayloadError("LinkedIn poll question is required")
    if utf16_length(text) > POLL_QUESTION_MAX_CHARS:
        raise LinkedInPayloadError(
            f"LinkedIn poll question exceeds {POLL_QUESTION_MAX_CHARS} characters"
        )
    rows = [str(item or "").strip() for item in (options or [])]
    rows = [item for item in rows if item]
    if not (POLL_OPTIONS_MIN <= len(rows) <= POLL_OPTIONS_MAX):
        raise LinkedInPayloadError(
            f"LinkedIn polls take {POLL_OPTIONS_MIN} to {POLL_OPTIONS_MAX} options, got {len(rows)}"
        )
    for item in rows:
        if utf16_length(item) > POLL_OPTION_MAX_CHARS:
            raise LinkedInPayloadError(
                f"LinkedIn poll option {item!r} exceeds {POLL_OPTION_MAX_CHARS} characters"
            )
    window = str(duration or "").strip().upper() or "SEVEN_DAYS"
    if window not in POLL_DURATIONS:
        raise LinkedInPayloadError(
            f"LinkedIn poll duration must be one of {', '.join(POLL_DURATIONS)}"
        )
    return {
        "question": text,
        "options": [{"text": item} for item in rows],
        "settings": {"duration": window},
    }


def build_document_media(*, document_urn: str, title: str) -> dict[str, Any]:
    """content.media for a document post — the title is REQUIRED by LinkedIn."""
    urn = str(document_urn or "").strip()
    if not urn:
        raise LinkedInPayloadError("LinkedIn document URN is required")
    heading = str(title or "").strip()
    if not heading:
        raise LinkedInPayloadError("LinkedIn document posts require a title")
    return {"id": urn, "title": heading}


def build_video_media(*, video_urn: str, title: str = "") -> dict[str, Any]:
    """content.media for a video post — the title is optional."""
    urn = str(video_urn or "").strip()
    if not urn:
        raise LinkedInPayloadError("LinkedIn video URN is required")
    media: dict[str, Any] = {"id": urn}
    heading = str(title or "").strip()
    if heading:
        media["title"] = heading
    return media


def build_post_body(
    *,
    author_urn: str,
    commentary: str,
    images: Sequence[Mapping[str, Any]] | None = None,
    article: Mapping[str, Any] | None = None,
    poll: Mapping[str, Any] | None = None,
    media: Mapping[str, Any] | None = None,
    visibility: str = "PUBLIC",
    feed_distribution: str = "MAIN_FEED",
    reshare_disabled: bool = False,
) -> dict[str, Any]:
    """One post body, ONE content shape.

    ``images`` -> content.media/multiImage; ``article`` -> content.article;
    ``poll`` -> content.poll; ``media`` -> a prebuilt content.media entry
    (document or video URN). Passing more than one raises: the doc implies a
    single content member per post, and this guard fails actionably instead of
    letting LinkedIn fail opaquely.
    """
    text = str(commentary or "")
    if not text.strip():
        raise LinkedInPayloadError("LinkedIn post commentary is required")
    # LinkedIn counts UTF-16 code units (emoji cost 2), not code points.
    if utf16_length(text) > LINKEDIN_POST_MAX_CHARS:
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
    image_content = build_post_content(images)
    members: list[tuple[str, dict[str, Any]]] = []
    if image_content is not None:
        members.append(("images", image_content))
    if article:
        members.append(("article", {"article": dict(article)}))
    if poll:
        members.append(("poll", {"poll": dict(poll)}))
    if media:
        members.append(("media", {"media": dict(media)}))
    if len(members) > 1:
        names = " + ".join(name for name, _content in members)
        raise LinkedInPayloadError(
            f"A LinkedIn post carries ONE content shape; got {names}"
        )
    if members:
        body["content"] = members[0][1]
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


def social_actions_comment_url(object_urn: str, comment_id: str) -> str:
    """One comment's entity URL on the unversioned `/v2/socialActions`."""
    quoted = urllib.parse.quote(str(comment_id or "").strip(), safe="")
    if not quoted:
        raise LinkedInPayloadError("LinkedIn comment id is required")
    return f"{social_actions_comments_url(object_urn)}/{quoted}"


def post_entity_url(post_urn: str) -> str:
    """One post's entity URL on `/rest/posts` (delete / partial update)."""
    quoted = urllib.parse.quote(str(post_urn or "").strip(), safe="")
    if not quoted:
        raise LinkedInPayloadError("LinkedIn post URN is required")
    return f"{LINKEDIN_POSTS_URL}/{quoted}"


def build_commentary_patch(commentary: str) -> dict[str, Any]:
    """PARTIAL_UPDATE body that replaces a post's commentary and nothing else.

    LinkedIn permits editing the text of a published post; the attached
    card/media can never change, so `commentary` is the only patched field.
    """
    text = str(commentary or "")
    if not text.strip():
        raise LinkedInPayloadError("LinkedIn post commentary is required")
    if utf16_length(text) > LINKEDIN_POST_MAX_CHARS:
        raise LinkedInPayloadError(
            f"LinkedIn post commentary exceeds {LINKEDIN_POST_MAX_CHARS} characters"
        )
    return {"patch": {"$set": {"commentary": text}}}


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
    article: Mapping[str, Any] | None = None,
    poll: Mapping[str, Any] | None = None,
    media: Mapping[str, Any] | None = None,
    visibility: str = "PUBLIC",
) -> httpx.Response:
    return await client.post(
        LINKEDIN_POSTS_URL,
        json=build_post_body(
            author_urn=author_urn,
            commentary=commentary,
            images=images,
            article=article,
            poll=poll,
            media=media,
            visibility=visibility,
        ),
        headers=rest_headers(access_token=access_token, api_version=api_version, json_body=True),
    )


async def delete_post(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    post_urn: str,
) -> httpx.Response:
    """Delete one post by URN.

    Idempotent per the Posts API doc: deleting an already-deleted post still
    answers 204.
    """
    headers = rest_headers(access_token=access_token, api_version=api_version)
    headers["X-RestLi-Method"] = "DELETE"
    return await client.delete(post_entity_url(post_urn), headers=headers)


async def update_post_commentary(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    post_urn: str,
    commentary: str,
) -> httpx.Response:
    """Replace one post's commentary (PARTIAL_UPDATE on the entity URL).

    204 = success; the response carries no body.
    """
    headers = rest_headers(access_token=access_token, api_version=api_version, json_body=True)
    headers["X-RestLi-Method"] = "PARTIAL_UPDATE"
    return await client.post(
        post_entity_url(post_urn),
        json=build_commentary_patch(commentary),
        headers=headers,
    )


# --- Document upload (Documents API; mirrors the Images API mechanics) ------


async def initialize_document_upload(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    owner_urn: str,
) -> httpx.Response:
    return await client.post(
        LINKEDIN_DOCUMENTS_URL,
        params={"action": "initializeUpload"},
        json={"initializeUploadRequest": {"owner": owner_urn}},
        headers=rest_headers(access_token=access_token, api_version=api_version, json_body=True),
    )


def parse_document_upload_init(body: Mapping[str, Any] | None) -> dict[str, Any]:
    value = (dict(body or {}).get("value") or {}) if isinstance(body, Mapping) else {}
    return {
        "upload_url": str(value.get("uploadUrl") or "").strip(),
        "document_urn": str(value.get("document") or "").strip(),
        "expires_at": int(value.get("uploadUrlExpiresAt") or 0),
    }


# --- Video upload (Videos API; multipart by byte ranges) --------------------


async def initialize_video_upload(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    owner_urn: str,
    file_size_bytes: int,
) -> httpx.Response:
    return await client.post(
        LINKEDIN_VIDEOS_URL,
        params={"action": "initializeUpload"},
        json={
            "initializeUploadRequest": {
                "owner": owner_urn,
                "fileSizeBytes": int(file_size_bytes),
                "uploadCaptions": False,
                "uploadThumbnail": False,
            }
        },
        headers=rest_headers(access_token=access_token, api_version=api_version, json_body=True),
    )


def parse_video_upload_init(body: Mapping[str, Any] | None) -> dict[str, Any]:
    value = (dict(body or {}).get("value") or {}) if isinstance(body, Mapping) else {}
    instructions = [
        {
            "upload_url": str(dict(item or {}).get("uploadUrl") or "").strip(),
            "first_byte": int(dict(item or {}).get("firstByte") or 0),
            "last_byte": int(dict(item or {}).get("lastByte") or 0),
        }
        for item in (value.get("uploadInstructions") or [])
    ]
    return {
        "video_urn": str(value.get("video") or "").strip(),
        "upload_token": str(value.get("uploadToken") or ""),
        "upload_instructions": instructions,
        "expires_at": int(value.get("uploadUrlsExpireAt") or 0),
    }


def etag_from_response(response: Any) -> str:
    """The uploaded part id: the ETag response header, quotes stripped."""
    headers = getattr(response, "headers", {}) or {}
    return str(headers.get("etag") or headers.get("ETag") or "").strip().strip('"')


async def upload_video_part(
    client: httpx.AsyncClient,
    *,
    upload_url: str,
    data: bytes,
) -> httpx.Response:
    """PUT one byte range to its signed part URL; the response carries the ETag."""
    if not str(upload_url or "").strip():
        raise LinkedInPayloadError("upload_url is required")
    return await client.put(
        upload_url,
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


async def finalize_video_upload(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    video_urn: str,
    upload_token: str,
    uploaded_part_ids: Sequence[str],
) -> httpx.Response:
    return await client.post(
        LINKEDIN_VIDEOS_URL,
        params={"action": "finalizeUpload"},
        json={
            "finalizeUploadRequest": {
                "video": video_urn,
                "uploadToken": str(upload_token or ""),
                "uploadedPartIds": [str(item) for item in uploaded_part_ids],
            }
        },
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


async def delete_comment(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    actor_urn: str,
    object_urn: str,
    comment_id: str,
) -> httpx.Response:
    """Delete a comment on `/v2/socialActions`.

    Unversioned by design, same as create_comment: comments live on `/v2` for
    standard applications. The deleting actor rides as a query parameter.
    """
    actor = str(actor_urn or "").strip()
    if not actor:
        raise LinkedInPayloadError("LinkedIn actor URN is required")
    return await client.delete(
        social_actions_comment_url(object_urn, comment_id),
        params={"actor": actor},
        headers=legacy_headers(access_token=access_token),
    )


# --- Organization lane (Community Management API) -------------------------
# Reads exist ONLY here: the member lane has no read at all. Every function is
# protocol-shape only, like the rest of this module.


def parse_organization_acls(body: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Approved role assignments from an organizationAcls response.

    Each row: {organization: urn, role, state, role_assignee: urn}.
    """
    elements = (dict(body or {}).get("elements") or []) if isinstance(body, Mapping) else []
    rows: list[dict[str, Any]] = []
    for item in elements:
        data = dict(item or {})
        organization = str(data.get("organization") or "").strip()
        if not organization:
            continue
        rows.append(
            {
                "organization": organization,
                "role": str(data.get("role") or "").strip(),
                "state": str(data.get("state") or "").strip(),
                "role_assignee": str(data.get("roleAssignee") or "").strip(),
            }
        )
    return rows


def parse_posts_page(body: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Post rows from a /rest/posts finder response, newest shape kept small."""
    elements = (dict(body or {}).get("elements") or []) if isinstance(body, Mapping) else []
    rows: list[dict[str, Any]] = []
    for item in elements:
        data = dict(item or {})
        urn = str(data.get("id") or "").strip()
        if not urn:
            continue
        rows.append(
            {
                "post_urn": urn,
                "permalink": post_permalink(urn),
                "author": str(data.get("author") or "").strip(),
                "commentary": str(data.get("commentary") or ""),
                "visibility": str(data.get("visibility") or "").strip(),
                "lifecycle_state": str(data.get("lifecycleState") or "").strip(),
                "published_at": int(data.get("publishedAt") or 0),
                "last_modified_at": int(data.get("lastModifiedAt") or 0),
            }
        )
    return rows


def parse_social_summary(body: Mapping[str, Any] | None) -> dict[str, Any]:
    """Comment/reaction counts from a socialActions entity response."""
    data = dict(body or {}) if isinstance(body, Mapping) else {}
    comments = dict(data.get("commentsSummary") or {})
    likes = dict(data.get("likesSummary") or {})
    return {
        "target_urn": str(data.get("target") or data.get("$URN") or "").strip(),
        "comment_count": int(comments.get("aggregatedTotalComments") or comments.get("count") or 0),
        "like_count": int(likes.get("aggregatedTotalLikes") or likes.get("totalLikes") or 0),
    }


def parse_comments_page(body: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Comment rows from a socialActions comments response."""
    elements = (dict(body or {}).get("elements") or []) if isinstance(body, Mapping) else []
    rows: list[dict[str, Any]] = []
    for item in elements:
        data = dict(item or {})
        message = data.get("message")
        text = str(dict(message or {}).get("text") or "") if isinstance(message, Mapping) else ""
        rows.append(
            {
                "comment_urn": str(data.get("commentUrn") or data.get("$URN") or "").strip(),
                "actor": str(data.get("actor") or "").strip(),
                "object": str(data.get("object") or "").strip(),
                "text": text,
                "created_at": int(dict(data.get("created") or {}).get("time") or 0),
            }
        )
    return rows


async def fetch_organization_acls(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    role: str = "ADMINISTRATOR",
    state: str = "APPROVED",
) -> httpx.Response:
    """Organizations the token's member holds the given role on."""
    return await client.get(
        LINKEDIN_ORGANIZATION_ACLS_URL,
        params={"q": "roleAssignee", "role": role, "state": state},
        headers=rest_headers(access_token=access_token, api_version=api_version),
    )


async def fetch_organization(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    organization_urn: str,
) -> httpx.Response:
    """One organization record (localizedName etc.) by URN."""
    org_id = str(organization_urn or "").rsplit(":", 1)[-1].strip()
    if not org_id:
        raise LinkedInPayloadError("LinkedIn organization URN is required")
    return await client.get(
        f"{LINKEDIN_ORGANIZATIONS_URL}/{urllib.parse.quote(org_id, safe='')}",
        headers=rest_headers(access_token=access_token, api_version=api_version),
    )


async def list_posts_by_author(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    author_urn: str,
    count: int = 10,
    start: int = 0,
) -> httpx.Response:
    """Posts authored by the given URN (org lane read: q=author finder)."""
    if not str(author_urn or "").strip():
        raise LinkedInPayloadError("LinkedIn author URN is required")
    return await client.get(
        LINKEDIN_POSTS_URL,
        params={
            "q": "author",
            "author": author_urn,
            "count": max(1, min(int(count or 10), 100)),
            "start": max(0, int(start or 0)),
        },
        headers=rest_headers(access_token=access_token, api_version=api_version),
    )


async def fetch_social_summary(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    object_urn: str,
) -> httpx.Response:
    """Comment/like summary of one post (org lane read, VERSIONED endpoint)."""
    quoted = urllib.parse.quote(str(object_urn or "").strip(), safe="")
    if not quoted:
        raise LinkedInPayloadError("LinkedIn post URN is required")
    return await client.get(
        f"{LINKEDIN_REST_SOCIAL_ACTIONS_URL}/{quoted}",
        headers=rest_headers(access_token=access_token, api_version=api_version),
    )


async def list_comments(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_version: str,
    object_urn: str,
    count: int = 25,
    start: int = 0,
) -> httpx.Response:
    """Comments on one post (org lane read, VERSIONED endpoint)."""
    quoted = urllib.parse.quote(str(object_urn or "").strip(), safe="")
    if not quoted:
        raise LinkedInPayloadError("LinkedIn post URN is required")
    return await client.get(
        f"{LINKEDIN_REST_SOCIAL_ACTIONS_URL}/{quoted}/comments",
        params={
            "count": max(1, min(int(count or 25), 100)),
            "start": max(0, int(start or 0)),
        },
        headers=rest_headers(access_token=access_token, api_version=api_version),
    )


__all__ = [
    "ARTICLE_DESCRIPTION_MAX_CHARS",
    "ARTICLE_TITLE_MAX_CHARS",
    "DEFAULT_LINKEDIN_API_VERSION",
    "LINKEDIN_IMAGES_URL",
    "LINKEDIN_ORGANIZATION_ACLS_URL",
    "LINKEDIN_ORGANIZATIONS_URL",
    "LINKEDIN_POSTS_URL",
    "LINKEDIN_POST_MAX_CHARS",
    "LINKEDIN_REST_BASE",
    "LINKEDIN_REST_SOCIAL_ACTIONS_URL",
    "LINKEDIN_SOCIAL_ACTIONS_URL",
    "MAX_GIF_FRAMES",
    "MAX_IMAGE_PIXELS",
    "MULTI_IMAGE_MAX",
    "MULTI_IMAGE_MIN",
    "SUPPORTED_IMAGE_MIME",
    "LINKEDIN_DOCUMENTS_URL",
    "LINKEDIN_VIDEOS_URL",
    "MAX_DOCUMENT_BYTES",
    "MAX_VIDEO_BYTES",
    "MIN_VIDEO_BYTES",
    "POLL_DURATIONS",
    "POLL_OPTIONS_MAX",
    "POLL_OPTIONS_MIN",
    "POLL_OPTION_MAX_CHARS",
    "POLL_QUESTION_MAX_CHARS",
    "SUPPORTED_DOCUMENT_MIME",
    "SUPPORTED_VIDEO_MIME",
    "LinkedInPayloadError",
    "build_article_content",
    "build_comment_body",
    "build_commentary_patch",
    "build_document_media",
    "build_image_upload_body",
    "build_poll_content",
    "build_post_body",
    "build_video_media",
    "etag_from_response",
    "finalize_video_upload",
    "initialize_document_upload",
    "initialize_video_upload",
    "parse_document_upload_init",
    "parse_video_upload_init",
    "upload_video_part",
    "build_post_content",
    "comment_urn_from_body",
    "create_comment",
    "create_post",
    "created_urn_from_response",
    "delete_comment",
    "delete_post",
    "fetch_organization",
    "fetch_organization_acls",
    "fetch_social_summary",
    "initialize_image_upload",
    "legacy_headers",
    "list_comments",
    "list_posts_by_author",
    "organization_urn",
    "parse_comments_page",
    "parse_image_upload_init",
    "parse_organization_acls",
    "parse_posts_page",
    "parse_social_summary",
    "person_urn",
    "post_entity_url",
    "post_permalink",
    "rest_headers",
    "social_actions_comment_url",
    "social_actions_comments_url",
    "update_post_commentary",
    "upload_image_bytes",
    "utf16_length",
]
