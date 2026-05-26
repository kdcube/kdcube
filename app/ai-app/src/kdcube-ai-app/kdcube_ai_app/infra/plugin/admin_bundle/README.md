---
id: kdcube.admin
title: "KDCube Admin Bundle"
summary: "Built-in privileged administration bundle for control-plane tools."
status: "mvp"
tags: ["admin", "bundle", "widgets", "storage", "control-plane"]
see_also:
  - "interface/README.md"
  - "interface/admin-storage.openapi.yaml"
  - "ks:docs/sdk/bundle/bundle-widget-integration-README.md"
---
# KDCube Admin Bundle

`kdcube.admin` is the built-in privileged bundle for operator-facing control
plane tools. It is registered by the platform and is available to users with
privileged access.

## Widgets

### `bundle_storage`

`bundle_storage` is a React/Redux file-browser widget for operational storage
cleanup and export.

It exposes three storage roots:

- `bundle_storage`: per-tenant/project bundle runtime storage, including built
  widget artifacts and bundle-owned runtime data.
- `managed_bundles`: materialized bundle sources resolved from git or built-in
  managed sources.
- `shared_storage`: configured shared application storage when it is backed by
  a local filesystem path.

The widget can browse storage paths, export selected files/directories as a zip,
delete selected files/directories after confirmation, and compare managed bundle
folders with the active bundle registry.

## Runtime Contract

The storage widget uses two backend surfaces:

- Ingress storage APIs under `/api/admin/control-plane/storage`.
- Processor registry API at `/admin/integrations/bundles/storage-registry`.

Local filesystem roots that the widget browses must be mounted into the ingress
runtime, because the storage APIs are served there. The processor runtime also
uses the same roots for bundle execution/build work.

The OpenAPI contract is maintained in
[interface/admin-storage.openapi.yaml](interface/admin-storage.openapi.yaml).

## Build

The widget source lives at:

```text
ui/storage
```

The admin bundle wires it as:

```text
ui.widgets.bundle_storage.src_folder = ui/storage
ui.widgets.bundle_storage.build_command = npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

The built widget is materialized under the bundle storage root for
`kdcube.admin`, using the same widget lifecycle as other bundle widgets.
