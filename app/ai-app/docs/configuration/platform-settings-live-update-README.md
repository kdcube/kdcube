---
id: repo:kdcube-ai-app/app/ai-app/docs/configuration/platform-settings-live-update-README.md
title: "Live Platform Settings Updates"
summary: "Descriptor-owned administrator edits, value-free Redis notifications, service-specific live handlers, and refresh-only boundaries for platform settings."
status: current
tags: ["configuration", "platform-settings", "descriptors", "live-update", "redis", "auth"]
keywords: ["live platform settings", "descriptor editor", "platform settings notification", "auth provider reload", "Connection Hub administrator"]
updated_at: 2026-09-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/app-hosted-platform-login-and-session-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
---
# Live Platform Settings Updates

KDCube can let an administrator edit selected staged platform settings and
apply the parts that are safe to change while services are running. The
descriptor files remain the place of truth. A Redis notification tells
interested services which section changed so they can reread their own
configuration.

## Enablement

Editing is closed unless `assembly.yaml` explicitly enables both the editor
and the section:

```yaml
management:
  platform_settings:
    editing:
      enabled: true
      sections:
        auth: true
```

Keep this switch off when descriptor files are published from a source
repository or deployment pipeline. In that case, change the source descriptor
and publish a new runtime snapshot. A missing switch has the same meaning as
`false`.

## Update Path

The current deployment has three application processes:

```text
Connection Hub operation in proc
  -> administrator guard and CSRF check
  -> locked descriptor read, validation, backup, atomic replace
  -> value-free tenant/project Redis notification
  -> ingress rereads descriptors and rebuilds its auth manager
```

Proc hosts Connection Hub, writes the staged descriptor, and publishes the
notification. Ingress subscribes because it is the current deployed owner of
the platform auth manager. The metrics server serves telemetry and needs no
auth-settings handler.

Every ingress worker subscribes to
`kdcube:config:platform-settings:update:{tenant}:{project}`. Redis Pub/Sub does
not retain messages, so a listener rereads each section it handles whenever it
subscribes or reconnects. The notification contains the section, scope,
changed descriptor paths, reason, actor and event provenance. This fixed
metadata-only envelope keeps configuration and credentials in their owning
descriptor and secret stores.

## Auth Activation Rules

| Change | File | Runtime behavior |
| --- | --- | --- |
| Provider configuration or trusted pools | `bundles.yaml` | Ingress builds a candidate manager from a fresh descriptor snapshot and swaps it in only after the build succeeds. New requests use it; in-flight requests finish with the manager they already resolved. |
| Selected provider or sign-in lane | `assembly.yaml` | Saved with a backup and reported as refresh-required. Active processes keep their original selection so running browser sessions are not cut over underneath them. |

Connection Hub reports a provider edit as `live` only when Redis reports at
least one subscriber. If no listener receives the message, the descriptor is
still saved and the operation reports `refresh`. A lane edit always reports
`refresh`.

The editor serializes the full read-modify-write cycle with a lock beside the
descriptor. It keeps the old file as `<name>.bak-<UTC timestamp>`, writes a
temporary file in the same directory, flushes it, and replaces the descriptor
atomically. The lock implementation supports POSIX and Windows hosts.

## Adding Another Section

A new platform-settings section needs four explicit pieces:

1. A targeted descriptor writer with validation, locking and a backup.
2. A section key under `management.platform_settings.editing.sections`.
3. A value-free event with a named scope and changed descriptor paths.
4. A handler only in each deployed service that holds state derived from that section.

The handler defines which scopes can apply live and which require a refresh.
Handlers belong to the deployed services that hold state derived from the
section. Per-request readers already observe the descriptor directly.
