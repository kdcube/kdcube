---
id: versatile@2026-03-31-13-36-interface
title: "Versatile Reference Bundle Interface Contract"
summary: "Swagger-style REST contract and frontend integration notes for versatile@2026-03-31-13-36."
status: "active"
---

# Versatile Reference Bundle Interface Contract

Use this directory as the frontend/API contract for the versatile reference
bundle.

- OpenAPI contract: [versatile.openapi.yaml](./versatile.openapi.yaml)
- Product/design notes: [../docs/design](../docs/design)
- Bundle maintainer journal: [../docs/journal/journal.md](../docs/journal/journal.md)

## Base Paths

The bundle is served by KDCube under:

```text
/api/integrations/bundles/{tenant}/{project}/versatile@2026-03-31-13-36
```

KDCube control-plane widget APIs use:

```text
/api/integrations/bundles/{tenant}/{project}/versatile@2026-03-31-13-36/operations/{alias}
```

The KDCube control-plane React widget entrypoint is:

```text
/api/integrations/bundles/{tenant}/{project}/versatile@2026-03-31-13-36/widgets/versatile_webapp
```

Telegram Mini App APIs use:

```text
/api/integrations/bundles/{tenant}/{project}/versatile@2026-03-31-13-36/public/{telegram_alias}
```

The Telegram Mini App React entrypoint is:

```text
/api/integrations/bundles/{tenant}/{project}/versatile@2026-03-31-13-36/public/widgets/versatile_webapp
```

Subpaths are supported by the static widget route, for example:

```text
/api/integrations/bundles/{tenant}/{project}/versatile@2026-03-31-13-36/public/widgets/versatile_webapp/chats
```

## Request Envelope

Use the endpoint method declared by the OpenAPI contract. The auth surface does
not decide the HTTP method: read operations are `GET` with query parameters,
and mutation/start-action operations are `POST` with a `{ "data": { ... } }`
body.

All bundle `POST` operation/public calls must send payload fields under `data`:

```json
{
  "data": {
    "conversation_id": "conv_123",
    "delete_history": true
  }
}
```

All bundle `GET` operation/public calls use query parameters. Telegram Mini App
`GET` calls still send raw Telegram initData in the `X-Telegram-Init-Data`
header.

Responses are wrapped by the KDCube platform. The operation result is under the
field named exactly like the alias:

```json
{
  "status": "ok",
  "tenant": "demo-tenant",
  "project": "demo-project",
  "bundle_id": "versatile@2026-03-31-13-36",
  "conversations_list": {
    "ok": true,
    "count": 1,
    "conversations": []
  }
}
```

Frontend code should unwrap `response[alias]`.

Fields named `user_id` in this interface are bundle user scopes. They are not
guaranteed to be KDCube account ids. For an approved Telegram user without a
KDCube mapping, the scope is `telegram_<telegram_user_id>`.

## Telegram Init Data

For Telegram Mini App APIs, pass the exact raw string from:

```ts
window.Telegram.WebApp.initData
```

Send it on every API request as:

```http
X-Telegram-Init-Data: <raw initData string>
```

Do not send `initDataUnsafe`, the parsed `user` object, a Telegram user id, or a
manually rebuilt query string as identity. The server verifies the raw initData
HMAC using the bot token, checks max age, resolves the Telegram user in the
admin registry, and derives the storage user scope.

Allowed Telegram roles for Mini App APIs are `registered` and `admin`.
`anonymous` means the user is pending approval in the KDCube Telegram Admin
panel and API calls return `403`.

Telegram Admin Mini App calls also use signed Telegram initData, but require
the signed Telegram user to have role `admin`.

## Runtime Selection

Use the same React app for KDCube and Telegram, but use different transports.

```ts
const bundleId = "versatile@2026-03-31-13-36";
const bundleBase =
  `/api/integrations/bundles/${tenant}/${project}/${bundleId}`;

const telegramInitData = window.Telegram?.WebApp?.initData || "";
const isTelegramMiniApp = telegramInitData.length > 0;

function telegramAlias(alias: string): string {
  const map: Record<string, string> = {
    telegram_profile: "telegram_profile",
    versatile_webapp_data: "telegram_versatile_webapp_data",
    conversations_list: "conversations_list",
    conversations_create: "telegram_conversations_create",
    conversations_switch: "telegram_conversations_switch",
    conversations_delete: "telegram_conversations_delete",
    preferences_canvas_data: "telegram_memory_canvas_data",
    preferences_canvas_save: "telegram_memory_canvas_save",
    preferences_canvas_export_excel: "telegram_memory_canvas_export_excel",
    preferences_canvas_import_excel: "telegram_memory_canvas_import_excel",
    telegram_user_admin_data: "telegram_webapp_user_admin_data",
    telegram_user_admin_upsert: "telegram_webapp_user_admin_upsert",
    telegram_user_admin_delete: "telegram_webapp_user_admin_delete"
  };
  return map[alias] || alias;
}

const getOperations = new Set([
  "telegram_profile",
  "conversations_list"
]);

function queryString(data: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(data)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

async function callOperation(alias: string, data: Record<string, unknown> = {}) {
  const actualAlias = isTelegramMiniApp ? telegramAlias(alias) : alias;
  const route = isTelegramMiniApp ? "public" : "operations";
  const usePost = !getOperations.has(alias);
  const url =
    `${bundleBase}/${route}/${actualAlias}` +
    (!usePost ? queryString(data) : "");
  const response = await fetch(url, {
    method: usePost ? "POST" : "GET",
    headers: {
      ...(usePost ? { "Content-Type": "application/json" } : {}),
      ...(isTelegramMiniApp
        ? { "X-Telegram-Init-Data": telegramInitData }
        : platformAuthHeaders)
    },
    body: usePost ? JSON.stringify({ data }) : undefined
  });
  if (!response.ok) {
    throw new Error(`${response.status}: ${await response.text()}`);
  }
  const wrapped = await response.json();
  return wrapped[actualAlias];
}
```

KDCube control-plane widgets should use the platform-provided auth/config
handshake. Telegram Mini App widgets should use raw Telegram initData and must
not wait for the KDCube parent-frame handshake. The logical conversation
operations are the same in both surfaces: KDCube calls
`/operations/conversations_*`; Telegram maps them to
`/public/telegram_conversations_*`.

## Common Calls

Bootstrap or refresh the app:

```ts
const data = await callOperation("versatile_webapp_data", {
  widget_path: "memory",
  mark_memory_seen: true
});
```

`versatile_webapp_widget` is only the decorated widget compatibility operation.
Do not use it for React app state.

List, create, switch, and delete chat conversations:

```ts
const conversations = await callOperation("conversations_list");

const created = await callOperation("conversations_create", {
  title: "Research"
});

await callOperation("conversations_switch", {
  conversation_id: created.active_conversation_id
});

await callOperation("conversations_delete", {
  conversation_id: created.active_conversation_id,
  delete_history: true
});
```

In the KDCube widget these calls resolve the current KDCube user id through the
Telegram Admin mapping. If no Telegram row is linked to that KDCube user, the
response returns `ok: false` with `error.code == "telegram_mapping_required"`.
In the Telegram Mini App, the same logical calls use signed Telegram initData.

Load and save the current memory/preferences canvas:

```ts
const canvas = await callOperation("preferences_canvas_data");

await callOperation("preferences_canvas_save", {
  document_text: canvas.document_text
});
```

Export and import the canvas as Excel:

```ts
const exported = await callOperation("preferences_canvas_export_excel");

await callOperation("preferences_canvas_import_excel", {
  content_b64: exported.content_b64
});
```

The current memory panel is backed by the existing preferences canvas. It is a
temporary compatibility surface until the new cross-conversation memories
subsystem replaces it.

Telegram admin operations:

```ts
const registry = await callOperation("telegram_user_admin_data");

await callOperation("telegram_user_admin_upsert", {
  telegram_user_id: "123",
  telegram_chat_id: "123",
  telegram_username: "name",
  kdcube_user_id: "",
  role: "registered",
  conversation_id: "",
  notes: ""
});
```

In KDCube, these are `/operations/telegram_user_admin_*` and require the
configured KDCube admin role. In Telegram, the widget maps them to
`/public/telegram_webapp_user_admin_*` and requires the signed Telegram user to
have role `admin`.

## What The Frontend Must Not Call

- `telegram_webhook`: called by Telegram Bot API, not the Mini App.
- `telegram_user_admin_*` from non-admin users: the operation route is for
  KDCube admins; the public facade is for signed Telegram users with role
  `admin`.
- `preferences_tools` MCP endpoint: this is an MCP protocol endpoint protected
  by a bundle-owned shared token, not a browser widget REST API.
