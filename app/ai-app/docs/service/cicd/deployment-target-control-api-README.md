---
id: repo:kdcube/app/ai-app/docs/service/cicd/deployment-target-control-api-README.md
title: "KDCube Deployment Target Control API"
summary: "Documents the supported typed Python API for selecting a KDCube deployment target, inspecting local applications, controlling local lifecycle, and resolving local or remote application surfaces without importing CLI internals."
updated_at: 2026-09-11
tags: ["service", "cicd", "cli", "python-api", "deployment-target", "application-control"]
keywords: ["kdcube_cli.control", "local deployment target", "endpoint deployment target", "application surface", "structured control error", "Connection Hub CLI"]
see_also:
  - repo:kdcube/app/ai-app/docs/service/cicd/cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/delegated-management-service-README.md
  - repo:kdcube/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube/app/ai-app/docs/arch/security-and-trust-model-README.md
---
# KDCube Deployment Target Control API

`kdcube_cli.control` is the supported Python boundary for a product CLI that
needs to locate a KDCube app server, inspect its installed applications, manage
a supported local lifecycle, prepare a released platform source tree, or
resolve an application surface. The `kdcube` executable uses the same boundary
for local discovery, `info`, prepare-only initialization, `start`, and `stop`.

The first distribution version carrying this boundary is
`kdcube-cli==2026.09.02.1733`. Before that package is published, coordinated
consumers must install this KDCube source checkout rather than depending on an
older package with the same imports absent.

The boundary returns immutable typed data and raises `KDCubeControlError`
subclasses. Argument parsing, Rich rendering, prompts, and `SystemExit` remain
in the executable layer.

## Target contract

`DeploymentTargetRef` represents either:

- a local namespaced runtime with a filesystem `workdir`; or
- an endpoint-only target with an HTTP(S) endpoint and no filesystem or Docker
  assumption.

Every adapter implements the runtime-checkable `DeploymentTarget` protocol:
`reference`, `capabilities`, `describe()`, surface resolution, URL resolution,
and browser open. `describe()` is the common non-mutating inspection boundary.
For a local target it returns the available runtime status; for an
endpoint-only target, reachability and initialization remain unknown because
no remote status protocol is claimed. A downstream test double can implement
this protocol without exposing a workdir or Docker behavior.

Ask `target.capabilities.supports(...)` before an operation. Unsupported
operations fail closed with `UnsupportedCapabilityError`.

| Capability | Local target | Endpoint-only target |
| --- | --- | --- |
| initialize | prepare a descriptor-owned runtime | unsupported |
| start / stop | Docker Compose lifecycle | unsupported |
| refresh | unsupported by this first library slice; the executable still composes existing steps | unsupported |
| descriptor changes | unsupported | unsupported |
| application reload | unsupported | unsupported |
| logs | unsupported | unsupported |
| status | local filesystem, descriptor, application, release, and optional Docker status | unsupported |
| resolve endpoints | installed descriptor inventory | explicit app/surface coordinates |
| open | browser UI surfaces | explicit browser UI surface |

The current endpoint-only adapter resolves URLs for an already deployed app.
It constructs the standard routes from endpoint, tenant, project, application,
surface kind, and alias, then may probe those routes for reachability. It does
not send an administration credential or call a remote management API. The
human signs in to the browser application through the deployment's configured
identity provider.

Remote administration uses the same delegated-authority model as other
protected KDCube surfaces. A delegated access card is KDCube-recognized
authority created or approved by a grantor who already carries authority in
that deployment. Its authority is the exact set of resources and operations
written into the live card. The public
[Delegated KDCube Management Service](delegated-management-service-README.md)
publishes deployment inspection, application-surface discovery, and exact
application reload. A grantor can place selected operations on a caller
profile for the Connection Hub CLI.

The complete intended interaction is:

1. The user tells the CLI which running KDCube endpoint to use.
2. The CLI opens that deployment's authorization page in the browser.
3. The user signs in with the deployment's configured identity provider.
4. KDCube verifies that the user may delegate the requested management
   operations. The user approves a caller profile containing the target
   deployment and selected operations.
5. The browser returns an authorization code to the CLI through Authorization
   Code with PKCE. The CLI exchanges it for credentials bound to that card and
   keeps them in the operating-system credential store.
6. A person, agent, or automation invokes the CLI. The management service
   authenticates the caller credential and resolves the current card on every
   operation.
7. A missing operation returns a structured consent requirement. The user may
   grant it in the browser; a later retry observes the updated card. Revocation
   applies on the next call as well.

This does not require a second kind of authority. A caller card authorizes
KDCube management when its resource grants include those management
operations. Existing caller profiles remain bounded to the grants they already
hold.

The target must be reachable for browser login, card resolution, and the
management request. Starting or recovering a completely stopped deployment
uses its local or infrastructure control plane. Once KDCube is running, the
implemented first slice exposes bounded inspection and exact application
reload through the delegated management surface.

The current `EndpointDeploymentTarget` adapter still implements endpoint route
resolution and probing only. The separate Connection Hub CLI consumes the
public management protocol. Integrating that protocol into
`kdcube_cli.control`, and adding configuration, logs, or broader lifecycle
operations, remain later control-library work.

## Local application resolution

The local target reads the staged, descriptor-owned `assembly.yaml`,
`bundles.yaml`, and `install-meta.json`. It derives public routes from declared
KDCube surface coordinates:

- `config.ui.widgets.<alias>` and `surfaces.as_provider.widget.<alias>` become
  `/public/widgets/<alias>`;
- `surfaces.as_provider.mcp.<alias>` becomes `/public/mcp/<alias>`;
- `surfaces.as_provider.api.<alias>` becomes `/operations/<alias>`;
- `config.ui.main_view` becomes the bundle static main-view route.

Application inventory accepts both the current `bundles: {items: ...}` wrapper
and the earlier unwrapped `items:` catalog retained by the executable's info
path.

The API does not inspect application source to guess product intent. When an
application has multiple browser surfaces, pass a `SurfaceSelector`; otherwise
resolution raises `AmbiguousApplicationSurfaceError` with candidate surface
identifiers.

```python
from pathlib import Path

from kdcube_cli.control import (
    ApplicationRef,
    LocalDeploymentTarget,
    SurfaceKind,
    SurfaceSelector,
    select_local_target,
)

reference = select_local_target(
    Path.home() / ".kdcube/kdcube-runtime/demo-tenant__demo-project"
)
target = LocalDeploymentTarget(reference)
surface = target.resolve_surface(
    ApplicationRef("connection-hub@1-0"),
    SurfaceSelector(kind=SurfaceKind.WIDGET, alias="connections_settings"),
)
print(surface.url)
```

The complete runnable example is
`kdcube_cli.examples.resolve_application_surface`.

## Endpoint-only resolution

An endpoint target requires tenant, project, app, surface kind, and alias. It
constructs standard KDCube coordinates and marks the surface `declared=False`
because this first target has no remote inventory protocol.

```python
from kdcube_cli.control import (
    ApplicationRef,
    DeploymentTargetRef,
    EndpointDeploymentTarget,
    SurfaceKind,
    SurfaceSelector,
)

target = EndpointDeploymentTarget(
    DeploymentTargetRef.endpoint_target(
        "https://runtime.example",
        tenant="acme",
        project="prod",
    )
)
url = target.application_url(
    ApplicationRef("connection-hub@1-0"),
    SurfaceSelector(kind=SurfaceKind.WIDGET, alias="connections_settings"),
)
```

## Initialization and lifecycle

For a fresh local target, `LocalDeploymentTarget.prepare_source()` creates or
reuses the managed `<workdir>/repo` Git checkout. A
`LocalPlatformSourceRequest` selects one explicit release, the release named by
`origin/main:release.yaml`, or current `origin/main` source. It returns
`PreparedPlatformSource` with the repository root, canonical descriptor
directory, resolved ref, and install mode. An occupied non-repository source
directory fails closed instead of being replaced.

`LocalDeploymentTarget.initialize()` takes a `LocalInitializationRequest`.
Configuration comes from the request's descriptor directory, or from the
selected platform repository's deployment descriptors. The operation prepares
the runtime and writes its staged runtime configuration; it does not start
Docker. Call `start()` explicitly after initialization.

For Google login, pass `auth_type="bundle"`, `auth_provider="google"`, and the
public OAuth Web application client ID in `auth_client_id`. An optional
`bootstrap_admin_email` grants the verified Google account initial platform
administration. The initializer stages `auth.type: bundle` and removes any
redundant `auth.idp` value before the non-interactive installer runs. This is the
maintained default used by the Connection Hub local setup.

`auth_type="simple"` remains available as an explicit local-development login.
Shared or remote deployments keep authentication in their selected descriptors
and deployment process.

`start()` and `stop()` preserve the local single-active-runtime lock and return
`OperationResult`. Callers can receive typed progress and command events through
an optional event sink. The API itself does not print.

## Errors and diagnostics

| Exception | Stable code |
| --- | --- |
| `UnsupportedCapabilityError` | `target.unsupported_capability` |
| `MissingTargetError` | `target.missing` |
| `AmbiguousTargetError` | `target.ambiguous` |
| `InvalidDescriptorError` | `descriptor.invalid` |
| `DockerUnavailableError` | `docker.unavailable` |
| `OperationFailedError` | `operation.failed` |
| `ApplicationNotFoundError` | `application.missing` |
| `ApplicationSurfaceNotFoundError` | `application.surface_missing` |
| `AmbiguousApplicationSurfaceError` | `application.surface_ambiguous` |

Exceptions and `Diagnostic` records contain a code, summary, and bounded
recovery coordinates. They do not carry descriptor secret values or raw
process output. `ApplicationStatus.source_ref` contains only the descriptor's
bundle release ref; repository URLs are not copied into public status.
