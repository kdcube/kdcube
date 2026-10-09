---
id: kdcube-services@1-0/docs/journal/2026-10-09-provider-fetch-signed-media-type.md
title: "Provider fetch serves the signed media type"
summary: "The public provider_fetch_download route serves the media type the staging action measured and signed, with nosniff, and serves anything outside a display-only set as an attachment."
status: active
tags: ["kdcube-services", "journal", "signed-files", "google-docs", "security"]
keywords: ["provider_fetch_download", "media type", "nosniff", "embed_image", "staged file"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube-services@1-0/interface/README.md
---

# Provider fetch serves the signed media type

**2026-10-09**

`provider_fetch_download` is the public route a provider uses to fetch a file
this deployment staged for one call; today Google Docs fetches an image for
`embed_image` through it. The provider arrives with no identity, so the route
is reachable by anyone holding the URL for its short lifetime, on the
platform's own origin.

The served media type now comes from the token. The staging action measures
the bytes, signs the detected type into the one-fetch token next to the staged
ref (`mint_file_download_token(media_type=...)`), and the route serves exactly
that type. A staged name never decides it: an image measures as PNG even with
other bytes after its end, so a name such as `page.html` would otherwise have
served those bytes as `text/html`.

The route keeps one rule for every caller: it always sends
`X-Content-Type-Options: nosniff`, and any type outside a display-only set
(PNG, JPEG, GIF, WebP, PDF), or a token without a type, is served as an
attachment. This keeps the route general for other providers and their types
while it never renders active content on this origin.

The Docs embed flow stages its served copy under a name derived from the
measured format (`image.png`, `image.jpg`, `image.gif`) and removes both that
copy and a caller's staged source when the write returns. Google Docs accepts
PNG, JPEG and GIF for an inline image, which is what the flow measures for.
