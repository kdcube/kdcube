---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/mail-named-service-README.md
title: "Mail Named Service Over MCP"
summary: "Expose user-connected mail accounts as one provider-neutral `mail` named-service namespace, so external agents can list accounts, search/read messages, download attachments, send, and forward through delegated consent."
status: active
tags: ["recipes", "connections", "connection-hub", "named-services", "mcp", "mail", "gmail", "connected-accounts", "delegated-to-kdcube"]
updated_at: 2026-07-06
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/google-gmail-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/named-services-mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/delegate-kdcube-service-to-external-client-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/mail/named_service.py
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/google/gmail_tools.py
---
# Mail Named Service Over MCP

Use this recipe when KDCube should expose a user's connected mail accounts to an
external agent through the generic named-services MCP surface.

The namespace is intentionally **mail**, not `gmail`. Gmail is one provider
behind the namespace. Later iCloud, Yahoo, IMAP/SMTP, or other mail providers
can share the same agent contract.

```text
Claude or another MCP client
  -> connects to kdcube-services@1-0/public/mcp/named_services
  -> user approves KDCube delegated grants: mail:read and/or mail:send
  -> agent calls namespace=mail
  -> KDCube resolves the current user's connected Gmail/iCloud/Yahoo accounts
  -> provider tool enforces provider-side claims such as gmail:read/gmail:send
```

## What Exists Now

Implemented now:

- `mail` named-service namespace in SDK integrations;
- Gmail transport through `kdcube_ai_app.apps.chat.sdk.integrations.google.gmail_tools`;
- `kdcube-services@1-0` registers the mail provider alongside `conv`;
- Connection Hub delegated credentials can grant `mail:read` and `mail:send`;
- the existing `named_services` MCP surface can expose the namespace to Claude.

Reserved in the schema, but not implemented yet:

- iCloud mail as the same `mail` namespace provider;
- Yahoo mail as the same `mail` namespace provider;
- a dedicated mail MCP server separate from generic named services.

## Object Refs

The agent never receives provider tokens. It receives refs.

```text
Connected account:
  mail:<provider>:<account_id>
  mail:gmail:acc_123

Message:
  mail:<provider>:<account_id>:message:<message_id>
  mail:gmail:acc_123:message:18f00...

Attachment:
  mail:<provider>:<account_id>:attachment:<message_id>:<attachment_id>
  mail:gmail:acc_123:attachment:18f00...:att_1
```

Multiple connected accounts are normal. If the user connects two Gmail accounts
and one iCloud account, the namespace should eventually list three account refs:

```text
mail:gmail:personal
mail:gmail:work
mail:icloud:icloud-main
```

Today, Gmail search fans out across every connected Gmail account that already
has `gmail:read`, unless the caller passes `account_id`.

## Operations

The named-service operations are provider-neutral.

| Operation | Tool | Requires KDCube delegated grant | Provider claim used today |
| --- | --- | --- | --- |
| `provider.about` | `named_services_about` | `mail:read` | none |
| `provider.capabilities` | `named_services_capabilities` | `mail:read` | none |
| `object.list` | `named_services_call` or list-capable wrapper | `mail:read` | none |
| `object.schema` | `named_services_schema` | `mail:read` | none |
| `object.search` | `named_services_search` | `mail:read` | `gmail:read` |
| `object.get` | `named_services_get` | `mail:read` | `gmail:read` |
| `object.action: download_attachments` | `named_services_action` | `mail:read` + `mail:send` at the current MCP action boundary | `gmail:read` |
| `object.action: send` | `named_services_action` | `mail:read` + `mail:send` at the current MCP action boundary | `gmail:send` |
| `object.action: forward` | `named_services_action` | `mail:read` + `mail:send` at the current MCP action boundary | `gmail:read` + `gmail:send` |

The current generic MCP action tool is operation-level. That is why the
delegated MCP boundary protects `object.action` with both `mail:read` and
`mail:send`. The provider-side Gmail resolver still checks the exact provider
claim needed for the action before calling Gmail.

## Consent Layers

There are two separate consent layers, and layer 1 applies to EVERY agent —
external or hosted inside a KDCube app.

```text
Layer 1: agent -> KDCube               (Delegated BY KDCube)
  User approves what THIS AGENT may ask KDCube to do.
  Grants: mail:read, mail:send (+ the entry grant named_services:use).
  External agent (Claude): the OAuth connector consent.
  In-platform agent: a per-agent grant keyed to kdcube-agent:<app>:<agent> —
    a native mail tool call on a governed deployment checks it at the attempt
    and, when missing, raises the one-click agent-grant demand in chat.

Layer 2: KDCube -> external mail provider   (Delegated TO KDCube)
  User connects a provider account to KDCube.
  Claims: gmail:read, gmail:send. Checked at every call.
```

Connecting a mail account answers only layer 2 — it does not by itself
authorize any particular agent. Revoking either layer stops the tool
immediately. Identity model and the grant flow:
[Agents Acting On Behalf Of The User](../../../sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).

If Claude has `mail:read` but the current KDCube user has not connected Gmail
with `gmail:read`, the provider returns the structured consent error
(`needs_connected_account_consent` with `reason`, `retry_hint`, labeled
`candidates`, and `connection_hub_url` — see
[the consent-error story](../../kdcube_for_agents/named-services-mcp-README.md#the-consent-error-story)).
The fix is not to reconnect Claude; the user must connect or upgrade the Gmail
account in Connection Hub.

If the user already connected Gmail but the provider token expired, KDCube tries
to refresh the Google access token before the Gmail transport is called. The
Connections widget exposes the non-secret credential health:

```text
Connected
  usable credential is present

Refreshes automatically
  access token is expired/near expiry, but a refresh token exists

Reconnect required
  credential is missing, revoked, or expired without a refresh token
```

Only **Reconnect required** means the user must run the Gmail connect flow again.
An expired access token with a stored refresh token should not require user
action.

## Connection Hub Configuration

The delegated credential capabilities make the grants visible during external
client consent:

```yaml
connections:
  delegated_credentials:
    oauth:
      capabilities:
        - grant: mail:read
          label: Read connected mail
          description: Search and read messages and attachments from mail accounts connected to KDCube.
          delegable_roles:
            - kdcube:role:registered
            - kdcube:role:paid
            - kdcube:role:privileged
            - kdcube:role:super-admin
          delegable_permissions:
            - mail:read
        - grant: mail:send
          label: Send connected mail
          description: Send or forward messages through mail accounts connected to KDCube.
          delegable_roles:
            - kdcube:role:registered
            - kdcube:role:paid
            - kdcube:role:privileged
            - kdcube:role:super-admin
          delegable_permissions:
            - mail:send
```

The named-services MCP resource registers the namespace boundary:

```yaml
connections:
  delegated_credentials:
    oauth:
      resources:
        - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
          label: KDCube named services MCP
          tools:
            named_services_list:
              grants: [named_services:use]
            named_services_about:
              grants: [named_services:use]
            named_services_capabilities:
              grants: [named_services:use]
            named_services_schema:
              grants: [named_services:use]
            named_services_search:
              grants: [named_services:use]
            named_services_get:
              grants: [named_services:use]
            named_services_action:
              grants: [named_services:use]
            named_services_call:
              grants: [named_services:use]
          named_services:
            namespaces:
              mail:
                label: Mail
                description: Search, read, download attachments from, send, and forward messages through connected mail accounts.
                authority_id: delegated_client
                tools:
                  about:
                    operation: provider.about
                    grants: [mail:read]
                  capabilities:
                    operation: provider.capabilities
                    grants: [mail:read]
                  list:
                    operation: object.list
                    grants: [mail:read]
                  schema:
                    operation: object.schema
                    grants: [mail:read]
                  search:
                    operation: object.search
                    grants: [mail:read]
                  get:
                    operation: object.get
                    grants: [mail:read]
                  action:
                    operation: object.action
                    operations:
                      object.action:
                        grants: [mail:read, mail:send]
                  call:
                    operations:
                      provider.about:
                        grants: [mail:read]
                      provider.capabilities:
                        grants: [mail:read]
                      object.list:
                        grants: [mail:read]
                      object.schema:
                        grants: [mail:read]
                      object.search:
                        grants: [mail:read]
                      object.get:
                        grants: [mail:read]
                      object.action:
                        grants: [mail:read, mail:send]
```

The provider account side is configured separately under
`connections.delegated_to_kdcube`. See
[Google Gmail Integration](google-gmail-README.md) for the full Google OAuth
client, callback URL, scope, and secret configuration.

## Agent Usage

Connect the external agent to the generic named-services MCP URL:

```text
https://<runtime>/api/integrations/bundles/<tenant>/<project>/kdcube-services@1-0/public/mcp/named_services
```

Useful prompt for Claude:

```text
Use the KDCube named services connector. First list namespaces. Then use the
mail namespace to list connected mail accounts, search for "Anthropic receipt",
read the most relevant message, and tell me whether it has attachments.
```

For attachments:

```text
Use the mail namespace. Search for "invoice". Read the top message. If it has
attachments, run the mail download_attachments action and return the KDCube file
refs only.
```

For sending:

```text
Use the mail namespace. List connected mail accounts. Send an email from the
work Gmail account to <recipient> with subject "<subject>" and body "<body>".
```

For forwarding:

```text
Use the mail namespace. Search mail for "<topic>", read the matching message,
then forward it to <recipient> with the note "<note>".
```

## Application Code

The provider is SDK code:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.mail import make_mail_named_service_provider

providers.append(
    make_mail_named_service_provider(
        entrypoint=self,
        bundle_id=self._named_services_bundle_id(),
    )
)
```

`kdcube-services@1-0` does only that thin registration. The mail namespace and
Gmail transport live in SDK integration modules:

- `kdcube_ai_app.apps.chat.sdk.integrations.mail.named_service`
- `kdcube_ai_app.apps.chat.sdk.integrations.google.gmail_tools`

## Testing Checklist

1. In Connection Hub, connect Gmail with `gmail:read`.
2. Connect Claude to `kdcube-services@1-0/public/mcp/named_services`.
3. During consent, approve named services plus `mail:read`.
4. Ask Claude to list namespaces and inspect `mail`.
5. Search mail.
6. Read a message by `mail:gmail:<account_id>:message:<message_id>`.
7. Connect or upgrade Gmail with `gmail:send`.
8. Reconnect Claude and approve `mail:send` if it was not approved initially.
9. Test send or forward.

If the MCP call is accepted but the provider returns
`needs_connected_account_consent`, Connection Hub delegated MCP consent is
working; the missing piece is the user-to-provider connected account claim, and
`error.details.reason` says whether to connect, approve a claim, reconnect, or
pick an account.
