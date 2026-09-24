---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/app-deployment-and-static-widget-delivery-README.md
title: "App Deployment And Static Widget Delivery"
summary: "Fleet-coordinated app-resource deployment and policy-aware static widget serving from prepared local or shared filesystem artifacts."
tags: ["sdk", "bundle", "app", "deployment", "widget", "static-ui", "authorization", "readiness", "efs"]
keywords: ["on_app_deploy", "app resource barrier", "static widget delivery mode", "predeployed widget", "policy manifest", "legacy shadow deployed", "side-effect-free widget request", "local filesystem", "EFS", "role protected widget"]
updated_at: 2026-08-18
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/ui-components-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/surfaces/as-provider-surfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/application-startup-health-and-readiness-README.md
---
# App Deployment And Static Widget Delivery

KDCube prepares app-owned shared resources before admitting the app. The
`on_app_deploy` hook is the fleet-coordinated resource barrier for one exact
source/config/runtime generation. Static UI compilation and policy-manifest
publication are optional participants in that barrier.

Widget files remain in the app's configured storage and are served by proc.
Authorization therefore remains in the normal application surface instead of
being delegated to an unguarded public object-store origin.

## Select The Static Delivery Mode

Configure the mode in `assembly.yaml`:

```yaml
platform:
  services:
    proc:
      bundles:
        static_widget_delivery_mode: deployed
```

| Mode | Preparation behavior | Request behavior |
| --- | --- | --- |
| `legacy` | App preparation builds configured UI; no deployed policy manifest is published. | Proc resolves the ready app and serves its prepared static files through the established route. |
| `shadow` | App preparation also publishes the static-surface policy manifest. | Proc serves through the legacy route and marks the response `legacy-shadow`. |
| `deployed` | Same manifest and artifact preparation as shadow. | Proc prefers the bounded manifest path; a missing/stale manifest falls back to prepared legacy serving. |

All modes require app readiness before serving. No mode imports an app for
repair, scans source trees, or runs npm/Vite from the browser request.

## App Resource Barrier

The lifecycle is:

```text
desired app generation
        |
        v
process-local on_bundle_load             every proc process
        |
        v
shared app-resource signature + lock     local shared FS or EFS
        |
        v
on_app_deploy                            once per shared generation
        |
        +-- app-owned catalogs, schemas, indexes, projections, assets
        |
        +-- configured UI ensure         when UI exists
        |
        +-- policy manifest              shadow/deployed only
        |
        v
mark desired app generation ready        process-local admission fact
```

`on_app_deploy` is optional app code but the resource-barrier phase is part of
every app's supervised preparation. The hook must be async, idempotent, and
retry-safe. Its shared signature prevents unchanged resource publication from
repeating; its lock heartbeat and TTL permit recovery after an interrupted
owner.

`on_bundle_load` remains process-local and runs in every proc process. Shared
completion cannot substitute for another process's local initialization.

## Generation Inputs

The shared resource generation includes:

- app identity and source declaration;
- resolved Git commit and source fingerprint;
- descriptor props fingerprint;
- runtime/resource schema generation.

A source edit, immutable ref change, policy change, build config change, or
relevant runtime release therefore creates a new resource identity. Completion
for the prior identity cannot mark the desired app generation ready.

## Published Static State

Each app receives generated state under its normal storage root:

```text
<bundle-storage>/<tenant>/<project>/<app-id>/
  ui/widgets/<alias>/...
  .kdcube.app-deployment/
    app-resources.v1.signature
    static-widget-surfaces.v1.json
    static-widget-surfaces.v1.signature
```

Local deployments use configured local bundle storage. Shared proc deployments
use the shared filesystem mounted as bundle storage, such as EFS. Static app
deployment has no S3 requirement.

The policy manifest contains:

- app source generation and descriptor-props fingerprint;
- resolved widget `user_types`, roles, enabled state, and authority/grant
  policy;
- relative prepared artifact directory;
- build and deployment signatures.

It contains no app secrets, user credentials, or provider tokens.

## UI Build Publication

The UI build contract remains independent of request lifetime:

1. Compute component and shared-source signatures.
2. Acquire the shared-storage resource lock.
3. Copy source into a worker-local build tree.
4. Run npm/Vite in a dedicated process group; transient `node_modules` remain
   worker-local.
5. Write output into a shared temporary directory.
6. Require `index.html` and atomically replace the final artifact.
7. Write the completion signature.
8. Release the shared lock.
9. Clean worker-local source after publication and lock release.

Timeout, generation supersession, and proc shutdown terminate and reap the
entire build process group. A hard proc death stops the lock heartbeat; another
worker can retry after lock expiry.

See [UI Components Lifecycle](ui-components-lifecycle-README.md) for exact
paths, signatures, lock behavior, and author requirements.

## Deployed Request Path

For `static_widget_delivery_mode: deployed`, proc handles a widget request in
this order:

1. Resolve tenant, project, and app from the active registry.
2. Require the desired app generation to be ready in this proc.
3. Read the small generated policy manifest.
4. Match its source generation and descriptor-props fingerprint to current
   authority state. The source generation comes from the app's declared
   coordinates, so every proc process computes the same value: the declared
   location and, for an app pinned with `activation.commit`, that commit. A
   pinned app loads a snapshot in the managed root and is matched by its
   commit and declared path, never by the snapshot's own path. A commit named
   only in a reload request is known to the process that prepared it, so a
   durable pin belongs in the descriptor as the full commit.
5. Enforce app roles plus widget user type, role, enabled, and authority-grant
   policy.
6. Serve `index.html` or an asset from the declared prepared directory.

A policy denial returns `403` or `404`; it does not fall through. A missing or
stale deployment manifest may use prepared legacy serving, which still enforces
the app surface and still does no build work. Missing built output after the app
is ready is an artifact invariant failure.

Responses identify the selected serving path:

```text
X-KDCube-Widget-Delivery: deployed
X-KDCube-Widget-Delivery: legacy-fallback
X-KDCube-Widget-Delivery: legacy-shadow
X-KDCube-Widget-Delivery: legacy
```

Successful deployed responses also carry a short
`X-KDCube-App-Deployment` signature. Authenticated widget assets use private
browser caching; only the explicit `/public/widgets/...` route emits public
cache directives.

## Startup And Live Changes

Proc schedules every active app generation during startup without waiting for
the complete app set to finish. Registry and props updates supersede only the
changed app:

- Bundle Admin Save persists authority, updates the full registry, invalidates
  the changed app, and schedules its replacement generation.
- `bundles.update` carries changed app ids to other proc processes.
- `bundles.props.update` creates a new props/resource generation.
- explicit Reload replays authority and force-prepares the selected app.

Admin requests return after publishing desired state. The lifecycle supervisor
owns source materialization, hooks, UI build, retries, and cancellation.

Direct local source edits become active through the supported app reload or
runtime refresh. Browser requests deliberately do not poll source trees.

## Readiness And Failure

The app is marked ready only after process-local load and required shared
resource publication succeed. A failure publishes no ready generation and no
new active static manifest. The app enters retrying state under bounded
backoff; its doors return the structured retryable `application_not_ready`
diagnosis.

Only apps declared `service.readiness: required` affect aggregate proc
`GET /health`. Independent apps remain unavailable only through their own
doors while preparing.

The full endpoint, queue-deferral, retry, and deployment-adapter contract is in
[Application Startup, Health, And Readiness](../../arch/proc/application-startup-health-and-readiness-README.md).

## Verification

1. Configure one widget and start proc. Confirm the app moves from preparing to
   ready and the artifact/signature exist before the widget returns `200`.
2. Set `shadow`. Confirm the manifest exists and the response identifies
   `legacy-shadow`.
3. Set `deployed`. Confirm the response identifies `deployed` and a request
   performs no workflow construction, source scan, or build command.
4. Test a role-restricted widget with allowed and denied users. The denied user
   must receive the same policy denial in legacy, shadow, and deployed serving.
5. Change the app ref or widget props. Confirm Save returns before compilation,
   the app becomes pending/preparing, and the replacement artifact is published
   before readiness returns.
6. Fail the build command. Confirm no new signature or ready generation is
   published, the build process group is reaped, and supervisor diagnostics
   show bounded retry state.
