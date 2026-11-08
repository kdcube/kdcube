---
id: sdk.integrations.public-reachability
title: "Public Reachability Of Provider-Fetched Files"
summary: "Check that a configured public origin serves staged files to an anonymous provider before writing their URLs into a document."
tags: [sdk, integrations, provider-fetch, docs, public-origin]
keywords: [file_ref, provider_fetch, public_base_url, deployment_origin_not_public, deployment_origin_unreachable, tunnel]
see_also:
  - repo:kdcube/app/ai-app/docs/sdk/integrations/google/google-README.md
---

# Public Reachability Of Provider-Fetched Files

Some providers fetch an image themselves after a write receives its URL. A
deployment can hold valid image bytes while its public tunnel serves a warning
page or login screen. The SDK checks the minted URL before the write so the
agent receives a diagnosis the runtime administrator can act on.

Docs `embed_image` with `file_ref` stages an authorized copy and mints a
short-lived provider-fetch URL. It probes that URL as an anonymous browser-like
caller, expecting HTTP 200 and an `image/` content type. Only a matching
response proceeds to the provider write. The existing file validation,
authorization and staging cleanup still apply.

Configure the serving origin through the `kdcube-services` property
`integrations.provider_fetch.public_base_url` in the bundles descriptor. The
origin must serve the signed file URL publicly over HTTPS without an
interstitial or interactive login. A probe confirms the response from this
deployment's network location; provider-side reachability still needs a live
check. Other provider-file consumers, including Slides, can use this helper.

## Deployment Refusals

| Code | Meaning |
| --- | --- |
| `deployment_origin_not_public` | The origin answered with an unexpected status or media type, such as HTTP 200 `text/html` from a tunnel warning page. |
| `deployment_origin_unreachable` | The origin could not be fetched, including connection, timeout or redirect failures. |

Both return HTTP 502 with `details.where: deployment_config` and a `purpose`
such as `docs.embed_image`. An unexpected response includes `origin_status`
and `origin_media_type`. An unreachable response includes only the exception
class in `error`. The refusal asks the runtime administrator to fix the origin,
tunnel or proxy. No provider write occurs. Capability URLs, response bodies and
raw network exception messages are excluded from the refusal.

These shared codes replace Docs' former `docs_image_origin_not_public` and
`docs_image_origin_unreachable` codes. Missing hosting configuration retains
`docs_image_hosting_unavailable`.

## SDK Contract

`kdcube_ai_app.apps.chat.sdk.integrations.public_reachability` owns the probe
and the shared deployment refusal:

```python
result = await probe_public_url(
    minted_url,
    caller="browser",
    expect=PublicURLExpectation(status=200, media_prefix="image/"),
)
error = deployment_origin_error(result, purpose="docs.embed_image")
if error is not None:
    return error.as_response(request, provider=provider_identity)
```

`result.kind` is `reachable`, `answered_by_other` or `unreachable`. The probe
uses a fresh client with no caller credentials, follows redirects, and checks
status and media type without reading the image body. An optional byte marker
is searched within the first 16 KiB. `caller="server"` selects a server user
agent; it currently has no webhook or OAuth consumer. Provider-file downloads
can use `public_fetch_headers("browser")` to retain the same caller shape.

The namespace checks the URL after the provider-fetch factory mints it. This
keeps the factory responsible for URL creation and lets each consumer choose
its expected media type and purpose. The probe accepts deployment-owned URLs;
foreign URLs continue through the consuming integration's SSRF, size and
content validation.
