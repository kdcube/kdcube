---
id: ks:docs/service/configuration/descriptor-plain-config-README.md
title: "Descriptor Plain Config Access"
summary: "How runtime code reads non-secret values directly from mounted assembly.yaml and bundles.yaml via read_plain/get_plain."
tags: ["service", "configuration", "assembly", "bundles", "runtime", "code"]
keywords: ["read_plain", "get_plain", "assembly.yaml", "bundles.yaml", "dot path", "a:", "b:", "/config"]
see_also:
  - ks:docs/service/configuration/service-config-README.md
  - ks:docs/service/configuration/code-config-secrets-README.md
  - ks:docs/service/cicd/assembly-descriptor-README.md
  - ks:docs/service/secrets/secrets-service-README.md
---
# Descriptor Plain Config Access

This document explains the complementary runtime path to `get_secret(...)`.

Use:

- `get_secret(...)` / `read_secret(...)` for secrets
- `get_plain(...)` / `read_plain(...)` for non-secret descriptor values

The goal is simple:

- `assembly.yaml` and `bundles.yaml` remain the human-edited source of truth
- runtime code can read selected plain values from those descriptors directly
- secrets still stay in `secrets.yaml`, `bundles.secrets.yaml`, or the configured secrets provider

## 1. Runtime API

Code entrypoints live in:

- [config.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config.py)

Available helpers:

- `get_plain(key, default=None)`
- `read_plain(key, default=None)`
- `get_secret(key, default=None)`
- `read_secret(key, default=None)`

Example:

```python
from kdcube_ai_app.apps.chat.sdk.config import read_plain

workspace_type = read_plain("storage.workspace.type", default="custom")
default_bundle_id = read_plain("b:default_bundle_id")
```

## 2. Namespaces

`read_plain(...)` uses a small namespace convention:

- no prefix -> `assembly.yaml`
- `a:` -> `assembly.yaml`
- `b:` -> `bundles.yaml`

Examples:

- `read_plain("storage.workspace.type")`
- `read_plain("a:proxy.route_prefix")`
- `read_plain("b:default_bundle_id")`

Mounted runtime paths:

- `/config/assembly.yaml`
- `/config/bundles.yaml`

## 3. Dot-path rules

Traversal is dot-separated.

Examples:

- `storage.workspace.type`
- `storage.claude_code_session.repo`
- `bundles.my.bundle@1.0.0.widgets.0.alias`

Supported behavior:

- nested object traversal
- list indexing by numeric segment
- dotted YAML keys, such as bundle ids containing `.` characters

That means lookups like this are valid:

```python
read_plain("b:bundles.demo.bundle@1.0.0.widgets.0.alias")
```

If the path is missing, the helper returns the provided default.

## 4. When to use `get_settings()` vs `read_plain(...)`

Prefer `get_settings()` when:

- the value is already rendered into env
- the service should consume the normalized runtime contract
- the setting is part of stable service configuration

Use `read_plain(...)` when:

- code needs the descriptor value itself
- the value is not rendered into env
- you want the runtime to inspect `assembly.yaml` or `bundles.yaml` directly
- you want to use dot-path access without introducing new env vars

Good examples for `read_plain(...)`:

- reading optional `assembly.yaml` feature flags or nested metadata
- reading bundle registry metadata from `bundles.yaml`
- reading bundle config that should stay descriptor-shaped rather than env-shaped

Do not use `read_plain(...)` for:

- secrets
- API keys
- passwords
- tokens

Those still belong behind `get_secret(...)`.

## 5. Mounting contract

Runtime plain reads only work if the descriptor files are mounted into the service.

### Docker Compose

In compose, both descriptors are mounted into:

- ingress
- proc
- metrics

at:

- `/config/assembly.yaml`
- `/config/bundles.yaml`

### ECS / Terraform

In ECS, any service that calls `read_plain(...)` must receive the shared `/config` mount.

Current rule:

- proc must have `/config`
- ingress must also have `/config` if ingress code uses `read_plain(...)`
- metrics must also have `/config` if metrics code uses `read_plain(...)`

This mount contract is independent from `AGENTIC_BUNDLES_JSON`.

Important distinction:

- `AGENTIC_BUNDLES_JSON` selects the active bundle-registry source for proc startup
- `/config/assembly.yaml` and `/config/bundles.yaml` are broader runtime-readable descriptor files

So even when proc seeds the registry from `/config/bundles.yaml`, the assembly file should still stay mounted for plain runtime reads.

## 6. Descriptor source of truth

The runtime should treat descriptor reads as read-only configuration access.

- `assembly.yaml` = platform-level plain configuration
- `bundles.yaml` = bundle registry and plain bundle metadata

Neither should be used as a writable runtime datastore.

## 7. Related settings already rendered from assembly

Some values are still promoted from `assembly.yaml` into env by the CLI or deployment layer, for example:

- `storage.workspace.type` -> `REACT_WORKSPACE_IMPLEMENTATION`
- `storage.workspace.repo` -> `REACT_WORKSPACE_GIT_REPO`
- `storage.claude_code_session.type` -> `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION`
- `storage.claude_code_session.repo` -> `CLAUDE_CODE_SESSION_GIT_REPO`

So code may choose either:

- normalized env-backed `get_settings()`
- direct descriptor-backed `read_plain(...)`

depending on whether it needs the rendered runtime contract or the descriptor itself.
