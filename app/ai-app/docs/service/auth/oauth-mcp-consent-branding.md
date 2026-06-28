---
id: repo:kdcube-ai-app/app/ai-app/docs/service/auth/oauth-mcp-consent-branding.md
title: "Branding the MCP Authorization Screen"
summary: "How to configure the product name shown on the OAuth MCP consent screen through auth.oauth_mcp.brand."
status: active
tags: ["service", "auth", "oauth", "mcp", "branding", "descriptor"]
updated_at: 2026-06-27
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/oauth-mcp-integration-access-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
---
# Branding the MCP Authorization Screen

How to put your own product name on the OAuth/MCP consent screen, with no code
change and no image rebuild.

## What the consent screen is

When an admin connects an MCP client (for example, Claude) to your deployment,
KDCube acts as a **self-hosted OAuth2 authorization server**. Before the client
is granted access, the admin is sent to `/oauth/authorize`, which renders a
**consent screen**: it shows the requesting client, the redirect target, the
requested scope(s), and a per-capability tool selection, then asks the admin to
Approve or Deny.

By default that page is branded **KDCube**. If your deployment ships under a
different product name, you can change the brand shown to the admin.

## The one knob: `auth.oauth_mcp.brand`

Set `brand` under `auth.oauth_mcp` in your `assembly.yaml` descriptor to your
product name:

```yaml
auth:
  oauth_mcp:
    enabled: true
    issuer: "https://mcp.example.com"
    brand: "Acme AI"
```

That is the only field involved. The other `auth.oauth_mcp` settings (`issuer`,
`public_clients`, `dynamic_client_registration`, ...) are documented in
[`oauth-mcp-integration-access-README.md`](./oauth-mcp-integration-access-README.md).

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

## Default behavior

If `brand` is unset or empty, the consent screen uses **KDCube**.

## No code change or image rebuild needed

`brand` is descriptor configuration, not code. Edit `assembly.yaml`, then let the
change be picked up on the next config sync / service restart — no rebuild of the
application image is required.
