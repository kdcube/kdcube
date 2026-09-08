---
id: repo:kdcube-ai-app/app/ai-app/docs/procedures/platform-source-testing-README.md
title: "Run Platform Source Tests"
summary: "Contributor procedure for selecting the correct checkout, Python environment, extracted package sources, bundle fixture, and verification depth when testing KDCube platform code."
tags: ["procedures", "contributors", "testing", "pytest", "frontend", "pull-requests"]
keywords: ["platform source tests", "python interpreter", "PYTHONPATH", "app foundation source overlay", "connection hub source overlay", "bundle under test", "bundle path", "regression test", "pull request head"]
updated_at: 2026-09-03
see_also:
  - repo:kdcube-ai-app/AGENTS.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/operations/operate-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/cli-README.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/requirements-chat-processor.txt
---
# Run Platform Source Tests

Use this procedure when changing or reviewing KDCube platform code under
`app/ai-app/src/kdcube-ai-app`, platform UI code, deployment code, or public
platform documentation.

The test result is meaningful only when all four inputs are explicit:

1. the KDCube checkout or pull-request worktree being tested;
2. the Python interpreter and installed service dependencies;
3. any intentionally overlaid extracted package, such as App Foundation or
   Connection Hub;
4. the app fixture required by shared bundle tests.

## 1. Select the checkout and interpreter

Set paths from the checkout that contains the code under test:

```bash
export KDCUBE_REPO=/abs/path/to/kdcube
export KDCUBE_SRC="$KDCUBE_REPO/app/ai-app/src/kdcube-ai-app"
export PY=/abs/path/to/a/compatible/python
```

For a pull request, create a separate Git worktree and set `KDCUBE_REPO` to
that worktree. Do not point `PYTHONPATH` at the primary checkout while testing
files from a review worktree.

Use the prepared virtual environment for the service whose code is being
tested. The interpreter may live outside the review worktree, including in a
compatible prepared environment from the primary checkout. The imported
KDCube source must still resolve to `KDCUBE_SRC` in the review worktree.

For example, a standard source checkout may already provide:

```bash
export PY=/abs/path/to/kdcube/app/venvs/ai-app/chat-processor/bin/python
```

If a compatible environment does not exist, create one and install the
matching service requirement file. Chat processor, SDK, harness, and shared
bundle tests use `requirements-chat-processor.txt`, which includes the test
tools and declares the `app-foundation` and `connection-hub` dependencies:

```bash
python3.11 -m venv "$KDCUBE_REPO/.venv-platform-tests"
export PY="$KDCUBE_REPO/.venv-platform-tests/bin/python"
"$PY" -m pip install -r "$KDCUBE_SRC/requirements-chat-processor.txt"
```

When a declared package floor is still an unpublished coordinated candidate,
preinstall its local distribution metadata before resolving the requirement
file:

```bash
export APP_ECOSYSTEM_REPO=/abs/path/to/app-ecosystem
export APP_FOUNDATION_SOURCE="$APP_ECOSYSTEM_REPO/packages/app-foundation"

"$PY" -m pip install --no-deps "$APP_FOUNDATION_SOURCE"
"$PY" -m pip install -r "$KDCUBE_SRC/requirements-chat-processor.txt"
```

The requirement pass installs the candidate's declared extras and the rest of
the service environment. Section 2 then selects the exact checkout source for
test imports.

Use the corresponding `requirements-<service>.txt` for another service. Add
`pytest` and `pytest-asyncio` to a service environment when its requirement
file does not include test tools.

Prove the interpreter before interpreting a test result:

```bash
"$PY" -c 'import sys; print(sys.executable)'
"$PY" -m pytest --version
"$PY" -m pip show pytest-asyncio
```

Use `"$PY" -m pytest`, not an unqualified `pytest`. This keeps collection and
execution in the same environment.

## 2. Select installed or extracted package code

KDCube service requirement files declare released extracted dependencies. A
normal source test uses those installed distributions. The MCP client
implementation is owned by `app-foundation`; the historical KDCube import path
is a compatibility facade over that package.

When a change intentionally spans KDCube and unreleased App Foundation or
Connection Hub source, add each selected package's `src` directory to
`PYTHONPATH`:

```bash
export APP_ECOSYSTEM_REPO=/abs/path/to/app-ecosystem
export APP_FOUNDATION_SRC="$APP_ECOSYSTEM_REPO/packages/app-foundation/src"
export CONNECTION_HUB_SRC="$APP_ECOSYSTEM_REPO/products/connection-hub/packages/connection-hub/src"
export PYTHONPATH="$KDCUBE_SRC:$APP_FOUNDATION_SRC:$CONNECTION_HUB_SRC${PYTHONPATH:+:$PYTHONPATH}"
```

For tests that do not overlay extracted package source:

```bash
export PYTHONPATH="$KDCUBE_SRC${PYTHONPATH:+:$PYTHONPATH}"
```

The `PYTHONPATH` overlay selects coordinated repository source for this test
process. Runtime-image verification uses the maintainer package-source build in
§7, while released service installs resolve their declared package versions.
Record every overlaid repository commit when it affects the result.

Prove import origins before collection:

```bash
"$PY" - <<'PY'
from pathlib import Path

import app_foundation
import connection_hub
import kdcube_ai_app

print("kdcube_ai_app:", Path(kdcube_ai_app.__file__).resolve())
print("app_foundation:", Path(app_foundation.__file__).resolve())
print("connection_hub:", Path(connection_hub.__file__).resolve())
PY
```

The first path must be inside `KDCUBE_SRC`. Each extracted package path must be
either the installed distribution selected for the test or its explicit
source checkout. A missing import or an unexpected origin is a test-environment
failure. Correct the environment before assessing product behavior.

## 3. Run the exact regression first

Start with the smallest test that reproduces the changed behavior:

```bash
"$PY" -m pytest -q -rs \
  "$KDCUBE_SRC/kdcube_ai_app/path/to/tests/test_feature.py::test_exact_case"
```

A bug fix needs a negative assertion that fails on the pre-fix behavior. A
green neighboring suite does not replace that regression test.

Then run the complete test file and the nearest owning test directory:

```bash
"$PY" -m pytest -q -rs \
  "$KDCUBE_SRC/kdcube_ai_app/path/to/tests/test_feature.py"

"$PY" -m pytest -q -rs \
  "$KDCUBE_SRC/kdcube_ai_app/path/to/tests"
```

Run known-similar subsystem tests when the changed class or contract has more
than one construction site, process role, transport, or runtime owner.

## 4. Supply the app fixture to shared bundle tests

Tests under `kdcube_ai_app/apps/chat/sdk/tests/bundle` require an app folder.
Pass it with `--bundle-path` or `BUNDLE_UNDER_TEST`. A missing app folder is a
pytest invocation error, not a platform failure.

For a focused shared-suite test against the reference LangGraph app:

```bash
export REFERENCE_BUNDLE="$KDCUBE_SRC/kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13"

"$PY" -m pytest -q -rs \
  "$KDCUBE_SRC/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_event_streaming.py" \
  --bundle-path="$REFERENCE_BUNDLE"
```

For the supported shared-suite runner:

```bash
"$PY" -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path "$REFERENCE_BUNDLE" \
  -q -rs
```

Choose an app that declares the capability being exercised. Always retain
`-rs` in reported runs. A skip such as `Bundle has no event filter` describes
the selected fixture and is not a passing behavioral assertion.

## 5. Run the broader backend checks

After focused and neighboring tests pass, set the explicit app fixture for the
grouped package-tree runs below:

```bash
export BUNDLE_UNDER_TEST="$REFERENCE_BUNDLE"
```

The ReAct v2 and v3 tool tests currently contain matching module basenames.
The SDK's `examples/mcp` package also has the same top-level name as the
installed `mcp` distribution when pytest collects it below namespace-package
parents. Preload the installed SDK with `-p mcp`, and collect the example and
the MCP v2 conformance file in separate processes so each resolves the
intended `mcp` package. Together, the commands below still cover the complete
backend tree:

```bash
export REACT_V2_TOOLS="$KDCUBE_SRC/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/tools/tests"
export REACT_V3_TOOLS="$KDCUBE_SRC/kdcube_ai_app/apps/chat/sdk/solutions/react/v3/tools/tests"
export MCP_EXAMPLE_TESTS="$KDCUBE_SRC/kdcube_ai_app/apps/chat/sdk/examples/mcp"
export MCP_V2_CONFORMANCE="$KDCUBE_SRC/kdcube_ai_app/apps/chat/sdk/runtime/mcp/test_mcp_v2_conformance.py"

"$PY" -m pytest -q -rs -p mcp \
  "$KDCUBE_SRC/kdcube_ai_app" \
  --ignore="$REACT_V2_TOOLS" \
  --ignore="$REACT_V3_TOOLS" \
  --ignore="$MCP_EXAMPLE_TESTS" \
  --ignore="$MCP_V2_CONFORMANCE"

"$PY" -m pytest -q -rs \
  "$REACT_V2_TOOLS"

"$PY" -m pytest -q -rs \
  "$REACT_V3_TOOLS"

"$PY" -m pytest -q -rs \
  "$MCP_EXAMPLE_TESTS"

"$PY" -m pytest -q -rs \
  "$MCP_V2_CONFORMANCE"
```

Some integration tests use Redis, Postgres, Docker, browser, or provider
fixtures. Bring up the required local service before interpreting those
failures. Name unavailable integration layers and skipped tests in the final
verification report.

## 6. Run frontend checks for every changed UI

For the main chat frontend:

```bash
cd "$KDCUBE_REPO/app/ai-app/ui/chat-web-app"
npm ci
npm run lint
npm run build
```

For another widget or frontend package, run the scripts declared in that
package's `package.json`. Verify responsive and interaction changes in a
running browser after lint and build pass.

## 7. Verify the staged runtime when behavior crosses a service boundary

Unit and integration tests execute source from the checkout. A local KDCube
runtime executes its staged platform copy. Refresh from the intended source
checkout before live verification:

```bash
kdcube refresh \
  --tenant <tenant> \
  --project <project> \
  --path "$KDCUBE_REPO" \
  --build
```

When the runtime change also consumes unpublished extracted-package source,
stage every imported distribution in the same build:

```bash
export APP_ECOSYSTEM_REPO=/abs/path/to/app-ecosystem
export APP_FOUNDATION_SOURCE="$APP_ECOSYSTEM_REPO/packages/app-foundation"
export CONNECTION_HUB_SOURCE="$APP_ECOSYSTEM_REPO/products/connection-hub/packages/connection-hub"

kdcube refresh \
  --tenant <tenant> \
  --project <project> \
  --path "$KDCUBE_REPO" \
  --build \
  --maintainer-local-python-package "app-foundation=$APP_FOUNDATION_SOURCE" \
  --maintainer-local-python-package "connection-hub=$CONNECTION_HUB_SOURCE"
```

The image installs its ordinary requirements without the selected
distributions, keyed by the selection manifest, then installs the exact
selected sources on top. A local candidate satisfies a newly declared version
floor, and an edit in a candidate re-runs only the last pip layer. The complete command
contract and package-selection rule live in
[Current KDCube CLI](../service/cicd/cli-README.md#maintainer-local-python-package-sources).

Then verify the behavior through the real transport and inspect the service
logs. See [Operate A Runtime](../recipes/operations/operate-runtime-README.md)
for runtime evidence and restart semantics.

## 8. Recheck a moving pull request before publishing a review

Immediately before posting review findings:

1. fetch the pull-request ref;
2. compare its head object ID with the object ID tested;
3. re-read the diff and rerun affected checks when the ID changed;
4. state the tested object ID and exact test result.

Example:

```bash
git fetch origin pull/<number>/head:refs/remotes/origin/pr-<number>
git rev-parse refs/remotes/origin/pr-<number>
```

Do not describe a previous commit as the current head.

## 9. Report verification precisely

The completion report records:

- checkout or pull-request object ID;
- Python executable and relevant import origins;
- source overlays and their object IDs;
- exact test commands and pass, fail, error, and skip counts;
- bundle fixture used by shared tests;
- unavailable infrastructure or tests not run;
- live runtime source and maintainer-package selectors, plus transport checks,
  when applicable.

`git diff --check`, import success, collection success, unit tests, shared app
contract tests, and live runtime checks prove different things. Report each as
the evidence it actually provides.
