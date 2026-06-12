---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/linkedin/linkedin-README.md
title: "LinkedIn SDK Integration"
summary: "Reusable LinkedIn integration mechanics for KDCube bundles: account metadata, LinkedIn OAuth, UGC Posts API for publishing content on behalf of connected users, and account settings operations."
tags: ["sdk", "integrations", "linkedin", "oauth", "ugc posts", "bundles"]
keywords: ["linkedin integration", "linkedin oauth", "linkedin post", "ugc posts api", "linkedin accounts"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/linkedin/linkedin-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/servicing-interfaces-README.md
---

# LinkedIn SDK Integration

The LinkedIn SDK integration contains reusable OAuth and publishing mechanics
that bundles can import from:

```python
from kdcube_ai_app.apps.chat.sdk.integrations import linkedin
```

The SDK owns LinkedIn protocol mechanics. The bundle still owns product policy:
who may connect accounts, how an account is exposed in UI, which agent or task
publishes on behalf of a user, and what content policy applies.

External provider setup is documented separately in
`linkedin-external-prereq-README.md`. Keep this article focused on the SDK
surface and bundle integration points.

## Package Surface

```text
kdcube_ai_app.apps.chat.sdk.integrations.linkedin
  accounts.py      account metadata, LinkedIn OAuth helpers, token storage,
                   profile fetch, UGC Posts API client (create_linkedin_post),
                   and image upload helpers (register_image_upload,
                   upload_media_binary, create_linkedin_media_post)
  delivery.py      content helpers: strip_markdown, truncate_post_text,
                   format_post_text
  settings.py      configurable account settings operations for status,
                   OAuth start/callback, disconnect, and Telegram variants
```

`__init__.py` re-exports the stable bundle-facing symbols for normal imports.

## Account Store

```python
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import LinkedInAccountStore

store = LinkedInAccountStore(storage_root, user_id=user_id, bundle_id=bundle_id)
account = await store.upsert_account_async({
    "provider": "linkedin",
    "person_id": "dE5aOhH-ap",
    "email": "user@example.com",
    "display_name": "Jane Smith",
    "status": "connected",
    "scope": ["openid", "profile", "email", "w_member_social"],
})
await store.set_tokens_async(account["account_id"], {"access_token": "...", "expires_in": 5183944})
```

`LinkedInAccountStore` stores account metadata under the bundle storage root and
stores OAuth tokens through the KDCube user-secret API under the key
`linkedin.accounts.{account_id}.tokens`. Account JSON records keep only metadata
and a `has_token` flag; access tokens do not live in account metadata files.

## Content Helpers

`delivery.py` provides utilities for preparing post text before sending it to
LinkedIn. LinkedIn text posts do not render HTML or markdown; raw syntax
characters appear verbatim.

```python
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import (
    strip_markdown,
    truncate_post_text,
    format_post_text,
)

# Strip markdown syntax (headings, bold, italic, code, lists, links, etc.)
clean = strip_markdown("## Title\n\n**Bold** and `code`")
# → "Title\n\nBold and code"

# Truncate to LinkedIn's 3000-char limit, breaking on a word boundary
short = truncate_post_text(long_text, max_chars=3000)

# Convenience: strip + truncate in one call
ready = format_post_text(markdown_text)
```

`format_post_text` is the recommended entry point. Call it on any agent-produced
text before passing it to `create_linkedin_post` or `create_linkedin_media_post`.

## Publishing Posts

`create_linkedin_post` sends a text post to the LinkedIn UGC Posts API on behalf
of a connected user:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import (
    create_linkedin_post,
    format_post_text,
)

result = await create_linkedin_post(
    access_token=access_token,
    person_id=person_id,
    text=format_post_text(agent_output),
)
# result["post_id"] → "urn:li:share:123456789"
```

The SDK constructs the UGC Posts payload with `lifecycleState: PUBLISHED`,
`shareMediaCategory: NONE`, and `MemberNetworkVisibility: PUBLIC`. The bundle
decides when to publish, which account to use, and what content to include.

## Publishing Posts with Images

`create_linkedin_media_post` publishes a post with attached images. Images must
be uploaded to LinkedIn's CDN first using a two-step register + PUT upload:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import (
    register_image_upload,
    upload_media_binary,
    create_linkedin_media_post,
    format_post_text,
)

# Step 1: register the upload slot
reg = await register_image_upload(access_token=access_token, person_id=person_id)
# reg → {"upload_url": "https://...", "asset_urn": "urn:li:digitalmediaAsset:...", "upload_headers": {...}}

# Step 2: upload binary data
image_bytes = Path("chart.png").read_bytes()
await upload_media_binary(
    upload_url=reg["upload_url"],
    data=image_bytes,
    content_type="image/png",
    extra_headers=reg["upload_headers"],
)

# Step 3: publish post with asset URN
result = await create_linkedin_media_post(
    access_token=access_token,
    person_id=person_id,
    text=format_post_text(agent_output),
    asset_urns=[reg["asset_urn"]],
    media_category="IMAGE",
)
# result["post_id"] → "urn:li:ugcPost:..."
```

Up to 4 images can be attached in one post. Supported formats: JPEG, PNG, GIF,
WebP. Maximum 5 MB per file.

### PDF Limitation

LinkedIn's Documents API (`POST /v2/documents?action=initializeUpload`) is
**not available to standard OAuth apps**. It requires LinkedIn Marketing API
partner access. Calls from apps with only `w_member_social` scope return 404
`No virtual resource found`.

To share document content on LinkedIn, generate PNG or JPEG images from the
document and attach them as images instead.

## Account Settings Operations

`settings.py` provides reusable account-management operations for bundle UIs
and Telegram Mini Apps. The bundle supplies its storage root, user resolution,
and optional Telegram identity resolver:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import settings as linkedin_settings

linkedin_settings.configure_linkedin_settings(
    storage_root_or_error=storage_root,
    target_user_id=target_user_id,
    resolve_identity=telegram_widget_auth.resolve_identity,
    bundle_id="my.bundle@1-0",
)

payload = await linkedin_settings.status(entrypoint, user_id="user-a")
oauth = await linkedin_settings.start_oauth(entrypoint, request=request)
```

The operations cover:

- `status(...)` — async operation that returns enabled/configured flags, configuration_missing list,
  and the accounts list with `has_token` for each account
- `start_oauth(...)` — async operation that builds the LinkedIn authorization URL with HMAC-signed
  state, returns `authorize_url` for the browser
- `callback(...)` — async handler for the OAuth redirect: exchanges code for
  token, fetches profile, upserts account record; returns an HTML done page
- `disconnect(...)` — async operation that removes account record and its token secret
- `telegram_status(...)`, `telegram_start_oauth(...)`, `telegram_disconnect(...)` —
  async Telegram Web App variants that first resolve Telegram `initData`

Typical bundle shape:

```text
bundle endpoint / widget action
  -> linkedin_settings.<operation>(entrypoint, ...)
       -> target_user_id hook supplied by bundle
       -> LinkedInAccountStore from SDK
       -> LinkedIn OAuth mechanics from SDK
```

This keeps UI routing and role policy in the bundle while the account mechanics
stay in the SDK.

## OAuth Flow

The SDK provides LinkedIn OAuth URL construction, callback code exchange, and
profile fetch:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import (
    build_linkedin_authorize_url,
    exchange_linkedin_code,
    fetch_linkedin_profile,
)
```

The bundle supplies the `entrypoint` so descriptor values and bundle-scoped
secrets can be resolved:

```text
integrations.linkedin.client_id
integrations.linkedin.client_secret      (secret)
integrations.linkedin.scopes
integrations.linkedin.oauth.public_base_url
integrations.linkedin.oauth.redirect_uri
integrations.linkedin.oauth_state_secret (secret)
```

OAuth state is HMAC-signed with `oauth_state_secret` and stored per-user under
the bundle storage root. The callback handler verifies the signature and
consumes the state before proceeding.

## OAuth Callback URL

The callback route must be registered in the LinkedIn Developer App as an
authorized redirect URI. For a bundle public operation alias named
`linkedin_oauth_callback`, the route shape is:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/linkedin_oauth_callback
```

## LinkedIn Scopes

Minimum scopes for profile identification and posting:

```text
openid
profile
email
w_member_social
```

`w_member_social` is required for `create_linkedin_post`. Without it the UGC
Posts API call returns a 403.

## Bundle Boundary

The SDK owns:

- LinkedIn OAuth URL construction and callback code exchange
- HMAC-signed OAuth state creation and verification
- LinkedIn profile fetch via the userinfo endpoint
- `LinkedInAccountStore` for file-backed account metadata and user-secret token
  storage
- UGC Posts API client (`create_linkedin_post`)
- reusable account settings operations

The bundle owns:

- user/admin policy and UI routes
- which conversation or task triggered the publish action
- account selection policy when multiple accounts are connected
- content and publish timing policy
- Telegram, web widget, or other product-specific presentation
