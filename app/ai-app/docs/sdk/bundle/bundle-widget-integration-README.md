---
id: ks:docs/sdk/bundle/bundle-widget-integration-README.md
title: "Bundle Widget Integration"
summary: "Bundle widget UI contract: source-folder widget apps, runtime config handshake, operation URL construction, auth propagation, and the recommended pattern when a capability is both widget and operation."
tags: ["sdk", "bundle", "widget", "iframe", "frontend", "integrations", "telegram", "memory"]
keywords: ["bundle widget contract", "iframe widget contract", "widget source folder", "static widget build", "runtime config handshake", "operation url construction", "auth propagation to widget", "widget and operation dual pattern", "shared sdk widget source", "telegram widget components", "memory widget component", "bundle widget integration"]
updated_at: 2026-05-22
see_also:
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - ks:docs/sdk/bundle/ui-components-lifecycle-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-client-ui-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
  - ks:docs/service/cicd/embedding-control-plane-frontend-README.md
---
# Bundle Widget Integration

Use this doc when a bundle exposes widget UI that KDCube serves and that must
call bundle operations correctly.

This is the rule:

- a widget must be declared as a bundle surface with `@ui_widget(...)`
- `ui.widgets.<alias>` config does not create a widget by itself; it
  tells the platform how to build and serve the already-declared widget alias
- new widget apps should be source folders, not Python-rendered TSX snippets
- KDCube serves the widget UI; the control plane/prototyping frontend may show
  it in an iframe, but that is a display choice
- the widget must request runtime config from the display environment
- the widget must build bundle operation URLs from that runtime config
- the widget must not hardcode tenant, project, or bundle id from the source tree

For the full lifecycle of discovery, preload, build, request-time fallback,
shared-storage locks, signatures, and concurrent workers, see
[UI Components Lifecycle](./ui-components-lifecycle-README.md).

If the same widget or static bundle UI is embedded by an external website, the
frame permission is an operator deployment setting, not a widget-code setting.
Configure `proxy.frame_embedding` so the KDCube proxy clears
`X-Frame-Options` and emits a CSP `frame-ancestors` allowlist on frameable
bundle routes. See
[Embedding The Control Plane Frontend](../../service/cicd/embedding-control-plane-frontend-README.md).

## Two Contracts: Surface And Build Config

Bundle widgets have two separate contracts that must align by alias.

1. **Surface contract**

   The bundle class exposes a method decorated with `@ui_widget(...)`.
   Discovery reads this decorator and places the alias in the bundle interface
   manifest. The control plane widget toolbar, widget visibility checks, and
   widget route resolution all start from this manifest.

   ```python
   @api(alias="versatile_webapp_widget", route="operations")
   @ui_widget(
       alias="versatile_webapp",
       icon={"lucide": "PanelTop", "tailwind": "heroicons-outline:rectangle-group"},
   )
   def versatile_webapp_widget(self, **kwargs):
       ...
   ```

2. **Build/serve contract**

   Bundle defaults or deployment descriptor props define
   `ui.widgets.<same_alias>`. This tells the platform that the alias is
   served as a built static app from a source folder.

   ```yaml
   ui:
     widgets:
       versatile_webapp:
         enabled: true
         src_folder: ui/widgets/versatile_webapp
         build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
   ```

If the decorator exists but `ui.widgets.<alias>` is absent, the widget
is a method-rendered widget: the route invokes the decorated Python method and
returns its payload.

If the decorator exists and `ui.widgets.<alias>` has an active `src_folder` and
`build_command`, the static widget app takes precedence. In that case the
decorated Python method is still required for discovery, visibility, and route
resolution, but it is not the UI that the browser receives. The method body may
be a small placeholder for legacy/fallback cases.

Source-folder builds currently run through the bundle UI build machinery
provided by the `BaseEntrypoint` class family. If a bundle exposes buildable
widgets, the entrypoint class must either inherit a concrete `BaseEntrypoint`
family class, such as `BaseEntrypoint`, `BaseEntrypointWithEconomics`,
`BaseEntrypointWithMemory`, or `BaseEntrypointWithEconomicsAndMemory`, or
implement the equivalent `_ensure_ui_build(...)` contract. A plain workflow
class with `@ui_widget(...)` decorators can declare widget surfaces, but it
will not build or refresh source-folder widget artifacts unless that build
contract exists. See
[Bundle Entrypoint Classes](./bundle-entrypoint-classes-README.md).

If `ui.widgets.<alias>` exists but the `@ui_widget(alias="<alias>")`
surface is missing, the static config is ignored for widget routing. A direct
widget request should fail with a missing-widget response because the platform
does not treat config-only entries as user-visible widget surfaces.

Use `enabled.widget.<alias>: false` to hide or disable a decorated widget alias.
This is separate from `ui.widgets.<alias>.enabled`, which controls
whether a static build config is active for that alias.

## Inherited Widget Aliases

Entrypoint classes commonly inherit widgets from SDK or platform base classes.
Those inherited `@ui_widget(...)` methods are normal bundle surfaces: they are
discovered through the class MRO, listed in the manifest, and can be served
unless disabled by effective bundle props.

To suppress an inherited widget without changing code, disable the surface:

```yaml
config:
  enabled:
    widget:
      memories: false
```

Do not use this as the suppression mechanism:

```yaml
config:
  ui:
    widgets:
      memories:
        enabled: false
```

That only disables the static app config for alias `memories`. If an inherited
decorated method still exists, the route may fall back to method-rendered HTML
instead of hiding the widget.

To replace an inherited widget UI while keeping the inherited surface, configure
the same alias as a source-folder widget:

```yaml
config:
  ui:
    widgets:
      memories:
        enabled: true
        src_folder: ui/widgets/my-memory-widget
        build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

The inherited `@ui_widget(alias="memories")` still declares the surface, while
the static app at `ui/widgets/my-memory-widget` becomes the served UI.

If code must override the decorator metadata itself, override the same Python
method name in the child class:

```python
class MyEntrypoint(BaseEntrypointWithMemory):
    @ui_widget(alias="memories", icon={"lucide": "NotebookText"})
    async def memories_widget(self, **kwargs):
        ...
```

Do not add a second method with the same alias while inheriting the parent
method. Duplicate widget aliases are invalid and manifest discovery raises a
duplicate-alias error.

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
     widgets:
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
required `shared_sources` in `configuration_defaults()` so workflow-side code
and rebuild hooks have stable defaults.

Current route-time widget serving evaluates effective bundle props after code
defaults and descriptor/admin props are merged. That means intrinsic widget
source/build values may live in `configuration_defaults()`, while descriptors
carry only deployment overrides. Descriptors may still repeat `src_folder` and
`build_command` when a seed file must be self-documenting or when an older
runtime has to be supported. For the exact merge/materialization rules, see
[Bundle Properties And Secrets Lifecycle](./bundle-properties-and-secrets-lifecycle-README.md).

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
  widgets:
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

### Build Signature & Cache

To avoid rebuilding unchanged sources on every bundle load, the loader
maintains a **build signature** per UI app and compares it to a stored
signature before deciding to rebuild. A matching signature produces:

```text
[bundle.ui] widget:<alias> skipped: signature cache hit
```

in the chat-proc logs and means the existing built artifacts under
`<bundle_storage_root>/ui/widgets/<alias>` are reused as-is. A mismatch
triggers a full rebuild via `build_command`.

**What goes into the signature**:

- `kind` — `main-view` or `widget:<safe_alias>`
- `src_path` — absolute path of the resolved source folder
- `build_command` — the exact command string from the widget config (after
  `<VI_BUILD_DEST_ABSOLUTE_PATH>` placeholder substitution)
- `bundle_delivery_id` — the bundle id from the active spec
- a sha256 over the source tree: each file's `relative_path + "\0" + size +
  "\0" + mtime_ns`, plus the same for every `shared_sources` source folder
  declared on the widget config

**What is ignored** when hashing the source tree:

- directories: `node_modules`, `.git`, `dist`, `build`, `.vite`,
  `.vite-temp`, `__pycache__`
- file suffixes: `.tsbuildinfo`
- generated `*.js` / `*.jsx` files (and their `.map` siblings) when a
  matching `*.ts` / `*.tsx` source file exists in the same directory — these
  are loader output shadows, not source

**Where signatures live**:

- main view: `<bundle_storage_root>/.ui.signature`
- per widget: `<bundle_storage_root>/.ui.widgets/<safe_alias>.signature`
  (where `safe_alias` is the widget alias with `/` → `_`)

**How to force a rebuild**:

- `touch` any non-ignored file under the widget's `src_folder` (changes
  `mtime_ns` → changes the signature)
- change anything in `build_command` (e.g. add a no-op flag)
- delete the signature file directly:
  `rm <bundle_storage_root>/.ui.widgets/<safe_alias>.signature`
- bump the bundle id (rarely useful for local development)

**Concurrency**: the build runs inside a shared-storage lock keyed by the
bundle storage root, so concurrent loads from multiple workers do not run
the same `build_command` twice. Workers that arrive after the signature was
written hit the cache and skip the build.

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
  widgets:
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
  widgets:
    task_memo_webapp:
      enabled: true
      src_folder: ui/widgets/task_memo_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

It does not change inherited method-rendered widgets such as `ai_bundles`,
`opex`, or other `@ui_widget` methods on a base class. Those aliases continue
to invoke their Python method and return method-rendered HTML unless their own
alias also has `src_folder` and `build_command`.

Do not add `ui.widgets.<alias>.src_folder/build_command` for an alias
unless that alias is intentionally migrating to the folder-built widget model.

## Main View Is Separate

Do not confuse widget app source with main-view source.

Use:

- `ui.main_view.src_folder: ui/main` for the bundle main view
- `ui.widgets.<alias>.src_folder: ui/widgets/<alias>` for widget apps

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
  -> ui.widgets.<alias>.shared_sources
  -> loader copies sdk://... into widget temp source under _shared/...
  -> Vite alias resolves @kdcube/<capability>-widget to _shared/...
  -> one built widget app is served from bundle storage
```

```yaml
ui:
  widgets:
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
  widgets:
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

### Tolerated Alternate Keys

A widget loaded in different host contexts (control plane, embedded
iframe, Telegram Mini App, public widget route) can receive the runtime
config payload under slightly different key names. The widget **must**
accept all of the following alternates when reading the payload, falling
back left-to-right within each group:

| Logical field | Canonical key | Alternates accepted |
| --- | --- | --- |
| Tenant | `defaultTenant` | `tenant`, `tenant_id` |
| Project | `defaultProject` | `project`, `project_id` |
| ID-token header name | `idTokenHeader` | `idTokenHeaderName`, `auth.idTokenHeaderName` |
| Base URL | `baseUrl` | (no alternate) |
| Access token | `accessToken` | (no alternate; preserve explicit `null`) |
| ID token | `idToken` | (no alternate; preserve explicit `null`) |
| App bundle id | `defaultAppBundleId` | (no alternate in the runtime-config payload; URL params accept `bundle_id` or `bundleId`) |

The reference implementation lives in
`sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/ui/widgets/copilot_webapp/src/store/settings.ts`
and reads:

```ts
const tenant  = config.defaultTenant  || config.tenant  || config.tenant_id;
const project = config.defaultProject || config.project || config.project_id;
const idHdr   = config.idTokenHeader
             || config.idTokenHeaderName
             || config.auth?.idTokenHeaderName
             || existing;
// access/id tokens use `??` so explicit null is preserved
const accessToken = config.accessToken ?? existing;
const idToken     = config.idToken     ?? existing;
```

When the widget is launched via a route that encodes the bundle id as a URL
query param, accept either spelling:

```ts
const bundleId = params.get('bundle_id') || params.get('bundleId');
```

### Tolerated Response Event Types

The widget must accept both response event types from the `postMessage`
runtime-config handshake:

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
        baseUrl from /api/cp-frontend-config or CONFIG_REQUEST
          == "https://kdcube.example.com"
        operation call -> "https://kdcube.example.com/api/..."
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

- first fetch `/api/cp-frontend-config` from the widget frame origin and use
  its `baseUrl` when it returns usable runtime config
- if that endpoint is unavailable, use `baseUrl` received from the KDCube
  runtime config handshake
- if no runtime config is available, fall back to `window.location.origin` from
  the widget frame itself
- if the widget route contains tenant/project/bundle, use that route only as a
  scope fallback, not as proof that the host-page origin is the API origin

The KDCube control-plane runtime sends `baseUrl` from its own frame origin. A
widget rendered by `srcDoc` still runs under the KDCube frontend frame origin;
a widget loaded by `src` from `/api/integrations/.../widgets/...` also runs
under the KDCube origin. In both cases, root-relative URLs and
runtime-provided `baseUrl` must resolve to the hosted KDCube domain.

## Frame Resize Contract

A parent page from another origin cannot read the widget iframe DOM to measure
`scrollHeight` or `scrollWidth`. Cross-origin sizing must be cooperative.

KDCube-hosted static bundle UI and widget HTML entrypoints inject a resize
reporter before serving `index.html`. The reporter posts:

```js
window.parent.postMessage({
  type: 'kdcube-resize',
  height: measuredContentHeight,
  width: optionalOverflowWidth,
  contentWidth: measuredContentWidth,
  viewportWidth: currentFrameViewportWidth,
}, '*');
```

The actual server-injected reporter measures the maximum document/body
scroll/client/offset dimensions, debounces short layout bursts, and sends:

- `height`: the content height the parent should apply to the iframe
- `width`: non-zero only when content overflows the current iframe viewport
- `contentWidth`: measured content width for diagnostics
- `viewportWidth`: the iframe viewport width observed by the embedded document
- `seq`: monotonic sequence number from the reporter instance
- `reason`: trigger that produced the measurement

It runs on load, window resize, DOM changes, resize observations, and short
delayed retries.

The parent must provide normal iframe width with CSS, for example
`width: 100%`. Do not set `iframe.style.width` from every `kdcube-resize`
message. That creates a width feedback loop: an early narrow measurement can be
applied as the real iframe width, the widget reflows into a narrow layout, and
the final height becomes genuinely large. Use the message `width` only as an
optional `min-width` signal when it is greater than the current iframe width.

For diagnostics, append `?kdcube_resize_debug=1` to the widget/static UI route
or set `localStorage.setItem('kdcube.resize.debug', '1')` in the embedded page
and reload. The reporter logs `[kdcube-resize]` entries for measurements it
posts and for measurements it skips, including untrusted tiny viewport cases.

If a widget or bundle UI contains another iframe, each frame layer must either
host a KDCube-injected document or manually forward the same `kdcube-resize`
message upward. Frame headers decide whether the browser may display the frame;
they do not grant cross-origin DOM measurement.

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
when `ui.widgets.<alias>.src_folder/build_command` is configured.

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

async function loadFrontendConfig(): Promise<boolean> {
  try {
    const response = await fetch(`${state.baseUrl}/api/cp-frontend-config`, {
      credentials: 'include',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) return false
    const config = await response.json()
    applyConfig({
      ...config,
      defaultTenant: config.defaultTenant || config.tenant || config.tenant_id,
      defaultProject: config.defaultProject || config.project || config.project_id,
      idTokenHeader: config.idTokenHeader || config.idTokenHeaderName || config.auth?.idTokenHeaderName,
    })
    return hasConfig()
  } catch {
    // Route-derived tenant/project/bundle may still be enough for read-only
    // static surfaces, but auth metadata only comes from runtime config.
    return false
  }
}

function requestParentConfig(): void {
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
}

void loadFrontendConfig().then((loaded) => {
  if (!loaded) requestParentConfig()
})
```

## Operational Rules

- Widget load should be read-only by default.
- Use an explicit in-widget action such as `Refresh` if the widget needs to trigger a syncing bootstrap or other mutating backend operation.
- If the widget can derive tenant/project/bundle defaults from its route, use those only as safe standalone fallbacks.
- First fetch `/api/cp-frontend-config`; if it returns usable runtime config,
  do not wait for parent messaging.
- Keep parent `CONFIG_REQUEST` / `CONFIG_RESPONSE` as a fallback, because
  older runtime display environments may be the only source of auth tokens and
  final runtime scope.
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

- `/api/cp-frontend-config` first, then `CONFIG_REQUEST` as fallback to the
  runtime display environment
- accept both `CONN_RESPONSE` and `CONFIG_RESPONSE`
- build operation URLs from runtime scope
- use the host-provided auth tokens and token-header name
