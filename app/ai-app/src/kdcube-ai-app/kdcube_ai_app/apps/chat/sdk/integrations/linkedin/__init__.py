"""LinkedIn integration.

Two independent layers share this package:

- ``accounts`` / ``settings``: bundle-owned OAuth over the legacy ``/v2`` API.
- ``rest_api`` / ``tools`` / ``named_service``: Connection Hub connected
  accounts over the versioned ``/rest`` API.
"""

from __future__ import annotations

from .named_service import (
    ACTION_ADD_COMMENT,
    ACTION_DISCARD_UPLOAD,
    ACTION_PUBLISH_IMAGE_POST,
    ACTION_PUBLISH_POST,
    ACTION_REQUEST_UPLOAD,
    LINKEDIN_ACTIONS,
    LINKEDIN_NAMESPACE,
    LINKEDIN_SCHEMA_PROJECTION,
    LinkedInNamedServiceProvider,
    linkedin_named_service_spec,
    make_linkedin_named_service_provider,
    parse_linkedin_ref,
)
from .tools import (
    LINKEDIN_POST_CLAIM,
    LINKEDIN_PROFILE_CLAIM,
    LINKEDIN_PROVIDER_ID,
    LinkedInTools,
)
from .delivery import (
    LINKEDIN_POST_MAX_CHARS,
    format_post_text,
    strip_markdown,
    truncate_post_text,
)
from .accounts import (
    DEFAULT_LINKEDIN_SCOPES,
    LINKEDIN_ASSETS_URL,
    LINKEDIN_AUTH_URL,
    LINKEDIN_DOCUMENT_EXTENSIONS,
    LINKEDIN_DOCUMENTS_URL,
    LINKEDIN_IMAGE_EXTENSIONS,
    LINKEDIN_SOCIAL_ACTIONS_URL,
    LINKEDIN_UGC_POSTS_URL,
    LINKEDIN_USERINFO_URL,
    LinkedInAccountStore,
    ProviderHttpError,
    add_linkedin_comment,
    build_linkedin_authorize_url,
    callback_url,
    create_linkedin_media_post,
    create_linkedin_post,
    exchange_linkedin_code,
    fetch_linkedin_profile,
    linkedin_client_id,
    linkedin_client_secret,
    linkedin_scopes,
    oauth_state_secret,
    register_document_upload,
    register_image_upload,
    upload_media_binary,
)

__all__ = [
    "ACTION_ADD_COMMENT",
    "ACTION_DISCARD_UPLOAD",
    "ACTION_PUBLISH_IMAGE_POST",
    "ACTION_PUBLISH_POST",
    "ACTION_REQUEST_UPLOAD",
    "LINKEDIN_ACTIONS",
    "LINKEDIN_NAMESPACE",
    "LINKEDIN_SCHEMA_PROJECTION",
    "LINKEDIN_POST_CLAIM",
    "LINKEDIN_PROFILE_CLAIM",
    "LINKEDIN_PROVIDER_ID",
    "LinkedInNamedServiceProvider",
    "LinkedInTools",
    "linkedin_named_service_spec",
    "make_linkedin_named_service_provider",
    "parse_linkedin_ref",
    "LINKEDIN_POST_MAX_CHARS",
    "format_post_text",
    "strip_markdown",
    "truncate_post_text",
    "DEFAULT_LINKEDIN_SCOPES",
    "LINKEDIN_ASSETS_URL",
    "LINKEDIN_DOCUMENT_EXTENSIONS",
    "LINKEDIN_DOCUMENTS_URL",
    "LINKEDIN_IMAGE_EXTENSIONS",
    "LINKEDIN_SOCIAL_ACTIONS_URL",
    "add_linkedin_comment",
    "create_linkedin_media_post",
    "register_document_upload",
    "register_image_upload",
    "upload_media_binary",
    "LINKEDIN_AUTH_URL",
    "LINKEDIN_UGC_POSTS_URL",
    "LINKEDIN_USERINFO_URL",
    "LinkedInAccountStore",
    "ProviderHttpError",
    "build_linkedin_authorize_url",
    "callback_url",
    "create_linkedin_post",
    "exchange_linkedin_code",
    "fetch_linkedin_profile",
    "linkedin_client_id",
    "linkedin_client_secret",
    "linkedin_scopes",
    "oauth_state_secret",
]
