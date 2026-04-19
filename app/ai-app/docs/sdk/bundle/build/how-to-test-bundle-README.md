---
id: ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
title: "How To Test A Bundle"
summary: "Operational testing guide for KDCube bundles: shared suite, bundle-local tests, widget/API checks, runtime reload, and bundle-specific failure diagnosis."
tags: ["sdk", "bundle", "testing", "pytest", "widget", "runtime", "validation"]
keywords: ["how to test bundle", "bundle suite", "run_bundle_suite", "bundle widget testing", "bundle reload", "bundle diagnostics"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/bundle-reference-versatile-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
  - ks:docs/sdk/bundle/bundle-ops-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
---
# How To Test A KDCube Bundle

This document is the operational test playbook for bundle builders.

The goal is not “run something once”.
The goal is to prove that the bundle works in the supported KDCube runtime contract.

Use this together with:

- [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
- [bundle-reference-versatile-README.md](../bundle-reference-versatile-README.md)
- [bundle-widget-integration-README.md](../bundle-widget-integration-README.md)
- [bundle-runtime-README.md](../bundle-runtime-README.md)
- [bundle-ops-README.md](../bundle-ops-README.md)

## 1. Testing Order

Run tests in this order:

1. syntax/import checks
2. shared SDK bundle suite
3. bundle-local pytest tests
4. isolated direct checks for generated HTML/builders if applicable
5. local runtime reload path
6. real widget/API/manual runtime checks

Do not jump straight to browser/manual testing.
That is the slowest feedback loop and the weakest signal.

## 2. Baseline Test Matrix

Every non-trivial bundle should be tested at these layers.

### 2.1 Quick commands by feature

Use these as the first actionable checks for each bundle surface.

### Syntax and imports

```bash
python -m py_compile /abs/path/to/bundle/entrypoint.py
```

### Shared bundle contract

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle
```

### Bundle-local tests

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
pytest -q /abs/path/to/bundle/tests
```

### Authenticated API

```bash
curl -X POST \
  "http://localhost:5173/api/integrations/bundles/<tenant>/<project>/<bundle_id>/operations/task-board-api" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"data":{"operation":"list","payload":{}}}'
```

### Public API with `header_secret`

```bash
curl -X POST \
  "http://localhost:5173/api/integrations/bundles/<tenant>/<project>/<bundle_id>/public/incoming_webhook" \
  -H "X-Webhook-Secret: <shared-secret>" \
  -H "Content-Type: application/json" \
  -d '{"event":"ping"}'
```

### Public MCP

```bash
curl -X POST \
  "http://localhost:5173/api/integrations/bundles/<tenant>/<project>/<bundle_id>/public/mcp/docs_public" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/list"}'
```

### Bundle-authenticated MCP

```bash
curl -X POST \
  "http://localhost:5173/api/integrations/bundles/<tenant>/<project>/<bundle_id>/mcp/docs" \
  -H "X-Docs-MCP-Token: <shared-token>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/list"}'
```

### Widget path sanity check

Open the widget, then verify in browser networking that it calls:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{alias}
```

If you see `////operations/...`, the widget is not correctly wired.

### Cron logic

Expose the cron body through a helper and test it directly:

```python
@cron(alias="sync", expr_config="task_tracker.sync", span="bundle")
async def sync(self, **kwargs):
    await self._sync_impl()
```

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app pytest -q /abs/path/to/bundle/tests -k sync
```

Reference map:
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
- [bundle-widget-integration-README.md](../bundle-widget-integration-README.md)
- [bundle-transports-README.md](../bundle-transports-README.md)
- [bundle-scheduled-jobs-README.md](../bundle-scheduled-jobs-README.md)

### A. Import and syntax layer

Verify that the bundle files can load.

Typical command:

```bash
python -m py_compile /abs/path/to/bundle/entrypoint.py
```

If the bundle includes helper modules that build complex HTML/JS strings inside Python:

- compile those files too
- then execute the HTML-builder functions directly

Why:

- `py_compile` catches syntax errors
- direct execution catches runtime f-string/template mistakes that compile cleanly but crash later

### B. Shared SDK bundle suite

Run the shared SDK bundle suite against the bundle.

Command:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle
```

This suite validates the shared bundle contract.

Use it for every bundle, not only for reference bundles.

### C. Bundle-local pytest tests

If the bundle has `tests/`, run them too.

Command:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
pytest -q /abs/path/to/bundle/tests
```

Write bundle-local tests for:

- bundle-specific transforms
- storage helpers
- business logic
- serialization and validation code
- widget data shaping

Do not rely only on the shared suite for bundle-specific behavior.

### D. Runtime integration checks

After unit/shared tests pass:

- load or reload the bundle in KDCube
- inspect widget/API discovery
- call operations through the real integrations routes
- verify actual runtime storage and mutation behavior

## 3. Runtime Surface Checks

Do not treat all runtime paths as equivalent.
They are not.

### A. Chat turn / SSE / socket path

Verify that request-bound logic behaves correctly when the bundle is entered through the normal processor path.

What to validate:

- request-bound code sees the expected `self.comm` / `self.comm_context`
- streaming or communicator-driven behavior works when the request is peer/session-bound
- actor-derived behavior is only exercised in this path, not assumed globally

### B. REST bundle operation path

Verify that request-bound bundle operations behave correctly through `/api/integrations/bundles/.../operations/...`.

What to validate:

- request-bound `self.comm` / `self.comm_context` are usable
- operation body parsing works with the expected payload shape
- widget-triggered operations are treated as request-bound entrypoint calls, not as detached background jobs

### C. Cron / scheduled-job path

Cron must be tested as a separate class of runtime.

What to validate:

- cron logic does not assume a real end-user actor
- cron logic does not depend on session/socket/request-header state
- cron code works with bundle props, storage, DB/Redis, and explicit scope only

If a bundle has `@cron(...)`, you should test at least one run path that invokes the scheduled logic directly.

### D. Isolated runtime path

If the bundle or its tools may execute in isolated runtime, test that path explicitly.

What to validate:

- code does not depend on arbitrary host-process globals
- only documented portable runtime bindings are assumed
- isolated execution gets the inputs it actually needs

Do not accept “works in process” as proof that isolated execution is correct.

### E. Singleton path

If the bundle is configured as singleton, test that explicitly too.

What to validate:

- the bundle reuses the cached instance as intended
- request-bound behavior still reads the current context correctly
- request state is not accidentally retained across calls

The question to answer is:

- “does singleton reuse break request correctness?”

not:

- “did singleton exist?”

### F. Exclusivity path

If the bundle has operations that must not overlap, test the exclusivity mechanism directly.

For cron:

- verify the selected `span` matches the intended exclusivity scope
- verify the job does not overlap inside the chosen scope

For non-cron operations:

- verify the explicit lock path, not just the happy-path operation result

Do not treat singleton configuration as proof of exclusivity.

## 4. Shared Suite Details

The shared suite runner is:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/run_bundle_suite.py`

It expects:

- a bundle directory
- that directory must contain `entrypoint.py`

Useful variants:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle \
  --shared-only
```

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle \
  --bundle-only
```

Use `--shared-only` when you want fast contract feedback.
Use `--bundle-only` when you are iterating on bundle-specific tests.

## 5. What Bundle-Local Tests Should Cover

Bundle-local tests should cover the parts that the platform cannot know.

Typical targets:

- local storage path resolution
- content normalization
- markdown/html transforms
- API payload shaping
- admin operation behavior
- index/document generation
- workflow helper logic
- storage round-trips
- bundle-local report export logic

Use the `versatile` tests as the model:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tests/test_preferences_canvas.py`

That test file is useful because it checks:

- bundle-local storage-backed behavior
- import/load paths
- data transforms
- export/import round-trips

That is the right style.
Test the bundle’s concrete behavior, not just that functions exist.

## 6. Widget Testing Rules

Widget testing has two layers:

1. static/widget-generation correctness
2. iframe integration correctness

### 5.1 Widget generation correctness

If the widget HTML is generated from Python:

- execute the builder function directly
- do not stop at `py_compile`

This catches issues like:

- unescaped braces in Python f-strings
- malformed embedded CSS
- malformed embedded JS template literals

Example failure mode:

- CSS like `@page{size:auto}` inside a Python f-string can raise `NameError`
- JS template literals with `${...}` can also break if braces are not escaped

Rule:

- if Python is generating HTML/JS/CSS, directly evaluate the builder function before runtime testing

### 5.2 Widget iframe contract

A widget is not tested until you verify the iframe host contract.

Check:

- widget requests config from parent
- widget accepts both `CONN_RESPONSE` and `CONFIG_RESPONSE`
- widget builds operation URLs from runtime config
- widget uses `defaultAppBundleId`, not a source-folder guess
- widget uses host-provided auth headers
- widget unwraps the `[alias]` field from integrations responses

Manual test:

- open the widget
- inspect browser networking
- confirm operation path is:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{alias}
```

If you see:

- missing tenant/project/bundle id
- `////operations/...`
- source-folder name instead of runtime bundle id

the widget is not integrated correctly.

### 5.3 Read-only load check

A bundle widget should usually be read-only on initial load.

Verify:

- simply opening the widget does not trigger unwanted mutation
- sync, push, rebuild, or run-now paths happen only on explicit user action

This is especially important for:

- archive sync widgets
- admin widgets
- pipeline dashboards

## 7. API Testing Rules

For every decorated API surface, verify both discovery and invocation.

Check:

- the bundle appears in the integrations listing
- the expected `apis` and `widgets` entries are present
- the operation is callable through the correct alias

Important:

- if a bundle import fails, the bundle may appear partially but with missing APIs/widgets
- if manifest discovery looks wrong, check import errors first

For POST operations, widget clients should send:

```json
{ "data": { ... } }
```

The integrations layer also accepts raw JSON objects, but widgets should use the platform wrapper.

## 8. Access-Control Testing

You must test visibility and invocation for the declared access model.

Current user-type ordering:

- `anonymous < registered < paid < privileged`

Test that:

- `registered` widgets/apis are visible to `registered`, `paid`, `privileged`
- `paid` widgets/apis are visible to `paid`, `privileged`
- `privileged` widgets/apis are visible only to `privileged`

If `roles=(...)` is also declared:

- test both with and without the role

For admin widgets/apis:

- verify no-permission behavior is explicit and clean
- verify the privileged path works through the real UI/API

## 9. Storage Testing

Bundles usually touch more than one storage tier.
Test the correct one.

### Local bundle storage

If the bundle keeps mutable local state, verify it is created under the platform-managed bundle storage root, not under the source tree.

Check:

- local workspaces
- cloned repos
- generated caches
- archive mirrors

Expected rule:

- instance-local mutable state belongs under bundle local storage

### Descriptor-backed props

If the bundle updates deployment-scoped config, verify:

- the write goes through bundle props
- the change lands in the expected descriptor-backed path
- reload respects it

### Artifact storage

If the bundle uses `AIBundleStorage`, verify:

- the correct artifact path is written
- local working state is not incorrectly stored there

## 10. Runtime Reload And Local Development Loop

If you are testing a locally mounted bundle, verify the actual reload path.

Typical loop:

```bash
kdcube --descriptors-location <dir> --build
```

Then after code/descriptor changes:

```bash
kdcube --workdir <runtime-workdir> --bundle-reload <bundle_id>
```

This is important because a bundle may pass tests but still fail during descriptor-driven runtime resolution.

Use reload testing after changing:

- bundle code
- `bundles.yaml`
- `bundles.secrets.yaml`

## 11. Standalone Helper Testing

If the bundle ships a standalone helper script, test it separately from runtime.

Typical rule:

- standalone mode is for local debug only
- operational runtime must still work through KDCube wiring

For standalone tests:

- load local `.env` into the platform settings path
- read config through `get_settings()` / `get_secret()`
- do not validate the bundle only through raw shell env behavior

You should test both:

- standalone path
- real bundle runtime path

If one works and the other does not, the feature is not done.

## 12. Failure Modes You Should Actively Probe

These are common enough that they should be tested deliberately.

### A. Descriptor identity mismatch

Test that runtime identity comes from descriptors/runtime context, not from the source folder name.

Symptoms:

- wrong storage root
- wrong workspace branch
- wrong session or conversation ID

### B. Missing widget config handshake

Symptoms:

- widget never loads data
- widget calls malformed routes
- widget has empty tenant/project/bundle id

### C. Import/manifest discovery failure

Symptoms:

- bundle listed without APIs/widgets
- 404 on expected operation alias

Cause is often:

- import error in entrypoint or imported modules

### D. Python f-string HTML builder failure

Symptoms:

- widget load crashes before response
- `NameError` from CSS or JS fragments inside Python string builder

Test by direct function execution.

### E. Read-only load unexpectedly mutates state

Symptoms:

- widget open triggers commits, pushes, syncs, or background jobs

### F. Wrong storage tier

Symptoms:

- local operational state written next to the source tree
- artifacts mixed with live workspace state

### G. Runtime config read from raw env instead of platform surfaces

Symptoms:

- feature works only in one shell session
- runtime ignores descriptors or bundle props

### H. Cron path assumes request-bound context

Symptoms:

- scheduled job crashes or behaves differently than the same logic behind a widget/API call

### I. Isolated path depends on host globals

Symptoms:

- helper works in one in-process test but fails when executed in isolated runtime

### J. Singleton reuse leaks request state

Symptoms:

- one request sees stale actor/session/request data from another request

### K. Singleton is mistaken for exclusivity

Symptoms:

- bundle is configured singleton but overlapping runs still happen
- state corruption still occurs under concurrency

## 13. Suggested Test Checklist Before Marking A Bundle Done

Before calling the bundle complete, verify all of these:

- `py_compile` passes for entrypoint and helper modules
- HTML/JS builder functions execute if the bundle generates widget HTML in Python
- shared SDK bundle suite passes
- bundle-local pytest tests pass
- bundle reload works through the local descriptor-driven flow
- bundle appears with correct APIs/widgets in integrations listing
- expected operations are callable through real routes
- widget networking uses the correct runtime URL shape
- widget respects auth/config handshake
- runtime identity matches descriptor identity
- local mutable state goes into bundle local storage
- descriptor-backed settings survive reload
- admin surfaces are permission-gated correctly
- singleton reuse does not leak request-bound state
- exclusivity behavior is validated separately from singleton behavior
- cron/system path does not assume request-bound comm context
- isolated-exec path does not assume host-process globals

## 14. Minimum Acceptance Standard

A bundle is not accepted just because:

- one method returns a value
- one widget renders once
- one manual operation succeeded

A bundle is accepted when:

- it passes shared and bundle-local tests
- it reloads correctly
- it follows the real platform widget/API contract
- it stores state in the correct tier
- it behaves correctly under the declared access model
- it survives the common failure modes listed above
