---
id: ks:docs/sdk/bundle/bundle-widget-integration-README.md
title: "Bundle Widget Integration"
summary: "Bundle widget UI contract: source-folder widget apps, runtime config handshake, operation URL construction, auth propagation, and the recommended pattern when a capability is both widget and operation."
tags: ["sdk", "bundle", "widget", "iframe", "frontend", "integrations", "telegram", "memory"]
keywords: ["bundle widget contract", "iframe widget contract", "widget source folder", "web app widget build", "runtime config handshake", "operation url construction", "auth propagation to widget", "widget and operation dual pattern", "shared sdk widget source", "telegram widget components", "memory widget component", "bundle widget integration"]
updated_at: 2026-05-16
see_also:
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-client-ui-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
---
# Bundle Widget Integration

Use this doc when a bundle exposes widget UI that KDCube serves and that must
call bundle operations correctly.

This is the rule:

- new widget apps should be source folders, not Python-rendered TSX snippets
- KDCube serves the widget UI; the control plane/prototyping frontend may show
  it in an iframe, but that is a display choice
- the widget must request runtime config from the display environment
- the widget must build bundle operation URLs from that runtime config
- the widget must not hardcode tenant, project, or bundle id from the source tree

## Reusing SDK Widget Components

Reusable SDK widget UI is a build-time source materialization contract.
It is not an npm package, and it is not a runtime import from the KDCube
monorepo.

When a bundle widget imports SDK UI such as User Memory or Telegram
admin/channels panels, these three places must agree:

1. **Bundle defaults or descriptor props**

   The widget config must declare `shared_sources` for every SDK UI source the
   widget imports.

   ```yaml
   ui:
     web_app_widgets:
       copilot_webapp:
         enabled: true
         src_folder: ui/widgets/copilot_webapp
         build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
         shared_sources:
           memory_widget:
             src_folder: sdk://context/memory/ui/widget/memories
             target: _shared/memory-widget
           telegram_widget:
             src_folder: sdk://integrations/telegram/ui/widget.telegram
             target: _shared/telegram-widget
   ```

2. **Widget Vite aliases**

   The widget build must resolve the public import names to the materialized
   `_shared/...` folders. A monorepo fallback is allowed only for direct local
   development before the loader materializes shared sources.

   ```ts
   '@kdcube/memory-widget': memoryWidgetEntry
   '@kdcube/telegram-widget': telegramWidgetEntry
   ```

3. **Widget page wrappers**

   The bundle imports shared components and injects its own operation caller.
   The shared UI must not invent tenant/project/bundle ids or bypass backend
   auth.

   ```tsx
   import { TelegramAdminPanel } from '@kdcube/telegram-widget';
   import { callOperation } from './store/apiClient';

   export function TelegramAdminPage() {
     return <TelegramAdminPanel callOperation={callOperation} />;
   }
   ```

For built-in/reference bundles, keep `src_folder`, `build_command`, and
required `shared_sources` in `configuration_defaults()`. Then descriptors can
usually say only `enabled: true`. Descriptors may repeat the same values when a
seed should be self-documenting or when an environment intentionally overrides
the default.

If the build fails with a path like this:

```text
Could not load /integrations/telegram/ui/widget.telegram/src/index.tsx
Could not load /context/memory/ui/widget/memories/src/embed.tsx
```

then the widget imported a shared SDK component, but the matching
`shared_sources` entry was missing, had the wrong `target`, or did not get into
the effective bundle props. Fix the widget config/defaults first; do not patch
the built temp directory or hardcode an absolute developer-machine path.

## Source Folder Widget Apps

For new React/Vite widgets, keep widget app source under a widget-specific
folder such as:

```text
ui/widgets/<widget_alias>/
  package.json
  index.html
  vite.config.js
  src/
```

Do not put widget source under `ui/main`. In KDCube docs and examples, `ui/main`
is the convention for a bundle main view declared by `ui.main_view`.

Declare the widget source in bundle configuration:

```yaml
ui:
  web_app_widgets:
    task_memo_webapp:
      enabled: true
      src_folder: ui/widgets/task_memo_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
      # Optional: copy SDK/platform UI source into the temporary build workspace.
      # This works for external-git bundles because the bundle imports the
      # materialized `_shared/...` path, not a developer-machine monorepo path.
      shared_sources:
        memory_widget:
          src_folder: sdk://context/memory/ui/widget/memories
          target: _shared/memory-widget
        telegram_widget:
          src_folder: sdk://integrations/telegram/ui/widget.telegram
          target: _shared/telegram-widget
```

Build command contract:

- `<VI_BUILD_DEST_ABSOLUTE_PATH>` is the loader-provided temporary output
  directory
- the platform injects it through `OUTDIR`,
  `VI_BUILD_DEST_ABSOLUTE_PATH`, and `VITE_BUILD_DEST_ABSOLUTE_PATH`
- the widget build system must treat it as an output directory, not as a Vite
  positional build argument
- for Vite, configure `build.outDir` from `process.env.OUTDIR`
- for Vite, `base: './'` is required so built assets are relative to the
  widget route

Minimal Vite config:

```ts
export default defineConfig({
  base: './',
  build: {
    outDir: process.env.OUTDIR || 'dist',
    emptyOutDir: true,
  },
})
```

Check the built `index.html`. It must reference assets with relative paths:

```html
<script type="module" src="./assets/index-....js"></script>
<link rel="stylesheet" href="./assets/index-....css">
```

If Vite emits root-relative assets such as `/assets/index-....js`, the widget
iframe can appear blank because the browser requests assets from the KDCube app
root instead of:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{widget_alias}/assets/...
```

Fix this in the widget's Vite config with `base: './'`; do not work around it
by copying assets or hardcoding bundle routes.

Do not put the destination path after `vite build`.

Wrong:

```json
{
  "scripts": {
    "build": "vite build <VI_BUILD_DEST_ABSOLUTE_PATH>"
  }
}
```

Correct:

```json
{
  "scripts": {
    "build": "vite build"
  }
}
```

If the loader log or npm output shows:

```text
vite build /.../.ui.build.tmp...
```

then the output path leaked into the command as a positional argument. Vite will
treat it as the project root/entry and may fail with:

```text
[UNRESOLVED_ENTRY] Cannot resolve entry module .../.ui.build.tmp.../index.html
```

Fix the widget build contract; do not manually copy built files into bundle
storage.

The bundle loader builds that source folder into shared bundle storage under:

```text
<bundle_storage_root>/ui/widgets/<widget_alias>
```

The widget route serves the built app and supports SPA subpath fallback:

```text
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{widget_alias}
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{widget_alias}/{widget_path}
```

For Telegram Mini Apps or other public launch surfaces where the static widget
app must load before platform auth exists, use the public static-widget route:

```text
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{widget_alias}
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{widget_alias}/{widget_path}
```

That route serves only the built widget app assets. It does not authenticate
product data. Any public data/action API used by that widget must have its own
bundle-level auth, for example Telegram WebApp `initData` verification.

## Dual Runtime Pattern

When the same widget app runs in KDCube control plane and Telegram, select the
transport at runtime:

- if `window.Telegram?.WebApp?.initData` exists, treat it as Telegram
- otherwise, use the normal KDCube runtime config handshake

KDCube control-plane runtime:

- wait for `CONFIG_RESPONSE` or `CONN_RESPONSE`
- use `baseUrl`, `defaultTenant`, `defaultProject`, `defaultAppBundleId`
- call `/operations/{alias}`
- pass KDCube auth headers from runtime config

Telegram Mini App runtime:

- do not wait for the KDCube runtime config handshake
- call `window.Telegram.WebApp.ready()` and `expand()` when available
- serve the app from `/public/widgets/{widget_alias}`
- call bundle public aliases such as `/public/{telegram_alias}`
- send the exact `window.Telegram.WebApp.initData` string as
  `X-Telegram-Init-Data`

Keep the API aliases explicit. A common pattern is:

```ts
const telegramAliases: Record<string, string> = {
  task_memo_webapp_data: "telegram_task_memo_webapp_data",
  tasks_list: "telegram_tasks_list",
  tasks_create: "telegram_tasks_create",
  run_task_now: "telegram_run_task_now",
};
```

The public route loads the app; the public operation verifies the user. Do not
trust caller-supplied `user_id` or `fingerprint` in Telegram mode.

Use `npm ci` in `build_command` when the widget source commits a lockfile. For
early prototype widgets without a lockfile, `npm install --no-package-lock`
avoids mutating the source folder during loader builds.

For multiple buildable widgets, repeat the same contract per alias:

```yaml
ui:
  web_app_widgets:
    first_widget:
      enabled: true
      src_folder: ui/widgets/first_widget
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
    second_widget:
      enabled: true
      src_folder: ui/widgets/second_widget
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

Each widget source folder needs its own `package.json` and build config that
honors `OUTDIR`. One working widget does not prove the second widget's Vite
config is correct.

The decorated `@ui_widget(...)` method remains the widget discovery/manifest
surface. Product behavior and data mutations should live behind separate
structured `@api(route="operations")` methods that the widget calls.

### Per-Alias Selection

Source-folder serving is selected per widget alias.

This config affects only `task_memo_webapp`:

```yaml
ui:
  web_app_widgets:
    task_memo_webapp:
      enabled: true
      src_folder: ui/widgets/task_memo_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

It does not change inherited or legacy widgets such as `ai_bundles`, `opex`, or
other `@ui_widget` methods on a base class. Those aliases continue to invoke
their Python method and return method-rendered HTML unless their own alias also
has `src_folder` and `build_command`.

Do not add `ui.web_app_widgets.<alias>.src_folder/build_command` for an alias
unless that alias is intentionally migrating to the folder-built widget model.

## Main View Is Separate

Do not confuse widget app source with main-view source.

Use:

- `ui.main_view.src_folder: ui/main` for the bundle main view
- `ui.web_app_widgets.<alias>.src_folder: ui/widgets/<alias>` for widget apps

Both use the same loader/build/storage paradigm. They are different surfaces.

## Shared UI Source Materialization

Some bundle widgets need to reuse platform UI code without packaging that code
inside the bundle repository. Configure `shared_sources` on the widget or main
view build. The builder copies each source into the temporary build source tree
before running `npm install` / `npm run build`.

Ownership rule:

- bundle code defaults should declare the widget's `src_folder`,
  `build_command`, and required SDK `shared_sources`
- descriptors should normally only set deployment policy such as
  `enabled: true` and provider URLs/secrets
- descriptor-level `shared_sources` is still allowed for explicit local testing
  or overrides, but a built-in/reference bundle should not require every
  descriptor to repeat its own UI source wiring

Runtime flow:

```text
bundle defaults / descriptor props
  -> ui.web_app_widgets.<alias>.shared_sources
  -> loader copies sdk://... into widget temp source under _shared/...
  -> Vite alias resolves @kdcube/<capability>-widget to _shared/...
  -> one built widget app is served from bundle storage
```

```yaml
ui:
  web_app_widgets:
    versatile_webapp:
      src_folder: ui/widgets/versatile_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
      shared_sources:
        memory_widget:
          src_folder: sdk://context/memory/ui/widget/memories
          target: _shared/memory-widget
        telegram_widget:
          src_folder: sdk://integrations/telegram/ui/widget.telegram
          target: _shared/telegram-widget
```

The widget can then import from its materialized local path, for example through
a Vite alias that points to `_shared/memory-widget/src/embed.tsx` or
`_shared/telegram-widget/src/index.tsx`.

Current SDK-owned shared widget sources:

| Capability | `sdk://` source | Usual target | Typical import |
| --- | --- | --- | --- |
| User Memory widget | `sdk://context/memory/ui/widget/memories` | `_shared/memory-widget` | `@kdcube/memory-widget` |
| Telegram admin/channels panels | `sdk://integrations/telegram/ui/widget.telegram` | `_shared/telegram-widget` | `@kdcube/telegram-widget` |

Supported source path forms:

- `sdk://...` resolves under the installed KDCube SDK package.
- `bundle://...` resolves under the bundle root.
- relative paths resolve under the bundle root.
- absolute paths are allowed for local development, but should not be used in
  reusable descriptors.

The build signature includes both the bundle source tree and all shared source
trees, so updating the shared component triggers a rebuild.

## Advanced: Hybrid Widget Composition

The `shared_sources` pattern is a hybrid between two simpler options:

- **iframe composition:** serve an existing platform widget as its own widget
  route and embed it in another app with `<iframe>`.
- **bundle-local composition:** copy all shared UI code into the bundle repo and
  import it like ordinary local source.

Hybrid composition keeps the bundle repo small while still producing a single
React tree at build time. The platform materializes the selected SDK source into
the temporary build workspace, and the bundle web app imports that materialized
source as if it were local.

Use this pattern when:

- the user experience should be one app surface, not nested frames
- the reusable UI is platform-owned SDK code
- the bundle may live in an external git repository
- the bundle should not know the developer-machine path to the KDCube monorepo

Do not use this pattern when:

- a standalone iframe widget is good enough
- the shared code is bundle-specific and should live in the bundle repo
- the shared source has a large dependency surface that the host widget should
  not own

Example host widget config:

```yaml
ui:
  web_app_widgets:
    versatile_webapp:
      enabled: true
      src_folder: ui/widgets/versatile_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
      shared_sources:
        memory_widget:
          src_folder: sdk://context/memory/ui/widget/memories
          target: _shared/memory-widget
        telegram_widget:
          src_folder: sdk://integrations/telegram/ui/widget.telegram
          target: _shared/telegram-widget
```

Example Vite alias in the host widget:

```ts
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const materializedMemoryWidget = path.resolve(__dirname, '_shared/memory-widget/src/embed.tsx');
const materializedTelegramWidget = path.resolve(__dirname, '_shared/telegram-widget/src/index.tsx');
const sdkMemoryWidget = path.resolve(
  __dirname,
  '../../../../../../context/memory/ui/widget/memories/src/embed.tsx',
);
const sdkTelegramWidget = path.resolve(
  __dirname,
  '../../../../../../integrations/telegram/ui/widget.telegram/src/index.tsx',
);
const memoryWidgetEntry = fs.existsSync(materializedMemoryWidget)
  ? materializedMemoryWidget
  : sdkMemoryWidget;
const telegramWidgetEntry = fs.existsSync(materializedTelegramWidget)
  ? materializedTelegramWidget
  : sdkTelegramWidget;

export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: {
      '@kdcube/memory-widget': memoryWidgetEntry,
      '@kdcube/telegram-widget': telegramWidgetEntry,
    },
  },
  build: {
    outDir: process.env.OUTDIR || 'dist',
    emptyOutDir: true,
  },
});
```

The fallback path is only for SDK-local development, where the developer builds
the host widget directly from the monorepo before the platform materializes
`_shared/...`. Reusable descriptors should rely on `sdk://...`, not on that
fallback path.

Example host component:

```tsx
import { MemoriesWidgetEmbed } from '@kdcube/memory-widget';
import { TelegramAdminPanel, TelegramConversationsPanel } from '@kdcube/telegram-widget';

import { callOperation } from './store/apiClient';

export function MemoryPage() {
  return <MemoriesWidgetEmbed />;
}

export function TelegramAdminPage() {
  return <TelegramAdminPanel callOperation={callOperation} />;
}

export function ConversationsPage({ conversations, reload }) {
  return (
    <TelegramConversationsPanel
      conversations={conversations}
      reload={reload}
      callOperation={callOperation}
    />
  );
}
```

Operational rules:

- the shared source is copied only into the temporary build workspace; it is not
  committed into the external bundle repo
- the host widget `package.json` must include the runtime dependencies required
  by the shared component
- the copied source is included in the UI build signature, so changes to SDK
  shared source trigger a rebuild
- the shared component still calls bundle APIs through the normal runtime config
  or public bridge; it must not hardcode tenant, project, bundle id, or host
  routes
- use a wrapper/export component in the shared source, such as `src/embed.tsx`,
  when the shared widget needs style isolation or its own provider tree
- shared UI is not an authorization boundary; backend operations still enforce
  KDCube roles, Telegram `initData`, and Telegram registry roles
- for Telegram Mini App panels, expose the same source-folder widget in KDCube
  and Telegram, but gate admin tabs from backend payload such as
  `permissions.show_admin_component`; regular users should still be able to use
  non-admin panels such as memories and chat/channel selection
- shared Telegram panels accept an injected operation caller so each host
  widget can map logical operations to KDCube-authenticated or Telegram-public
  aliases without duplicating panel UI

## Required Runtime Config

The widget should request these fields from the runtime display environment:

- `baseUrl`
- `accessToken`
- `idToken`
- `idTokenHeader`
- `defaultTenant`
- `defaultProject`
- `defaultAppBundleId`

The widget must accept both response event types:

- `CONN_RESPONSE`
- `CONFIG_RESPONSE`

Both are used in the platform today. Do not listen only for `CONFIG_RESPONSE`.

## Frame Origin And API Base URL

Widget API calls must be anchored to the KDCube-hosted frame origin, not to the
top-level page that may be embedding KDCube.

Browser URL resolution is frame-local:

```text
https://host-app.example.net
  iframe src="https://kdcube.example.com/platform/chat"
    KDCube frontend frame:
      window.location.origin == "https://kdcube.example.com"
      fetch("/api/...")     -> "https://kdcube.example.com/api/..."

      nested bundle widget frame:
        baseUrl from CONFIG_REQUEST == "https://kdcube.example.com"
        operation call              -> "https://kdcube.example.com/api/..."
```

This means a normal embedded deployment works when the host application frames
the KDCube frontend by URL:

```html
<iframe src="https://kdcube.example.com/platform/chat"></iframe>
```

The widget must never use `window.top.location`, `document.referrer`, or a
caller-provided host-page URL as its API base. Those values can point to the
embedding application, for example `https://host-app.example.net`, and would
make the widget call `https://host-app.example.net/api/...` by mistake.

Correct base URL selection:

- first use `baseUrl` received from the KDCube runtime config handshake
- if no runtime config is available, fall back to `window.location.origin` from
  the widget frame itself
- if the widget route contains tenant/project/bundle, use that route only as a
  scope fallback, not as proof that the host-page origin is the API origin

The KDCube control-plane runtime sends `baseUrl` from its own frame origin. A
widget rendered by `srcDoc` still runs under the KDCube frontend frame origin;
a widget loaded by `src` from `/api/integrations/.../widgets/...` also runs
under the KDCube origin. In both cases, root-relative URLs and
runtime-provided `baseUrl` must resolve to the hosted KDCube domain.

There is one valid exception: a same-origin reverse-proxy deployment may serve
the host application and KDCube under one public origin:

```text
https://app.example.com/app/*       -> host application
https://app.example.com/platform/*  -> KDCube frontend
https://app.example.com/api/*       -> KDCube API
```

In that topology, `window.location.origin` is intentionally
`https://app.example.com`, and the proxy must route all KDCube paths used by
the frontend and widgets, including `/platform`, `/api`, streaming endpoints,
and `/api/integrations/...`.

## Required URL Shape

Bundle operations must be called as:

```text
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{alias}
```

For bundle widgets, `{bundle_id}` should come from `defaultAppBundleId`.

Do not build operation URLs with:

- empty `tenant` / `project` / `bundleId`
- source-folder names
- bundle-local constants that may drift from `bundles.yaml`

## Compatibility Pattern

For source-folder widgets, prefer a minimal decorated widget method plus
separate structured APIs. If an existing client still loads the widget through
the operations route, keep the widget method decorated with both:

```python
@ui_widget(alias="task-board", user_types=("registered",))
@api(alias="task-board", route="operations", user_types=("registered",))
async def task_board(self, **kwargs):
    ...
```

That means:

- widget discovery/fetch is still driven by `@ui_widget(...)`
- legacy operation callers can still call the same method through `/operations/task-board`

For a source-folder widget, the method may return only a small compatibility
fallback because the platform serves the built widget app from bundle storage
when `ui.web_app_widgets.<alias>.src_folder/build_command` is configured.

If the widget UI itself needs a structured backend API, expose a separate alias such as:

```python
@api(method="POST", alias="task-tracker-api", route="operations", user_types=("registered",))
async def task_tracker_api(self, operation: str, payload: dict | None = None, **kwargs):
    ...
```

Example operations for that API might be:

- `list_tasks`
- `create_task`
- `update_schedule`
- `run_task_now`

Then the widget calls `/operations/task-tracker-api`, not `/operations/task-board`.

## Minimal Widget Handshake Example

```ts
const identity = 'MY_BUNDLE_WIDGET'

const state = {
  baseUrl: window.location.origin,
  accessToken: '',
  idToken: '',
  idTokenHeader: 'X-ID-Token',
  tenant: '',
  project: '',
  bundleId: '',
}

function hasConfig(): boolean {
  return !!(state.baseUrl && state.tenant && state.project && state.bundleId)
}

function makeHeaders(): Headers {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  if (state.accessToken) headers.set('Authorization', `Bearer ${state.accessToken}`)
  if (state.idToken) headers.set(state.idTokenHeader || 'X-ID-Token', state.idToken)
  return headers
}

function operationUrl(alias: string): string {
  if (!hasConfig()) throw new Error('Widget configuration is incomplete.')
  // baseUrl is the KDCube frame origin from runtime config, or this widget
  // frame's own origin as a fallback. It must not come from window.top.
  const baseUrl = state.baseUrl.replace(/\/+$/, '')
  const tenant = encodeURIComponent(state.tenant)
  const project = encodeURIComponent(state.project)
  const bundleId = encodeURIComponent(state.bundleId)
  return `${baseUrl}/api/integrations/bundles/${tenant}/${project}/${bundleId}/operations/${alias}`
}

async function postOperation<T>(alias: string, payload: Record<string, unknown>): Promise<T> {
  const response = await fetch(operationUrl(alias), {
    method: 'POST',
    credentials: 'include',
    headers: makeHeaders(),
    body: JSON.stringify({ data: payload }),
  })

  const text = await response.text()
  let parsed: unknown = {}
  try {
    parsed = text ? JSON.parse(text) : {}
  } catch {
    parsed = { raw: text }
  }

  if (!response.ok) {
    const detail =
      parsed && typeof parsed === 'object' && 'detail' in (parsed as Record<string, unknown>)
        ? String((parsed as Record<string, unknown>).detail)
        : text || response.statusText
    throw new Error(detail)
  }

  if (parsed && typeof parsed === 'object' && alias in (parsed as Record<string, unknown>)) {
    return (parsed as Record<string, unknown>)[alias] as T
  }

  return parsed as T
}

function applyConfig(config: Record<string, unknown>): void {
  if (typeof config.baseUrl === 'string' && config.baseUrl) state.baseUrl = config.baseUrl
  if (typeof config.accessToken === 'string' || config.accessToken === null) state.accessToken = config.accessToken || ''
  if (typeof config.idToken === 'string' || config.idToken === null) state.idToken = config.idToken || ''
  if (typeof config.idTokenHeader === 'string' && config.idTokenHeader) state.idTokenHeader = config.idTokenHeader
  if (typeof config.defaultTenant === 'string' && config.defaultTenant) state.tenant = config.defaultTenant
  if (typeof config.defaultProject === 'string' && config.defaultProject) state.project = config.defaultProject
  if (typeof config.defaultAppBundleId === 'string' && config.defaultAppBundleId) state.bundleId = config.defaultAppBundleId
}

window.addEventListener('message', (event: MessageEvent) => {
  if (event.data?.type !== 'CONN_RESPONSE' && event.data?.type !== 'CONFIG_RESPONSE') return
  if (event.data.identity !== identity || !event.data.config) return
  applyConfig(event.data.config)
})

window.parent.postMessage(
  {
    type: 'CONFIG_REQUEST',
    data: {
      identity,
      requestedFields: [
        'baseUrl',
        'accessToken',
        'idToken',
        'idTokenHeader',
        'defaultTenant',
        'defaultProject',
        'defaultAppBundleId',
      ],
    },
  },
  '*',
)
```

## Operational Rules

- Widget load should be read-only by default.
- Use an explicit in-widget action such as `Refresh` if the widget needs to trigger a syncing bootstrap or other mutating backend operation.
- If the widget can derive tenant/project/bundle defaults from its route, use those only as safe standalone fallbacks.
- Still keep the runtime config handshake, because auth tokens and final runtime scope belong to KDCube.
- For platform widgets and embedded browser clients, the preferred `POST /operations/{alias}` body shape is `{ "data": { ... } }`.
- The integrations layer also accepts a raw JSON object body and treats it as `data`, so webhook-style service integrations do not need a platform-specific wrapper.
- The integrations layer returns an envelope shaped like `{ status, tenant, project, bundle_id, [alias]: result }`; widgets should unwrap the `[alias]` field.
- Widget operations that start long-running work should submit a background job
  and return a durable job id/status immediately. The bundle's single `@on_job`
  handler should call `await super().handle_job(**kwargs)` first so SDK mixins
  can consume their own `work_kind` values, then the widget can refresh a status
  operation until the shared job record reaches a terminal state.

## Reference Examples

Use these as reference implementations for the runtime config handshake and
operation-call shape:

- [PreferencesBrowser.tsx](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/PreferencesBrowser.tsx)
- [KnowledgeBaseAdmin.tsx](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/ui/KnowledgeBaseAdmin.tsx)

Those examples are useful for runtime config and API calls. For new widgets,
prefer the source-folder layout described above instead of embedding TSX/HTML in
Python-rendered widget responses.

They show:

- `CONFIG_REQUEST` to the runtime display environment
- accept both `CONN_RESPONSE` and `CONFIG_RESPONSE`
- build operation URLs from runtime scope
- use the host-provided auth tokens and token-header name
