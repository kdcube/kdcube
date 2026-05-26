---
id: kdcube.admin/interface
title: "KDCube Admin Bundle Interface"
summary: "Privileged widget and storage control-plane contract for the built-in admin bundle."
status: "mvp"
tags: ["interface", "openapi", "admin", "storage", "bundle", "widgets"]
see_also:
  - "admin-storage.openapi.yaml"
  - "../README.md"
  - "ks:docs/sdk/bundle/bundle-widget-integration-README.md"
---
# KDCube Admin Bundle Interface

The OpenAPI contract is documented in:

- [admin-storage.openapi.yaml](admin-storage.openapi.yaml)

Protected surfaces:

- widget `bundle_storage`
- `GET /api/admin/control-plane/storage/roots`
- `GET /api/admin/control-plane/storage/tenants-projects`
- `GET /api/admin/control-plane/storage/list`
- `POST /api/admin/control-plane/storage/export`
- `POST /api/admin/control-plane/storage/delete`
- `GET /admin/integrations/bundles/storage-registry`

The storage APIs support scoped browsing, export, and deletion for local
filesystem-backed storage roots. The registry API returns the active bundle
registry and the managed bundle folders referenced by that registry, so the
widget can highlight active and orphaned managed folders.

All surfaces are privileged control-plane surfaces and use the platform admin
session.
