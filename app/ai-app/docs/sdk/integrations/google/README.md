---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/google/README.md
title: "Google Integration Docs"
summary: "Index for the KDCube Google SDK integration docs."
tags: ["sdk", "integrations", "google", "gmail", "sheets", "docs"]
keywords: ["google integration", "gmail integration", "sheets integration", "google docs integration", "google oauth adapter", "provider scopes"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/google/google-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/google-service-README.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/frontend/application/integrations/google.md
---

# Google Integration Docs

One Google OAuth client, one `google` provider, and one `gmail` connector app
serve Gmail, Sheets, Docs, and another Google service added the same way. Each
service adds its claims, scopes, and tools.

Use these docs in this order:

- [Google SDK Integration](google-README.md) - the SDK mechanics: the
  `google.oauth` adapter, connected-account credential resolution, and the scope
  machinery (claim -> provider-scope union, the read-write-supersedes-read-only
  collapse, and the Gmail, Sheets, and Docs execution paths.
- [Google Services Through KDCube (Gmail, Sheets, Docs)](../../../recipes/connections/integrations/google-service-README.md) -
  the end-to-end deployment recipe: provider claims, per-service wiring, connect,
  grant, and verification.
- [Public Reachability Of Provider-Fetched Files](../public-reachability-README.md) -
  staged image `file_ref` serving, public-origin configuration and deployment refusals.
- [Connected Presentations](slides-README.md) - targetless imports, slide edits,
  and mutation diagnostics with deck-wide counts.

The external operator setup (Google Cloud project, OAuth client, redirect URIs,
and per-service API enablement) lives in the bundle-local
[Connection Hub Google setup](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/frontend/application/integrations/google.md).
