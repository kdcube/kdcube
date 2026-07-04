---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-consent-branding-README.md
title: "Branding the MCP Authorization Screen"
summary: "How to configure the product name shown on the current OAuth delegated credential delegated-credential consent screen through the Connection Hub bundle config."
status: active
tags: ["service", "auth", "oauth", "mcp", "branding", "descriptor"]
updated_at: 2026-06-27
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegated-credential-protocol-adapters-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
---
# Branding the MCP Authorization Screen

How to put your own product name on the OAuth delegated credential consent screen, with no code
change and no image rebuild.

## What the consent screen is

When an admin connects an MCP client (for example, Claude) to your deployment,
KDCube acts as a **self-hosted OAuth2 authorization server**. Before the client
is granted access, the admin is sent to Connection Hub
`/public/oauth/authorize`, which renders a
**consent screen**: it shows the requesting client, the redirect target, the
requested scope(s), and a per-capability tool selection, then asks the admin to
Approve or Deny.

By default that page is branded **KDCube**. If your deployment ships under a
different product name, you can change the brand shown to the admin.

## Built-In Consent Branding

Set `brand` under `connections.delegated_credentials.oauth` in the
`connection-hub@1-0` bundle config to your product name:

```yaml
bundles:
  items:
    - id: "connection-hub@1-0"
      config:
        connections:
          delegated_credentials:
            oauth:
              enabled: true
              brand: "Acme AI"
```

The other `connections.delegated_credentials.oauth` settings (`issuer`,
`public_clients`, `dynamic_client_registration`, `consent_ui`, ...) are documented in
[`oauth-delegated-credential-protocol-adapter-README.md`](./oauth-delegated-credential-protocol-adapter-README.md).

## Bundle-Hosted Custom Consent

If a product needs a fully custom consent layout, configure the platform
authority provider with a consent entrypoint and point the OAuth adapter at it:

```yaml
bundles:
  items:
    - id: "connection-hub@1-0"
      config:
        authority_registry:
          authorities:
            kdcube.platform:
              platform: true
              providers:
                product_google_session:
                  type: bundle_session_login
                  entrypoints:
                    login:
                      bundle_id: product@1-0
                      route: public
                      operation: platform_login
                    session_issue:
                      bundle_id: product@1-0
                      route: public
                      operation: auth_google_session
                    consent:
                      bundle_id: product@1-0
                      route: public
                      operation: delegated_consent

        connections:
          delegated_credentials:
            oauth:
              enabled: true
              brand: "Acme AI"
              consent_ui:
                authority_ref:
                  authority_id: kdcube.platform
                  provider_id: product_google_session
                  entrypoint: consent
```

The custom consent operation renders the page. Connection Hub remains the
security boundary: approve/deny still posts to the Connection Hub consent
endpoint, where CSRF, selected grants/tools, authorization-code creation, and
token issuance are enforced.

The custom consent operation receives a POST payload like:

```json
{
  "request": {
    "client_id": "claude",
    "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
    "response_type": "code",
    "scope": "records:read",
    "scopes": ["records:read"],
    "resource": "https://runtime.example.test/.../public/mcp/example",
    "state": "...",
    "code_challenge": "...",
    "code_challenge_method": "S256"
  },
  "issuer": "https://runtime.example.test/api/integrations/bundles/.../connection-hub@1-0/public/oauth",
  "csrf_token": "...",
  "trusted": true,
  "brand": "Acme AI",
  "form_action": "/api/integrations/bundles/.../connection-hub@1-0/public/oauth/authorize/consent",
  "grantor_subject": "google:...",
  "grantor_label": "owner@example.com",
  "signout_action": "/api/integrations/bundles/.../connection-hub@1-0/public/oauth/logout",
  "return_to": "/api/integrations/bundles/.../connection-hub@1-0/public/oauth/authorize?...",
  "platform_grants": [
    {"grant": "records:read", "label": "Read records", "description": "..."}
  ],
  "tools": [
    {"name": "records_export", "label": "Export records", "description": "...", "grants": ["records:read"]}
  ]
}
```

It should return either an HTML string or a mapping with `html`. Its form must
POST to `form_action` and include the same OAuth hidden fields, `csrf_token`,
selected `platform_grants`, selected `tools`, and `decision=approve|deny`.

## What it changes

Setting `brand` updates the operator-visible branding on the consent page:

- the page **title** (`Authorize MCP connection · Acme AI`),
- the **heading** (`Authorize an MCP connection to Acme AI`),
- the **brand mark** in the header (the name and its monogram tile — initials
  derived from your brand name).

## What it does NOT change

- The **"Powered by KDCube"** footer attribution (linking to
  <https://kdcube.tech/>) is platform attribution and stays as-is, independent of
  your deployment brand.
- It does not affect any security behavior: the client id, redirect URL, scopes,
  pre-registered/newly-registered badges, and tool selection are unchanged.
- A bundle-hosted custom consent layout may change the presentation, but not the
  final authorization decision contract.

## Default behavior

If `brand` is unset or empty, the consent screen uses **KDCube**.

## No code change or image rebuild needed

`brand` is descriptor configuration, not code. Edit the Connection Hub entry in
`bundles.yaml`, then let the change be picked up on the next config sync /
service restart — no rebuild of the application image is required.
