# `connections` SDK contracts

The transport-neutral contract for the `connections` namespace: list a user's
connectable providers, fetch the user's access token for a provider, and drive
OAuth. A bundle implements the provider; any bundle consumes it through the
typed client over the local (in-process) or API (HTTP) transport.

The same package also exposes `IdentityLinksClient`, the app-to-app SDK client
for asking Connection Hub to resolve/link external identities such as Telegram.
Browser widgets should not build another app's public URL directly. They should
call their own app, and that app should call `IdentityLinksClient`.

For the full design (two layers, registry, user-scoped tokens, pins) see
[`docs/sdk/integrations/connections-README.md`](../../../../../../../../docs/sdk/integrations/connections-README.md).

## Operations

| Operation              | Purpose                                                        |
| ---------------------- | ------------------------------------------------------------- |
| `connection.catalog`   | List registered providers + the user's connected state (UI).  |
| `connection.status`    | Status for one provider.                                      |
| `connection.get_token` | Return the access token for (user, provider, optional account). The consumer op. |
| `connection.disconnect`| Disconnect an account.                                        |
| `oauth.start`          | Begin OAuth; returns `{authorize_url}`.                       |

The OAuth **callback** is a browser redirect handled by an HTTP route alias on
the implementing bundle, not a named-service operation.

## Consuming it

```python
from kdcube_ai_app.apps.chat.sdk.solutions.connections import ConnectionsClient, IdentityLinksClient

connections = ConnectionsClient(registry)            # local transport
# or: ConnectionsClient(registry, transport="api")   # HTTP transport
# or: ConnectionsClient(client=named_service_client)  # reuse an existing client

token = await connections.get_token("slack")
if token is not None:
    use(token.access_token)

entries = await connections.catalog()                # list[CatalogEntry]
info = await connections.status("slack")             # dict
start = await connections.start_oauth("slack")       # {"authorize_url": ...}
await connections.disconnect("slack", account_id)
```

For identity links from an app surface:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.connections import IdentityLinksClient, request_origin

identity_links = IdentityLinksClient(self)
result = await identity_links.telegram_link_start(
    telegram_init_data=telegram_init_data,
    public_origin=request_origin(request),
)
```

Configure the Connection Hub app id on the calling app:

```yaml
integrations:
  connection_hub:
    bundle_id: connection-hub@1-0
```

## Implementing it (in a bundle)

Subclass `ConnectionsProviderBase`, decorate with `named_service_provider`
passing `build_connection_operations(transports)`, and implement the abstract
hooks (`get_token`, `list_catalog`, `status`, `disconnect`, `start_oauth`)
against your chosen storage. The storage choice stays in the bundle.
