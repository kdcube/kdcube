---
id: ks:docs/configuration/runtime-read-write-contract-README.md
title: "Runtime Read/Write Contract Compatibility Entry"
summary: "Compatibility entry for older helper-contract links: redirects readers to the canonical bundle configuration API guide and the detailed runtime storage model."
tags: ["service", "configuration", "runtime", "helpers", "contract"]
keywords: ["helper api compatibility entry", "get_settings helper", "get_plain helper", "get_secret helper", "bundle_prop helper", "bundle secret write helper", "user prop helper", "user secret helper", "authoritative configuration reference"]
see_also:
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/configuration/runtime-configuration-and-secrets-store-README.md
  - ks:docs/configuration/service-runtime-configuration-mapping-README.md
  - ks:docs/configuration/assembly-descriptor-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/configuration/bundles-secrets-descriptor-README.md
  - ks:docs/configuration/secrets-descriptor-README.md
---
# Runtime Read/Write Contract Compatibility Entry

This page is kept as a compatibility entry for older links.

The single author-facing programmatic contract for settings, props, and secrets
now lives here:

- [bundle-runtime-configuration-and-secrets-README.md](bundle-runtime-configuration-and-secrets-README.md)

Use that SDK page for:

- `get_settings()`
- `get_plain(...)`
- `get_secret(...)`
- `self.bundle_prop(...)`
- `await set_bundle_prop(...)`
- `await set_bundle_secret(...)`
- `get_user_prop(...)`
- `set_user_prop(...)`
- `get_secret("u:...")`
- `set_user_secret(...)`
- `delete_user_secret(...)`

The detailed storage and authority model now lives here:

- [runtime-configuration-and-secrets-store-README.md](runtime-configuration-and-secrets-store-README.md)

Use that service page for:

- local file mode vs `aws-sm`
- Redis cache role
- PostgreSQL ownership for user props
- secrets-provider ownership for user secrets
- current bundle prop persistence path
- current bundle secret persistence path
- export and ejection behavior

## One hard rule still applies everywhere

Do not bypass the documented helper contract in normal feature code or bundle
code.

Avoid:

- `os.getenv(...)` or `os.environ[...]` for deployment-owned config or secrets
- direct secrets-provider calls from feature code or bundle code
- direct hardcoded opens of descriptor YAML files

Use the SDK configuration page above as the single programmatic reference.

Async rule:

- new bundle APIs, hooks, tools, cron handlers, and `@on_job` handlers should
  use `await get_secret(...)`, `await set_user_secret(...)`, and
  `await delete_user_secret(...)`
