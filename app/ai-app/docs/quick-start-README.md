---
id: repo:kdcube/app/ai-app/docs/quick-start-README.md
title: "Quick Start: Run KDCube Locally"
summary: "Install the published KDCube CLI, initialize a tenant/project from the latest release with Google-backed login by default, start the local Docker Compose runtime, and understand the generated workdir."
status: active
tags: [docs, quickstart, local, docker-compose, cli, authentication]
keywords: [install kdcube, kdcube init, local runtime, latest release, Google login, tenant project, runtime workdir]
updated_at: 2026-09-06
see_also:
  - repo:kdcube/app/ai-app/docs/what-you-can-do-with-kdcube-README.md
  - repo:kdcube/app/ai-app/docs/recipes/operations/install-clean-README.md
  - repo:kdcube/app/ai-app/docs/recipes/operations/install-from-descriptors-README.md
  - repo:kdcube/app/ai-app/docs/recipes/operations/operate-runtime-README.md
  - repo:kdcube/app/ai-app/docs/service/cicd/cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secrets-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secret-management-cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/host-vault-README.md
  - repo:kdcube/app/ai-app/docs/runtime/harness/README.md
  - repo:kdcube/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/quick-start-local.md
---

# Quick Start: Run KDCube Locally

This path installs a released KDCube platform and starts it locally with Docker
Compose. It does not require a KDCube source checkout or a CLI development
environment.

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose running
- Python 3.9 or newer and `pip`
- Git
- About 20 GB of free disk for images and local runtime data

KDCube uses Google login by default. For that path, create a Google OAuth
client of type **Web application** and add this authorized JavaScript origin:

```text
http://localhost:5173
```

Keep its client ID ready. A browser OAuth client ID is public configuration;
this login path does not use a Google client secret.

## 1. Install The CLI

```bash
python3 -m pip install --upgrade kdcube-cli
kdcube --help
```

## 2. Initialize A Runtime

Choose names for the deployment's tenant/project scope:

```bash
kdcube init --tenant acme --project local
```

In a terminal, `init` opens the first-run prompts. It:

1. uses the latest published KDCube release by default;
2. offers an authentication method, with Google login selected by default;
3. asks for the Google Web client ID and an optional bootstrap administrator
   email when Google is selected;
4. offers optional OpenAI, Anthropic, and private-Git credentials;
5. stages the platform and descriptors under the tenant/project workdir.

Press Enter to keep a default. Optional credentials can be left blank and
added later. KDCube stores provided service credentials in its server-side
secrets configuration, not in an app prompt or browser configuration.

`init` prepares the runtime and does not start containers. It is a first-time
operation and refuses to overwrite an initialized workdir.

### What bare `kdcube init` does

When run directly in a terminal, this also starts the first-run prompts:

```bash
kdcube init
```

It first asks for tenant and project, defaulting both to `default`, then follows
the same source, auth, and optional-secret flow above. When stdin/stdout are not
attached to a terminal, `init` does not prompt; automation must pass the target
and required auth fields explicitly.

### Select The Platform Version

No source flag means the latest published release. Use one of these only when
you want a different source:

```bash
# Make the default explicit.
kdcube init --tenant acme --project local --latest

# Install one exact release.
kdcube init --tenant acme --project local --release <release-ref>

# Build current upstream source instead of a release.
kdcube init --tenant acme --project local --upstream --build
```

### Select A Different Auth Method

The prompt offers these first-run choices:

| Choice | CLI value | Intended use |
| --- | --- | --- |
| Google login (default) | `bundle` | The workspace app verifies Google identity and Connection Hub issues the KDCube session. |
| SimpleIDP development login | `simple` | Local development and demos only. |
| Amazon Cognito | `cognito` | A deployment using an existing Cognito user pool. |

You can select a method without the prompt:

```bash
kdcube init --tenant acme --project local \
  --auth-type bundle \
  --client-id "$GOOGLE_CLIENT_ID" \
  --bootstrap-admin-email "admin@example.com"
```

For a scripted install, add `--non-interactive` and supply every required
provider value as a flag or in a descriptor set:

```bash
kdcube init --tenant acme --project local \
  --non-interactive --latest \
  --auth-type bundle \
  --client-id "$GOOGLE_CLIENT_ID" \
  --bootstrap-admin-email "admin@example.com"
```

For Cognito fields and descriptor-driven automation, use the
[CLI reference](service/cicd/cli-README.md) and
[Install From Descriptors](recipes/operations/install-from-descriptors-README.md).

## 3. Start KDCube

Use the same tenant/project values:

```bash
kdcube start --tenant acme --project local
```

The first start may pull platform images and initialize local services. The CLI
prints the actual UI URL; with the shipped local descriptor it is:

```text
http://localhost:5173/platform/chat
```

The base installation includes the workspace app, Connection Hub, managed
KDCube services, and user memory. From there you can use the native ReAct
Agent, connect an existing agent, or add ordinary application surfaces without
an agent.

To use Connection Hub from an external agent, continue with
[Run Connection Hub Locally With KDCube](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/quick-start-local.md).
That workflow connects an external MCP, creates a caller profile with exact
tool permissions, provides the proxy endpoint and one-time credential, and
proves live narrowing or revocation from the running client.

### Try One SDK Component Directly

The source checkout also provides smaller executable paths:

- [Build and run on-premises agents with the KDCube Harness](../../../agents/README.md):
  start with the included Native ReAct agent, LangGraph, or Claude Code and
  compose durable conversations, selected tools and skills, isolated code,
  files, rendering, streaming, and accounting on infrastructure you control.
  The examples run directly from Python with independent Redis and Postgres
  services. The full command contract is
  [Run the Agent Harness from Python](recipes/quickstart/run-agent-harness-from-python-README.md).
- [Web Search MCP](../../../mcp/web-search/README.md) starts KDCube Web Search
  as an operator-filtered stdio, HTTP, or SSE MCP server.

These component paths run as standalone local processes with their own support
services. Continue with this Quick Start for the full multi-user runtime and
its authentication, tool-execution enforcement, managed tools, isolated
workspaces, and app hosting.

## What `init` Creates

The runtime lives here:

```text
~/.kdcube/kdcube-runtime/<tenant>__<project>/
```

For the example above:

```text
~/.kdcube/kdcube-runtime/acme__local/
|-- config/   # authoritative descriptors, generated runtime env, install metadata
|-- repo/     # exact staged KDCube platform source used by this runtime
|-- data/     # tenant/project-scoped local service and application data
`-- logs/     # local runtime logs
```

The important configuration files are under `config/`:

| File | Owns |
| --- | --- |
| `assembly.yaml` | deployment scope, auth, ports, infrastructure, storage, and platform version |
| `bundles.yaml` | installed apps (`bundle` is the CLI/config alias), app properties, and provided/consumed surfaces |
| `secrets.yaml` | canonical `platform` and `users` secret namespaces |
| `bundles.secrets.yaml` | deployment-app secret values |
| `economics.yaml` | plans, prices, budgets, and usage policy |
| `gateway.yaml` | gateway capacity and process limits |

Configuration is descriptor-owned. Edit or apply these descriptors; generated
Redis state and container files are derived views.

## Normal Local Operations

```bash
# Inspect the selected release, paths, and running services.
kdcube info --tenant acme --project local

# Stop and start the same deployment.
kdcube stop --tenant acme --project local
kdcube start --tenant acme --project local

# Move an initialized runtime to the latest release and restart it.
kdcube refresh --tenant acme --project local --latest
```

Use `refresh`, not `init`, after the workdir exists. `refresh` preserves the
staged descriptors. See [Operate A KDCube Runtime](recipes/operations/operate-runtime-README.md)
for configuration updates, app reloads, logs, and cleanup.

After the runtime is available, `kdcube secrets metadata|get|set|delete`
manages exact logical keys through its selected provider, and
`kdcube secrets export` performs an administrator-confirmed one-use selected or
whole descriptor export. `kdcube config export --include-platform-descriptors`
places the complete private pair beside the ordinary descriptors; `config
import` bootstraps a file-backed runtime or upserts into an existing provider-
backed runtime while preserving that target's backend identity.
See [Manage KDCube Secrets](service/secrets/secret-management-cli-README.md)
for local and remote commands, delegated authority, and safe input/output.

An existing descriptor set with unqualified platform roots must be migrated
before refresh:

```bash
kdcube secrets namespace migrate --tenant acme --project local --dry-run
kdcube secrets namespace migrate --tenant acme --project local --yes
```

### Optional: Move Local Secrets To The Host Vault

The first local run deliberately uses the file-backed secret provider so the
runtime can bootstrap from `secrets.yaml` and `bundles.secrets.yaml`. Current
KDCube source also includes an opt-in durable host-vault path. It keeps the
same `ISecretsManager` contract used by apps and moves the active source of
truth behind the private `kdcube-secrets` broker.

This is a staged transition, not a single descriptor toggle:

```text
files active -> host vault enrolled -> shadow copy verified
             -> transactional activation -> host vault active
```

After the vault service and deployment identity are provisioned, configure
these non-secret fields in the runtime's `config/assembly.yaml`:

```yaml
secrets:
  provider: secrets-file
  service:
    backend: host-vault
    host_vault:
      address: host.docker.internal:7781
      server_name: host.docker.internal
      identity_dir: /absolute/path/outside-the-runtime-workdir

platform:
  services:
    proc:
      exec:
        py_code_exec_network_mode: auto
```

From this point the vault is a startup dependency of the runtime: `chat-ingress`
and `chat-proc` wait for the `kdcube-secrets` health check, and that check fails
while the vault is unreachable. A source-operated vault is a plain host process
that a reboot stops. When the vault runs on the same machine as the same user,
add `local_service` under `host_vault` so `kdcube start` and `kdcube refresh`
start it when nothing listens:

```yaml
      local_service:
        home: /absolute/path/to/vault-home
        python: /absolute/path/to/host-vault-venv/bin/python
        bind: "127.0.0.1"     # macOS default. Required on Linux, see the Host Vault page. Not supported on Windows.
```

Without `local_service` the same commands stop before Compose and name the
vault as the cause.

Then prepare the shadow broker, verify the copy, and activate through the CLI:

```bash
kdcube secrets backend host-vault prepare --tenant acme --project local --dry-run
kdcube secrets backend host-vault prepare --tenant acme --project local
kdcube secrets backend host-vault stage --tenant acme --project local --dry-run
kdcube secrets backend host-vault stage --tenant acme --project local
kdcube secrets backend host-vault activate --tenant acme --project local --dry-run
kdcube secrets backend host-vault activate --tenant acme --project local --yes
```

`prepare` projects only the descriptor-owned Host Vault fields into the
generated Compose environment and recreates only `kdcube-secrets`; file-backed
consumers remain active. Activation changes `secrets.provider` to
`secrets-service`, recreates the
affected consumers, verifies real reads, and rolls back ordinary failures.
The activation command is the supported provider-switch boundary because it
includes parity, ordering, verification, and rollback. The host service
provisioning, certificate enrollment, field meanings, recovery command,
security boundary, and current release gates are owned by
[Host Vault for Provider Secrets](service/secrets/host-vault-README.md). The
[CLI reference](service/cicd/cli-README.md#host-vault-lifecycle) owns the
command contract. Until a released `kdcube-cli` contains the
`secrets backend host-vault` command group, use this optional flow from a
source-installed CLI that matches the staged platform source.

## Build Or Connect An App

An app can expose any combination of REST, MCP, chat, UI, event, and job
surfaces, and it can consume tools, named services, connected accounts, or
external MCP servers. The literal CLI and descriptor term for the deployable
app package is `bundle`.

An app can use the native ReAct Agent or host LangGraph, Claude Code, and other
framework-owned agent runtimes through KDCube adapters. Each adapter binds the
[Agent Harness Runtime](runtime/harness/README.md) facilities that workflow
needs. The architecture calls this the hosted foreign-runtime path; the host
layer is runtime-agnostic, while LangGraph and Claude Code are worked adapters.
Direct app-owned Claude Code execution is also supported without requiring the
conversational harness binding.

Start with:

- [What You Can Do With KDCube](what-you-can-do-with-kdcube-README.md)
- [How To Write An App](sdk/bundle/build/how-to-write-bundle-README.md)
- [How To Configure And Run An App](sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

For Claude Code, the published
[KDCube plugin](https://github.com/kdcube/agent-plugins/tree/main/plugins/claude/kdcube)
packages runtime bootstrap, app scaffolding, configuration, testing, release,
and an offline documentation set.
