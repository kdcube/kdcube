---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
title: "Security And Trust Model"
summary: "Canonical KDCube security model: one tenant/project runtime, trusted applications, request-scoped users, profile-dependent generated-code isolation, server-side credentials, and guarded REST/MCP surfaces."
status: current
tags: ["arch", "security", "trust", "tenancy", "apps", "execution", "credentials", "mcp"]
updated_at: 2026-07-18
keywords: ["KDCube security model", "tenant/project deployment scope", "multi-user runtime", "trusted application", "generated-code isolation", "MCP security", "secret isolation"]
see_also:
  - repo:kdcube-ai-app/SECURITY.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/tenant-project-user-and-execution-boundaries-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-iso-runtime.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-runtime-modes-builtin-tools.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
---
# Security And Trust Model

This page is the canonical statement of what KDCube isolates, what it trusts,
and which guarantees depend on deployment configuration.

## What KDCube Is

KDCube is a self-hosted application runtime and SDK. It loads
operator-selected application code and gives that code declared product
surfaces: agents, tools, jobs, APIs, UI, websites, events, named services,
REST, and MCP.

This repository is not one installable MCP server and does not receive blanket
access to a user's workstation. An application may expose or consume an MCP
surface, but that is one configured interface inside a KDCube deployment.

## Boundary Map

```text
platform operator
  chooses deployment, identity, applications, refs, secrets, and policy
       |
       v
+------------------------------------------------------------------+
| one KDCube runtime deployment                                    |
| effective scope: tenant T + project P                            |
|                                                                  |
| many authenticated users                                         |
|   request identity + authority + app/conversation/turn lineage   |
|                                                                  |
| trusted application and supervisor code                          |
|   operator-approved source, SDK services, guarded tools          |
|          |                                                       |
|          | narrow requests and materialized inputs               |
|          v                                                       |
| generated/untrusted code boundary                                |
|   local subprocess | legacy Docker combined | Docker split       |
|   remote Fargate task profile                                    |
+------------------------------------------------------------------+
       |
       +-- namespaced PostgreSQL / Redis / object or file storage
       +-- server-side secret providers and connected accounts
       `-- explicitly allowed external services
```

These are different boundaries. A tenant/project namespace is not a generated
code sandbox. A generated code sandbox is not an application trust decision.
An authenticated user is not automatically authorized for every application
operation.

## Deployment Scope And Shared Users

KDCube supports a platform composed of many tenant/project environments. The
current runtime binding is:

```text
one running deployment -> one effective tenant/project
one tenant/project      -> many users and many applications
```

PostgreSQL, Redis, object storage, and shared filesystems may be dedicated to a
deployment or shared by several deployments. When infrastructure is shared,
KDCube services preserve tenant/project scope through schemas, namespaces,
keys, prefixes, and service-owned lookup contracts.

This tenant-aware data organization is suitable for a control plane that
provisions, discovers, upgrades, and observes many tenant/project-scoped
deployments. It does not change the runtime rule above: one running deployment
still binds one effective tenant/project.

Inside one tenant/project deployment, many users may share processes, queues,
connection pools, and filesystem infrastructure. User separation therefore
depends on authenticated request context and scoped service contracts, not on
one operating-system process per user.

This model does **not** claim that mutually hostile platform operators or
unreviewed application backends can safely share one processor. Use separate
deployments, accounts, networks, or backing services when the required
boundary is infrastructure-level isolation.

## Applications Are Trusted Deployment Code

An application is selected by an operator through a descriptor and source
reference. Its backend may execute inside a processor with the privileges of
that service. The application is therefore comparable to an installed backend
plugin, not to model-generated code.

```text
operator reviews and pins source
             |
             v
descriptor -> application loader -> trusted processor code
```

Applications should use request-scoped SDK services for storage, credentials,
authority, economics, and cross-runtime calls. Custom trusted code can still
bypass those contracts if it deliberately reaches ambient process resources.
KDCube cannot reconstruct a security boundary after trusted application code
has bypassed it.

Production operators should review application source, pin immutable refs,
grant the processor least privilege, and place unrelated or mutually
untrusted applications in separate deployments.

## Users Are Isolated By Carried Context And Scoped Services

Ingress authenticates the actor and binds a request context. The context
carries tenant, project, user, authority, application, conversation, agent,
and turn lineage across async tasks, threads, subprocesses, application calls,
and Data Bus work where applicable.

```text
verified actor
    |
    v
bound request context
    |
    +-> storage lookup is scoped
    +-> credential lookup is scoped
    +-> operation guard is scoped
    +-> accounting attribution is scoped
    `-> cross-runtime context is serialized explicitly
```

The context is situation and policy, not a bag of credentials. Services must
bind and verify the relevant scope at their boundary. See
[Tenant, Project, User, Authority, And Execution Boundaries](../runtime/tenant-project-user-and-execution-boundaries-README.md)
for the subsystem-level contracts.

## Generated-Code Isolation Depends On The Runtime Profile

KDCube separates the trusted supervisor/tool side from model-generated or
otherwise untrusted code. The strength of that separation is selected by the
operator:

| Profile | Security meaning |
| --- | --- |
| In-process or explicitly non-isolated execution | No security boundary. Use only for trusted code. |
| Local subprocess | Process and crash containment for development; not a security sandbox and not a network boundary. |
| Docker combined (legacy, configurable) | Container boundary and filtered child environment, but supervisor and executor share one container and mount namespace. It is not the production reference for untrusted code. |
| Docker split (reference) | Strongest built-in profile: a separate executor container with narrow mounts, no platform secret store, and no network by default. |
| Fargate remote task | The trusted supervisor and generated-code child run in one remote task/container. The child receives filtered state, drops privileges, and creates a network namespace, but this is not split Docker's separate-container mount boundary. Assess the task definition, IAM, filesystem, networking, and child-process controls. |

The reference deployment descriptors explicitly select `split`. The runtime
still accepts `combined` for legacy deployments; operators should not rely on
an omitted or unrecognized strategy value to establish the stronger boundary.
Fargate is a separate remote profile and must not be described as split Docker.

The operator-selected profiles are a ceiling. Within that ceiling the isolation
profile is chosen per agent, and each tool declares where it runs — the trusted
supervisor process, a subprocess, or the isolated executor — so isolation is not
a single global switch.

For isolated execution, model-proposed paths and references are untrusted
requests. A trusted resolver first binds the current tenant/project/user and
authority, then materializes only selected bytes into a sparse workspace.
Approved provider or platform operations run as trusted tools under the
carried request identity.

```text
model proposes locator or action
            |
            v
trusted resolver / tool guard
  binds identity, authority, and allowed operation
            |
       +----+---------------------+
       |                          |
       v                          v
selected workspace bytes     server-side provider call
       |                      split executor receives no credential
       v
isolated executor
```

Use [ISO Runtime](../exec/README-iso-runtime.md) for the concrete process,
mount, network, and supervisor/executor contracts.

## Credentials Stay On The Trusted Side

Tracked deployment descriptors should contain configuration and secret
references, not production credentials. Secret values are resolved by
server-side providers or stores in the managed runtime path.

Connected external accounts follow the same rule. Connection Hub stores a
server-side credential record for the KDCube user. A trusted tool resolves the
credential only after matching the current user, provider, requested claims,
and operation. For a hosted or in-app agent this is the first of two gates: the
calling agent must also hold its own delegated-by grant (keyed to its client
identity, `kdcube-agent:<app>:<agent>`) for that connected account and claim. The
two lifecycles are independent — rotating a provider token does not touch the
agent grant, and revoking either gate stops the tool immediately. Connecting an
account does not authorize every agent, and an agent does not inherit the
accounts the user connected. The token should not be placed in model context,
generated source, browser configuration, or an executor workspace. See
[Agents Acting For The User](../sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).

```text
user consent -> Connection Hub record -> server-side credential store
                                             |
trusted tool + bound request + claims -------+
                                             |
                                             v
                                      external provider
```

This is a supervisor/tool-side guarantee, not a property of every execution
profile. The local subprocess implementation inherits the host process
environment and receives the portable runtime payload; it is for trusted
development work, not secret isolation. Legacy combined Docker filters the
generated-code child environment but keeps supervisor and executor in one
container/mount trust zone. Split Docker is the profile that also removes
supervisor descriptors, storage roots, and provider credentials from the
executor container.

## REST And MCP Are Explicit Surfaces

KDCube can host public, platform-authenticated, or delegated REST and MCP
surfaces. Their security comes from the declared guard and server-side grant
state, not from the transport name.

```text
client -> REST or MCP endpoint
             |
             v
       configured guard
       identity / token lookup
       resource + operation grants
             |
             v
       application operation
```

A public surface is intentionally public. A managed surface must reject a
credential whose server-side resource/operation grants do not match the
request. An MCP endpoint is not automatically privileged, and adding MCP
support to an application does not turn the whole KDCube repository into an
MCP connector.

## How To Review This Repository

An automated or manual assessment should record four inputs before assigning a
security conclusion:

```text
1. deployment topology   one runtime, backing services, network and IAM
2. trusted applications  source refs and processor privileges
3. execution profile     in-process, local, legacy combined, split, or remote
4. exposed surfaces      public/authenticated/delegated REST, MCP, UI, events
```

The presence of MCP code means that KDCube applications can provide or consume
MCP. It does not mean the repository is a single MCP plugin with ambient
client access. The presence of execution code likewise requires identifying
whether the code runs as trusted application logic or in the configured
generated-code executor.

Tracked deployment templates may declare the *shape* of a secret field.
Non-secret descriptors should leave its value empty or refer to the
server-side secret contract. A live deployment must be assessed against its
effective secret provider and infrastructure policy, not a local-development
placeholder.

## Security Claims At A Glance

KDCube provides:

- tenant/project-scoped runtime and storage contracts;
- request identity and authority propagation across supported runtime
  boundaries;
- server-side secret and connected-account credential resolution in managed
  trusted services;
- two-gate delegated access: a user's provider connection plus a per-agent grant
  scoped per connected account and claim, so an agent's access is explicit and
  independently revocable;
- explicit guards and grants for protected application surfaces;
- configurable generated-code isolation, including a split executor profile;
- per-request and per-operation records where the corresponding subsystem is
  configured to emit them.

KDCube does not claim:

- that arbitrary operator-installed application code is sandboxed;
- that local subprocess execution is a security sandbox;
- that every deployment uses dedicated infrastructure;
- that every route is authenticated without an explicit guard;
- that telemetry alone is an immutable or complete compliance audit;
- a compliance certification for a deployment assembled by an operator.

## Production Hardening Baseline

Before exposing a deployment:

1. Configure a production platform authority and protect non-public surfaces.
2. Store real credentials only in the configured secret provider/store.
3. Review application code and pin immutable source revisions.
4. Select split Docker for production execution of untrusted generated code;
   treat local and legacy combined profiles according to their documented
   weaker boundaries.
5. Remove executor network access and mounts that are not required.
6. Grant processors, storage, and external providers least privilege.
7. Configure TLS, secure cookies, allowed origins, and trusted proxy headers.
8. Separate deployments or backing services where logical namespacing is not
   a sufficient boundary.
9. Retain and monitor the runtime records required by the operator's policy.
10. Keep the platform and loaded applications patched.

For undisclosed vulnerabilities, follow the repository
[security reporting policy](../../../../SECURITY.md).
