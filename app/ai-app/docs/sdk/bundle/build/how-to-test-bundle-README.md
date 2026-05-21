---
id: ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
title: "How To Test A Bundle"
summary: "Testing guide for bundle authors and QA: local syntax/suite/pytest validation, runtime reload validation, widget and API checks, scheduled-job verification, and failure diagnosis in the local runtime."
tags: ["sdk", "bundle", "testing", "pytest", "widget", "runtime", "validation"]
keywords: ["bundle testing workflow", "shared bundle suite", "local bundle tests", "widget and api validation", "shared sdk widget source validation", "runtime reload verification", "scheduled job checks", "bundle failure diagnosis", "manual and automated test loop", "local qa for bundles", "integration qa for bundles"]
updated_at: 2026-05-21
see_also:
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md
  - ks:docs/sdk/bundle/build/how-to-release-bundle-content-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
  - ks:docs/sdk/integrations/telegram/telegram-README.md
  - ks:docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - ks:docs/sdk/integrations/browser/browser-tools-README.md
  - ks:docs/service/cicd/ngrok-README.md
  - ks:docs/sdk/bundle/bundle-delivery-and-update-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
---
# How To Test A KDCube Bundle

This document is the operational test playbook for bundle builders.

If you are still deciding what to read first, start with
[how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md).

Tier 1 rule:

- this page is one part of the Tier 1 pack
- do not treat it as sufficient on its own
- read it together with the Tier 1 authoring, configuration, and configure/run pages

The goal is not “run something once”.
The goal is to prove that the bundle works in the supported KDCube runtime contract.

Critical Python import rule:

- bundle-local code must use package-relative imports such as
  `from .services.storage import ...`
- do not import bundle-local folders as top-level packages such as `services`,
  `apps`, `tools`, or `resources`
- see [bundle-runtime-README.md#critical-bundle-local-import-rule](../bundle-runtime-README.md#critical-bundle-local-import-rule)

Critical widget/browser test:

- verify widget network requests call the KDCube hosted origin, not the
  embedding host page origin
- in browser devtools, a control-plane iframe opened from
  `https://kdcube.example.com/platform/chat` should make widget/API calls to
  `https://kdcube.example.com/api/...`
- treat calls to `localhost`, `window.top`'s origin, or a host app domain as a
  widget integration bug unless the deployment intentionally reverse-proxies all
  KDCube paths under that same origin

Use this together with:

- [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md)
- [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
- [how-to-assemble-bundle-with-sdk-building-blocks-README.md](how-to-assemble-bundle-with-sdk-building-blocks-README.md)
- [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
- [how-to-release-bundle-content-README.md](how-to-release-bundle-content-README.md)
- [versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md)
- [bundle-widget-integration-README.md](../bundle-widget-integration-README.md)
- [bundle-runtime-README.md](../bundle-runtime-README.md)
- [bundle-delivery-and-update-README.md](../bundle-delivery-and-update-README.md)

Tier 1 role of this page:

- use it after you have bundle code and a real runtime
- use it to validate wrappers around existing user code, not only greenfield bundles
- use it to prove the full KDCube contract, not only unit-level correctness
- use it as the primary page for both local QA and integration QA

## 0. Which QA Job Are You Doing

There are two different QA jobs and this page covers both.

### Local QA

This means:

- syntax and import checks
- shared bundle suite
- bundle-local pytest
- direct checks of helpers, serializers, cron bodies, and builders

### Integration QA

This means:

- bundle discovery in a real KDCube runtime
- reload behavior after descriptor or code changes
- browser/widget validation
- API and MCP validation
- cron/runtime-path validation inside the real environment
- provider callback validation through public HTTPS when the provider cannot
  call localhost directly, for example Telegram webhooks, OAuth callbacks, or
  remote-control style callbacks

Use the same document for both, but do not confuse them.
Passing local tests is necessary and still not enough.

## 1. Testing Order

All commands in this page assume the working environment in
[1A. Working Environment For Agents](#1a-working-environment-for-agents).

Run tests in this order:

1. syntax/import checks
2. shared SDK bundle suite
3. bundle-local pytest tests
4. isolated direct checks for generated HTML/builders if applicable
5. local runtime reload path
6. real widget/API/manual runtime checks

After validation passes, use
[how-to-release-bundle-content-README.md](how-to-release-bundle-content-README.md)
only if the user agrees to cut a content release, tag it, push it, or update a
git-backed descriptor ref.

Do not jump straight to browser/manual testing.
That is the slowest feedback loop and the weakest signal.

Runtime-shape rule:

- if the runtime itself may be misconfigured, fix `assembly.yaml`, `bundles.yaml`, and `bundles.secrets.yaml` first
- use [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md) for the exact local runtime contract before debugging widget/API behavior
- use [how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md](how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md)
  when an agent must configure the runtime, patch bundle props/secrets, start
  ngrok, and prepare Telegram or Gmail values before live QA
- if an external provider must call the local runtime, use
  [Serving Local KDCube With Ngrok](../../../service/cicd/ngrok-README.md)
  before interpreting webhook, OAuth callback, or remote-callback failures

## 1A. Working Environment For Agents

Before touching bundle code, prove the test environment.

Use this baseline unless the task gives a different active runtime:

```bash
cd /abs/path/to/kdcube-ai-app
PY=app/venvs/ai-app/chat-processor/bin/python
```

If that interpreter does not exist, use the project venv for the runtime you are
testing. Do not use bare `python3` or bare `pytest` until you have proven they
point to the same environment.

Readiness checks:

```bash
$PY -c "import sys; print(sys.executable)"
$PY -m pytest --version
$PY -m pip show pytest-asyncio
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
  $PY -m pytest -q \
  app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/tests/test_singleton_request_context.py::test_singleton_entrypoint_keeps_comm_context_task_local
```

Interpretation:

- `pytest-asyncio` is required for tests that use `pytest.mark.asyncio`
- if it is missing, fix the venv/test dependencies before interpreting failures
- `PYTHONPATH=app/ai-app/src/kdcube-ai-app` is required unless the package is installed in the active venv
- shared bundle-suite tests require `--bundle-path` or `BUNDLE_UNDER_TEST`
- route/integration tests should create request objects with an ASGI `app` and `app.state.redis_async`, because production route code reads runtime state from `request.app.state`

First smoke commands:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
  $PY -m pytest -q \
  app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/tests/test_bundle_interfaces.py
```

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
  $PY -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle
```

## 1B. Skeleton-Stage Checks

For a brand-new bundle skeleton, prove the contract before adding product logic:

- the bundle has the skeleton files from
  [how-to-write-bundle-README.md#1b1-new-bundle-skeleton-checklist](how-to-write-bundle-README.md#1b1-new-bundle-skeleton-checklist)
- `entrypoint.py` parses and loads through the KDCube loader or shared bundle
  suite in the project venv
- `config/bundles.template.yaml` makes clear whether `path:` is a seed/source
  descriptor host path or a staged runtime/container path
- seed/source descriptors used by local CLI setup or IntelliJ/proc host runs
  use host-visible bundle paths
- deployment-scoped secrets are documented in `config/bundles.secrets.template.yaml`
- user-owned secrets, such as a user's personal email credentials, are not in
  descriptor templates
- `docs/design/` and `docs/journal/journal.md` exist and are updated with the
  first implementation decisions

Do this before building UI, tools, or scheduler logic. A clean skeleton makes
later failures narrower.

Do not validate bundle-local imports only with
`sys.path.insert(bundle_root); import entrypoint`. That direct top-level import
can mask the KDCube loader contract and can also create the same process-global
package collisions the runtime rule prevents. Use the shared bundle suite or a
real KDCube loader/runtime check for import validation.

## 1B.1 Reusable SDK Block Checks

When the bundle uses SDK integrations or solutions, test the bundle binding
instead of duplicating the SDK package tests.

Check that:

- `entrypoint.py` configures the SDK module with the intended storage root,
  target user resolver, bundle id, role policy, or widget modules;
- operations/public route tests prove the right auth boundary, for example
  KDCube auth, Telegram webhook secret, or Telegram Mini App `initData`;
- user-owned credentials use user-scoped secrets/state;
- deployment-owned provider settings use bundle props/secrets;
- design docs name the SDK blocks being used and describe what policy remains
  in the bundle;
- package-level SDK tests cover the reusable mechanics.

## 1C. React Tool/Skill Checks

Use [bundle-agent-integration-README.md](../bundle-agent-integration-README.md)
as the full contract for React descriptors, file-producing tools, MCP
connector/server wiring, and Claude Code subagent requirements.

For a React-backed bundle, prove the agent surface before manual testing:

- `entrypoint.py` instantiates the workflow in the same shape as
  `kdcube.copilot@2026-04-03-19-05`
- `orchestrator/workflow.py` calls `BaseWorkflow.build_react(...)`
- `tools_descriptor.py` exposes only the tool aliases the bundle actually needs
- `skills_descriptor.py` points to bundle-local skills
- `skills_descriptor.py` / `job_skills_descriptor.py` visibility filters use
  the real React decision ids `solver.react.v2.decision.v2.strong` and
  `solver.react.v2.decision.v2.regular`
- each skill has `SKILL.md`
- each skill `tools.yaml` references real tool ids from `tools_descriptor.py`
- distinct product concepts have distinct tool aliases
- stateful skill `when_to_use` rules clearly separate retrieval from
  write/reconcile scenarios
- tool descriptions tell the agent when to search, comment/update, create, or
  leave the state untouched
- public webhooks or operations APIs stay thin and do not duplicate React tool
  business logic
- bundle code defines normal model routing under `role_models`, and descriptor
  overrides can replace those roles without editing source
- temporary model-strength choices from APIs, MCP operations, cron, chat turns,
  or jobs bind `bundle_call_context.role_models` around the downstream agent
  call instead of mutating durable bundle props

Good React smoke tests:

- descriptor aliases match expected domain names, for example `tasks` and
  `user_memory`
- model-routing tests prove:
  - code defaults expose at least the roles the bundle uses
  - `bundles.yaml -> config.role_models` overrides a default role
  - `bundle_call_context.role_models` overrides only the current invocation and
    then falls back to the configured role
  - React role ids use the real runtime names
    `solver.react.v2.decision.v2.regular` and
    `solver.react.v2.decision.v2.strong`
- bundle-local storage tests prove each domain writes to its own storage area
- durable asset tests prove each task/memory is stored as one source-of-truth
  file and any SQLite index is rebuildable
- memory tests prove user-visible policy metadata is present and widget-open
  read receipts mark only returned visible entries/comments as seen
- task tests prove search can find existing tasks before edit/delete/link
  operations and task relations/conversation ids are persisted
- a webhook test proves the public endpoint is exposed and authenticated
- transport tests prove Telegram or other adapters render/send from the turn
  result or timeline rather than duplicating task/memory logic
- public webhook tests cover duplicate delivery of the same provider event id,
  for example Telegram `update_id`, and prove the bundle acknowledges the
  duplicate without running the React turn again
- attachment transport tests cover both captioned and attachment-only messages;
  provider file ids must be hydrated into normal bundle attachments before the
  React turn starts
- a runtime test or manual check proves the webhook triggers the React turn when
  the bundle is loaded in proc
- user-scope tests prove that state and user secrets are keyed by the resolved
  bundle user scope, not by an assumed KDCube account id
- if the bundle has public/external users, test both the KDCube-authenticated
  path and the public integration path, for example Telegram `initData`
  verification plus mapping/fallback to a stable external user scope
- tool-signature tests verify the model is not asked to invent runtime ids such
  as user id, task id, execution id, conversation id, internal account id, or
  storage paths; those must come from runtime context, job payload, or opaque
  references returned by earlier tools
- tool-description tests or direct signature checks verify model-facing return
  annotations include the timeline-visible `ret` shape, not only the envelope.
  For example, assert the annotation contains fields such as
  `ret={accounts:[...]}` or `ret={messages:[...],claude_code_mcp?...}` when the
  solver must use those fields in later steps.
- file-producing tool tests verify the strict result envelope:
  `{"ok": true, "ret": {"artifact_type": "files", "files": [...]}}`
- if a trusted bundle/catalog tool uses `host_files(...)`, tests or manual
  runtime checks verify hosted file rows include hosted metadata such as
  `hosted_uri`, `rn`, or `key`
- `host_files(...)` checks should run in a prepared tool context with
  `ToolSubsystem.hosting_service`, tenant, project, user id, conversation id,
  turn id, conversation storage, and output directory. The normal path is
  `BaseWorkflow.build_react(...)`; the isolated path is `bootstrap_bind_all(...)`.
- if generated executor code needs files, tests cover the scenario where it
  calls a catalog tool through `agent_io_tools.tool_call(...)` rather than
  trying to host files directly
- if the tool may run in isolated runtime, include an isolated-runtime check
  that proves the reconstructed tool subsystem can still host or return files

Do not add a gate-agent test unless the bundle intentionally has a gate.
For simple React bundles, a deterministic prepare step plus solver is enough.

## 1D. Runtime Log And Timeline Checks

When a manual SSE, webhook, widget, cron, or `@on_job` test fails, inspect
runtime logs and the turn timeline before changing bundle code. The final bot
answer is evidence, but it can be misleading if a tool returned the wrong
diagnostic.

Find the active log directory from the runtime descriptor or assembly. For local
development it is usually under the dev workspace:

```bash
LOG_DIR="${KDCUBE_DEV_WORKSPACE:-$HOME/.kdcube/dev-workspace}/log"
rg -n "<bundle-id>|<tool-id>|bundle.on_load|allowed_plugins|tool_ids|tool_runtime_not_bound|on_job" "$LOG_DIR"
```

Replace `<bundle-id>` and `<tool-id>` with the failing bundle and tool aliases.

Prove the sequence:

- proc actually restarted after platform or descriptor changes
- the bundle resolver found the expected path, git ref, and module
- `on_bundle_load` ran successfully for the bundle
- React was built with the expected `allowed_plugins` and `tool_ids`
- skill loading exposed the expected bundle-local skills
- skill visibility was filtered with the actual decision agent id shown in logs
  and accounting, not a legacy descriptor key
- the requested tool actually executed, not only appeared in the catalog
- the tool result block contains the `ret` fields promised by the model-facing
  tool return annotation
- the resolved bundle user scope and user type match the path being tested
  (KDCube-authenticated, Telegram/public, cron, or `@on_job`)
- `bundle_call_context` or job payload contains expected runtime ids for job
  tools instead of those ids being supplied by the model
- the turn timeline contains the events needed by the transport adapter

Classify failures by boundary:

- bundle absent, operation absent, or widget absent: descriptor, import, or
  decorator discovery failure
- tool missing from `tool_ids`: `tools_descriptor.py`, alias, skill visibility,
  or React configuration failure
- tool present but `tool_runtime_not_bound` or "tools are not bound": platform
  or bundle runtime binding failure, not a user OAuth/configuration failure
- explicit account/config errors such as "account not connected" or "account not
  found": user-actionable setup failure
- webhook returns only an acknowledgement: check the timeline and outbound
  transport rendering path, because webhook JSON is usually not the bot reply
- same old user request repeats after a webhook retry: check event-id
  idempotency before checking React behavior
- attachment message gets no answer: check whether attachment-only messages are
  dropped before React and whether provider file ids are converted to readable
  bundle attachments

Multiworker rule:

- check logs across all proc workers
- a bundle may load in one process while the failing request is served by another
- after platform code changes, restart proc; a new conversation is not enough

Close the loop:

- keep small, targeted runtime logs for surfaces that are hard to classify, such
  as `allowed_plugins`, `tool_ids`, resolved bundle path, and job metadata
- add a regression test at the boundary that failed
- do not replace an internal runtime error with user setup advice

## 1.1 What This Test Guide Must Prove

Testing is not only about “does one function work”.

For a bundle, the test set should prove all of these:

- the bundle loads and its manifest is discoverable
- the expected surfaces appear:
  - APIs
  - widgets
  - MCP endpoints
  - scheduled jobs
- each runtime path behaves correctly:
  - request-bound entrypoint path
  - operations/public HTTP path
  - cron/system path
  - isolated path when relevant
- config and secrets are read from the supported runtime contract
- mutable state goes to the correct storage tier
- reload/reconcile behavior works after descriptor changes

## 2. Baseline Test Matrix

Every non-trivial bundle should be tested at these layers.

### 2.1 Quick commands by feature

Use these as the first actionable checks for each bundle surface.

### Syntax and imports

```bash
$PY -m py_compile /abs/path/to/bundle/entrypoint.py
```

`py_compile` is only a syntax check. It does not prove the KDCube bundle-loader
contract.

Use the shared bundle suite for import validation. The suite loads the bundle
through the loader contract and lints bundle-local Python imports. Top-level
imports of Python roots owned by the bundle directory, such as
`from services...`, `from apps...`, or `import tools`, fail as authoring
errors. Use package-relative imports instead.

### Shared bundle contract

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
$PY -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle
```

When working specifically on bundle import isolation, this narrower command
runs only the import-contract slice:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
$PY -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle \
  --shared-only \
  -q -k import_contract
```

This is not a replacement for the full shared suite before release.

### Bundle-local tests

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
$PY -m pytest -q /abs/path/to/bundle/tests
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

### Public API with bundle-owned auth

```bash
curl -X POST \
  "http://localhost:5173/api/integrations/bundles/<tenant>/<project>/<bundle_id>/public/telegram_webhook" \
  -H "X-Telegram-Bot-Api-Secret-Token: <shared-token>" \
  -H "Content-Type: application/json" \
  -d '{"update_id":1}'
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
@cron(alias="sync", expr_config="task_tracker.sync", span="system")
async def sync(self, **kwargs):
    await self._sync_impl()
```

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app $PY -m pytest -q /abs/path/to/bundle/tests -k sync
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
$PY -m py_compile /abs/path/to/bundle/entrypoint.py
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
$PY -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle
```

This suite validates the shared bundle contract.

Use it for every bundle, not only for reference bundles.

### C. Bundle-local pytest tests

If the bundle has `tests/`, run them too.

Command:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
$PY -m pytest -q /abs/path/to/bundle/tests
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

If the bundle shells out to git or other subprocesses that need credentials, also verify the process-boundary behavior:

- the subprocess receives the expected per-call env
- the processor process env is not being rewritten by the bundle
- inherited processor `GIT_*` variables, if present, are understood to be shared across apps by design

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
- if the bundle has background jobs, there is exactly one decorated `@on_job`
  method and it calls `await super().handle_job(**kwargs)` before local
  `work_kind` dispatch

If a bundle has `@cron(...)`, you should test at least one run path that invokes the scheduled logic directly.

### D. Isolated runtime path

If the bundle or its tools may execute in isolated runtime, test that path explicitly.

What to validate:

- code does not depend on arbitrary host-process globals

## 4. Git-Backed Bundle Checks

If the bundle manages git workspaces, session stores, or external repos, test the git auth boundary explicitly.

Validate all of these:

- git remote normalization works as expected for your configured transport
- descriptor-backed token or SSH settings are visible to the git subprocess
- explicit call-site overrides affect only that subprocess invocation
- the bundle does not mutate processor `os.environ` in order to make git work

Minimal check pattern:

```python
env = build_git_env(
    git_http_token=get_secret("services.git.http_token"),
    git_http_user=get_secret("services.git.http_user"),
)
subprocess.run(["git", "config", "--get", "remote.origin.url"], env=env, check=True)
```

What the result should mean:

- inherited processor env is shared by design
- the bundle’s explicit subprocess env is local to that git command
- one bundle’s git override must not become another bundle’s process-global mutation
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
$PY -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle \
  --shared-only
```

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
$PY -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
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

1. source/build or static/widget-generation correctness
2. runtime display integration correctness

### 5.1 Widget source/build correctness

For new React widgets, prefer a source folder declared in bundle config:

```yaml
ui:
  widgets:
    task_memo_webapp:
      enabled: true
      src_folder: ui/widgets/task_memo_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

Local source checks:

```bash
cd /abs/path/to/bundle/ui/widgets/task_memo_webapp
npx tsc --noEmit
```

Runtime checks:

- open `/api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{alias}`
- open `/api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{alias}/{subpath}`
- confirm the source folder is built into shared bundle storage and subpaths
  fall back to the built `index.html`
- edit widget source and verify the bundle UI loader refreshes the built files
  from source signature changes

If the widget commits a lockfile, prefer `npm ci` in the build command. If it
does not, use `npm install --no-package-lock` so loader builds do not create
source-tree churn.

### 5.1A Legacy widget generation correctness

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

- if Python renders a React/TSX widget into HTML, directly evaluate the widget method before runtime testing

### 5.2 Widget UI runtime contract

A widget is not tested until you verify the runtime display contract.

Check:

- widget requests config from parent
- widget accepts both `CONN_RESPONSE` and `CONFIG_RESPONSE`
- widget builds operation URLs from runtime config
- widget uses `defaultAppBundleId`, not a source-folder guess
- widget uses host-provided auth headers
- widget unwraps the `[alias]` field from integrations responses
- if the widget is one web app with internal routes, direct widget subpaths
  return HTML and pass `widget_path` / `path` to the widget method

Manual test:

- open the widget
- inspect browser networking
- confirm operation path is:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{alias}
```

For a single-widget web app, also open:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{widget_alias}/{subpath}
```

It should return the same React widget shell with the requested panel/route
selected.

For a source-folder widget intended for a Telegram Mini App, also open:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{widget_alias}/{subpath}
```

That should load the same static shell without platform login. Then verify the
widget's public data/action calls separately with the bundle's own auth
mechanism, such as signed Telegram `initData`.

If you see:

- missing tenant/project/bundle id
- `////operations/...`
- source-folder name instead of runtime bundle id

the widget is not integrated correctly.

### 5.2A Browser-tool verification

When an agent needs to prove generated HTML or widget behavior in a real browser,
use the ReAct `browser_tools` namespace rather than guessing from static code.

Use it for:

- generated standalone HTML apps
- widget navigation and button/click behavior
- form filling and operation-path smoke checks
- below-the-fold content and scroll-dependent UI state
- screenshot-backed visual checks when DOM/text status is not enough

Do not overuse screenshots. They are useful for visual state, layout, canvas, or
image checks, but they add multimodal payload cost. Prefer DOM/text status after
ordinary clicks, fills, scrolls, and status checks when that is enough.

Important runtime boundary:

- `browser_tools` runs in the ReAct tool runtime and keeps a per-turn browser
  session
- isolated exec code may use Playwright independently if the runtime image
  supports it, but it does not share the ReAct `browser_tools` session
- turn completion, managed errors, watchdog timeout, and cancellation attempt
  browser-session cleanup through lifecycle finalizers
- cleanup logs should make it clear when browser sessions existed and were
  released, so operators can distinguish "nothing to clean" from "cleanup ran"

Primary docs:

- [../../integrations/browser/browser-tools-README.md](../../integrations/browser/browser-tools-README.md)
- [../../integrations/browser/playwright-README.md](../../integrations/browser/playwright-README.md)

For generated HTML repair, prefer a precise ReAct loop:

1. use `react.rg` to find files or suspicious text regions already materialized
   on the worker
2. use `react.read` ranges to inspect exact snippets; timeline previews may show
   line numbers for reading
3. use `react.patch` against current-turn editable text files; never include
   rendered line-number prefixes in patch or replacement content
4. use `browser_tools.open_page`, `click`, `fill`, `scroll`, and `status` to
   prove the behavior, adding screenshots only when visual state matters

### 5.2B Source-folder widget build contract

For React/Vite widgets declared under `ui.widgets.<alias>`, test the
loader build contract as well as the browser behavior.

Descriptor shape:

```yaml
build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

Widget source requirements:

- `package.json` script should be `vite build` or equivalent, without the
  loader output path as a positional argument
- Vite should use `build.outDir: process.env.OUTDIR || 'dist'`
- Vite should use relative assets such as `base: './'`
- if the widget imports SDK-owned UI through `shared_sources`, Vite aliases
  should prefer materialized `_shared/...` paths and only fall back to SDK-local
  monorepo paths for direct development builds
- run `npx tsc --noEmit` or the widget's equivalent typecheck before the Vite
  build when the widget imports shared SDK components

Local source check:

```bash
cd /abs/path/to/bundle/ui/widgets/<widget_alias>
OUTDIR=/tmp/kdcube-widget-build npm run build
test -f /tmp/kdcube-widget-build/index.html
```

For widgets that use shared SDK UI, also verify the descriptor contains the
expected source entries, for example:

```yaml
shared_sources:
  memory_widget:
    src_folder: sdk://context/memory/ui/widget/memories
    target: _shared/memory-widget
  telegram_widget:
    src_folder: sdk://integrations/telegram/ui/widget.telegram
    target: _shared/telegram-widget
```

Runtime log check:

- expected: `build command: npm install ...`
- expected: `build command: tsc -b && vite build` or `build command: vite build`
- bad: `vite build /.../.ui.build.tmp...`

If you see:

```text
[UNRESOLVED_ENTRY] Cannot resolve entry module .../.ui.build.tmp.../index.html
```

then the output directory leaked into Vite as a project/root argument. Fix the
widget `package.json`/Vite `outDir` contract or update to a platform build
runner that treats `<VI_BUILD_DEST_ABSOLUTE_PATH>` as an environment value.

If you see:

```text
Could not load /integrations/telegram/ui/widget.telegram/src/index.tsx
Could not load /context/memory/ui/widget/memories/src/embed.tsx
```

then the widget imported a shared SDK component, but the effective
`ui.widgets.<alias>.shared_sources` did not materialize that SDK source
under the `_shared/...` target used by the Vite alias. Fix bundle defaults or
descriptor props; do not patch the temporary build directory.

### 5.2C Custom main-view UI contract

For bundles with `ui.main_view` / `ui/main`, test the bundle main UI as a
runtime surface, not as a standalone website.

Local source checks:

```bash
cd /abs/path/to/bundle/ui/main
npx tsc --noEmit
```

Runtime checks:

- edit `ui/main`, not the built runtime storage directory
- do not run `OUTDIR=<bundle_storage_root>/ui npm run build` as the fix
- request the custom UI HTML through `/api/integrations/static/{tenant}/{project}/{bundle_id}`
- verify the bundle UI loader refreshes the built files when the source signature changed
- verify the bundle UI receives `baseUrl`, tenant, project, auth headers, `streamId`, and `defaultAppBundleId` from the runtime config bridge
- use `defaultAppBundleId` for `/sse/chat`, `/api`, `/mcp`, and widget calls
- for a new `/sse/chat` conversation, omit `conversation_id`; bind the server-generated id from the ack or first SSE envelope

Failure signals:

- `sse/chat failed (404) {"detail":"Conversation not found"}` usually means the UI sent a local fake `conversation_id` for a new conversation
- `Unknown bundle_id ...` usually means the bundle UI used a baked/source id or the runtime registry does not include the selected bundle
- a stale hashed JS asset after source changes means the loader/static route path must be checked, not manually bypassed

### 5.3 Read-only load check

A bundle widget should usually be read-only on initial load.

Verify:

- simply opening the widget does not trigger unwanted mutation
- explicit read-receipt widgets, such as a memory widget, mutate only the
  documented seen/acknowledged fields for returned visible items
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
kdcube init --descriptors-location <dir> --workdir <base-workdir>
kdcube start --workdir <runtime-workdir>
```

Then after code/descriptor changes:

```bash
kdcube reload <bundle_id> --workdir <runtime-workdir>
```

This is important because a bundle may pass tests but still fail during descriptor-driven runtime resolution.

Use reload testing after changing:

- bundle code
- `bundles.yaml`
- `bundles.secrets.yaml`

For generated custom main-view UI, also test the loader boundary:

- source lives in the bundle `ui/main`
- runtime serves built files from bundle storage
- the bundle UI loader owns freshness checks and builds
- concurrent proc workers or shared EFS storage should result in one build and other workers seeing the completed signature/cache hit
- manual runtime-storage builds are diagnostic only, not the supported workflow

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

### B2. Stale or wrong custom main-view UI

Symptoms:

- the browser still runs an old hashed asset after `ui/main` changed
- the bundle UI sends a baked bundle id instead of the selected runtime bundle id
- the bundle UI sends a local fake conversation id for a new SSE chat

Test by requesting the HTML entrypoint through the integrations static route and
checking browser networking for the actual `/sse/chat` payload.

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
- React/TSX widget render functions execute if the bundle renders widget HTML in Python
- shared SDK bundle suite passes
- bundle-local pytest tests pass
- bundle reload works through the local descriptor-driven flow
- bundle appears with correct APIs/widgets in integrations listing
- expected operations are callable through real routes
- memory-enabled bundles are tested with an explicit descriptor block:
  `memory.enabled: true`, `memory.widget.enabled: true`, and
  `ui.widgets.memories.enabled: true`
- widget networking uses the correct runtime URL shape
- custom main-view UI uses the runtime config bridge and selected runtime bundle id
- generated custom main-view UI is refreshed by the loader, not by manual runtime-storage builds
- generated HTML or widget click/form behavior is verified with browser tools
  when static checks are not enough
- external webhook/callback paths are verified through public HTTPS when the
  local runtime must be reached by Telegram, OAuth/Cognito, or another remote
  provider
- widget respects auth/config handshake
- runtime identity matches descriptor identity
- local mutable state goes into bundle local storage
- descriptor-backed settings survive reload
- admin surfaces are permission-gated correctly
- singleton reuse does not leak request-bound state
- exclusivity behavior is validated separately from singleton behavior
- cron/system path does not assume request-bound comm context
- isolated-exec path does not assume host-process globals

Memory smoke config:

```yaml
config:
  memory:
    enabled: true
    announce: {enabled: true, limit: 6, scope_filter: current_bundle}
    tools: {enabled: true, allow_write: false, default_scope_filter: current_bundle}
    widget: {enabled: true, allow_write: true, default_scope_filter: current_bundle}
    reconciliation: {enabled: true}
    snapshots: {enabled: true}
  ui:
    widgets:
      memories:
        enabled: true
```

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
