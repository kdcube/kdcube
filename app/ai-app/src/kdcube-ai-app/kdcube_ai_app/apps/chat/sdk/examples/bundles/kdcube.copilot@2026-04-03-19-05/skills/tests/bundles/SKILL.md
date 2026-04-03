---
name: bundles
id: bundles
description: |
  Guidance for using the current parameterized bundle pytest suite as contract evidence
  and validation material when generating, modifying, extracting, repairing, reviewing,
  or troubleshooting bundles.
version: 1.1.0
category: testing
tags:
  - bundles
  - pytest
  - validation
  - exec
  - kdcube-copilot
when_to_use:
  - The user asks to generate a new bundle
  - The user asks to modify an existing bundle and verify it still works
  - The user asks to review, repair, extract, or troubleshoot a bundle
  - The user asks how a bundle should be structured or imported
  - The agent needs the current bundle pytest suite and its expectations in front of it
  - The agent needs to locate or run the bundle tests exposed by kdcube.copilot
author: kdcube
created: 2026-03-20
namespace: tests
---

# Bundle Tests

## Purpose

This skill tells you where the current parameterized bundle pytest suite lives and how to use it from isolated exec.
Keep it loaded whenever bundle code is being authored or discussed in detail, not only when pytest is about to run.

Companion loading rule:
- For bundle tasks, load this skill together with `sk:product.kdcube`.
- `sk:product.kdcube` gives the platform/runtime model.
- `sk:tests.bundles` gives the current test contract and validation workflow.
- This skill alone is not the contract. Before writing code, read the actual current test files, not only this skill.
- Use the tests to understand the expected contract first, then write code to satisfy that contract.
- When platform or framework symbols are needed, confirm them from current docs/examples/source before coding.
- Do not invent platform symbols or import paths.
- Prefer the smallest implementation that can satisfy the currently confirmed contract; validate early, then expand.
- Bundle docs start point for authoring is `ks:docs/sdk/bundle/bundle-index-README.md`.
- Primary full reference bundle doc is `ks:docs/sdk/bundle/bundle-reference-versatile-README.md`.
- Normal reference bundle code root is `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`.
- For custom main-view UI work, also read `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui-src/src/App.tsx` and the custom UI section in the `versatile` README.
- For bundle authoring/modification, do not start with `react.write` or `react.patch` after reading only skills.
  First read the actual current tests that define the contract.
- If the bundle uses platform-integrated SDK/runtime/agent patterns, also read at least one current source/example/doc file that proves that pattern before writing code.

## Pre-write checklist

Before the first bundle file write, make sure all of the following are true:

- You have read the actual current pytest material that defines the relevant contract.
- You know the exact test file or small subset you plan to run, or you have a concrete discovery plan to find it.
- For every requested platform-integrated feature, you have read at least one current source/example/doc file that proves the needed pattern.
- The exact import paths and runtime symbols you intend to use are confirmed in visible evidence.
- You know the smallest bundle shape that should pass the relevant current tests.

If any item above is still missing, do not write bundle code yet. Gather the missing evidence first.

## Where the tests are

The current shared bundle pytest suite lives at this real knowledge-space path:
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/...`

Normal docs start point before reading individual tests:
- `ks:docs/sdk/bundle/bundle-index-README.md`
- then `ks:docs/sdk/bundle/bundle-reference-versatile-README.md`
- then `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`
- when the task includes a custom bundle UI, also read `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui-src/src/App.tsx`

Important distinction:
- `ks:` is one common prepared knowledge root.
- `ks:docs`, `ks:deployment`, and `ks:src/...` are just different paths under that one root.
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/...` is therefore a normal path inside the same common knowledge space, not a separate test-only namespace.

Current search behavior:
- `react.search_knowledge(...)` currently indexes docs metadata and deployment markdown.
- It does **not** currently index the `ks:src/...` source trees, including the shared pytest suite under `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`.
- This is a search-scope rule, not a namespace rule.

Practical implication:
- if you already know the exact test path, use `react.read(...)`
- if you do not yet know the exact test file, browse the test subtree from isolated exec
- do not assume one fixed single smoke-test file up front

Instead:
- keep `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle` as the logical base
- use generated exec code plus `bundle_data.resolve_namespace(...)` to browse the subtree
- inspect the exact pytest files that are relevant
- treat the resolved directory as a pytest suite root

This suite includes `conftest.py`, which defines:
- bundle selection by folder via `BUNDLE_UNDER_TEST` or `--bundle-path`
- bundle id derivation from the selected bundle folder for internal routing/config use
- shared Redis / Postgres / comm-context fixtures

There is also a default runner for full bundle validation:
- `python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite --bundle-path /abs/path/to/bundle`

That runner:
- always runs the shared SDK bundle suite under `sdk/tests/bundle`
- automatically adds `<bundle>/tests` when the bundle defines bundle-local tests
- keeps `BUNDLE_UNDER_TEST` set for the shared suite

Important nuance:
- bundle-local tests under `<bundle>/tests` are valid and encouraged for reference-bundle or package-specific behavior
- those tests are not magically injected into the shared suite by pytest itself
- the default runner is what makes the combined validation automatic
- bundle-local tests should be self-contained or define their own local `conftest.py` if they need local fixtures

## What the current suite covers

| Area | File(s) |
|---|---|
| Initialization | `test_initialization.py` |
| Configuration | `test_configuration.py` |
| LangGraph structure | `test_graph.py` |
| BundleState fields | `test_bundle_state.py` |
| Error handling | `test_error_handling.py` |
| Event streaming | `test_event_streaming.py` |
| Token accounting | `test_accounting.py` |
| Model routing | `test_model_routing.py` |
| Sequential requests / execution flow | `test_execution_flow.py` |
| Custom tools execution | `test_custom_tools_execution.py` |
| Custom tools integration | `test_custom_tools_integration.py` |
| Custom tools registration | `test_custom_tools_registration.py` |
| Custom tools storage behavior | `test_custom_tools_storage.py` |
| Custom skills execution | `test_custom_skills_execution.py` |
| Custom skills manifest/schema | `test_custom_skills_manifest.py` |
| Custom skills registration | `test_custom_skills_registration.py` |
| Custom skills visibility | `test_custom_skills_visibility.py` |
| Storage core behavior | `test_storage.py` |
| Cloud storage | `test_storage_cloud.py` |
| Local FS storage | `test_storage_local_fs.py` |
| Redis storage | `test_storage_redis.py` |
| Storage integration | `test_storage_integration.py` |

Interpretation rule:
- not every file in this suite is relevant to every bundle task
- choose the smallest relevant subset of tests for the user’s requested feature set
- when unsure, read the exact candidate tests first and let them tell you whether they apply

## How to use them

1. Read this skill if it is relevant:
   - `react.read(["sk:tests.bundles"])`
2. Read the bundle docs start point if you have not already:
   - `react.read(["ks:docs/sdk/bundle/bundle-index-README.md"])`
   - `react.read(["ks:docs/sdk/bundle/bundle-reference-versatile-README.md"])`
   - `react.read(["ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md"])`
   - when relevant, `react.read(["ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui-src/src/App.tsx"])`
3. Read the actual current test files before writing bundle code:
   - start from `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`
   - discover the relevant pytest files by browsing from generated exec code if the exact file is not already known
   - bring the exact discovered file(s) back into visible context with `react.read(...)`
4. In generated exec code, if discovery is needed:
   - resolve `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle` with `bundle_data.resolve_namespace(...)`
   - browse the returned `physical_path`
   - identify the relevant pytest files for bundle validation
   - emit exact logical refs or a short listing into `OUTPUT_DIR/...`
5. Only after the tests are actually read, write or patch the bundle code.
6. Run the narrowest exact file or subset that validates the current step.
7. Write the pytest results to `OUTPUT_DIR/...` so they come back to the agent clearly.

Implementation strategy:
- read the tests first to understand the minimum required shape
- if exact test paths are not obvious, do a narrow browse of the test subtree first and then read the exact discovered files
- for platform-integrated bundle code, read at least one current source/example/doc file that proves the needed SDK pattern before implementing it
- for normal bundle authoring in this repo, the default paired source example is the `versatile` bundle unless the question is specifically about `ks:` or a stripped-down isolated-exec example
- implement the smallest version that can satisfy that shape
- run the most relevant tests early
- only add non-essential structure after the current contract passes

## Default authoring loop

Use this as the normal workflow for bundle generation, repair, and modification:

1. Read the tests first.
2. Read the current source/examples/docs that prove the requested feature patterns.
3. If exact files are still unknown, use a small exec browse to discover candidate files, then `react.read` the exact discovered paths.
4. Write the smallest implementation that should satisfy both the tests and the explicit user request.
5. Run the relevant test subset immediately.
6. If it fails, read the exact traceback and the exact failing test/source/import targets before patching.
7. Repeat with small corrections until the relevant test subset passes.
8. Only then broaden validation or add optional polish.

Do not jump from skills directly to large speculative code generation.
Do not patch repeatedly from guesses when the traceback already tells you what exact file or import to inspect.

## Test-oriented exploration strategy

When preparing to write or repair a bundle, use this exploration strategy:

1. Read the tests first.
2. Extract any exact file paths, import paths, symbol names, or structural expectations from the tests.
3. If the tests imply a platform pattern that is still unclear, read the current source/example/doc files that prove that pattern.
4. If exact files are still unknown, use isolated exec to search the relevant subtree like you would locally:
   - resolve the subtree with `bundle_data.resolve_namespace(...)`
   - recursively list files
   - search for imports, base classes, descriptor patterns, decorators, or symbol names
   - use Python logic directly or `subprocess.run(...)` for local shell-style search when that is the clearest option
   - emit exact logical refs for the promising matches
5. `react.read(...)` the exact discovered files before writing code.

This means:
- the agent is allowed to do sophisticated repository searches
- but those searches must happen in isolated Python exec, not by assuming local shell access
- the result of the search should be exact files to read next, not speculative code generation

## Important protocol detail

`bundle_data.resolve_namespace(...)` returns an exec-only `physical_path`.
That path is valid only inside isolated exec.

If you need later follow-up with `react.read(...)`, keep the original logical base:
- logical base: `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`
- discovered relative path: whatever the exec-time browse step found
- follow-up logical ref: `f"{logical_base}/{relative_path}"`

## How to choose and run tests

This suite is a normal pytest tree with a shared `conftest.py`.
It runs against the selected bundle folder, not against a bundle id lookup.

Normal strategy:
- read the specific files that match the requested feature
- run the narrowest exact file or small subset that validates the current step
- only run the whole directory when broad regression coverage is actually useful
- for broad bundle validation, prefer the bundle suite runner so bundle-local tests are included automatically when present

Useful patterns:
- minimal bundle shape:
  - `test_initialization.py`
  - `test_configuration.py`
  - `test_graph.py`
- custom tools:
  - `test_custom_tools_*.py`
- custom skills:
  - `test_custom_skills_*.py`
- storage:
  - `test_storage*.py`
- broad regression sweep:
  - the whole directory

Example run shapes:
- default broad validation with automatic bundle-local test inclusion:
  - `PYTHONPATH=app/ai-app/src/kdcube-ai-app python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite --bundle-path /abs/path/to/bundle -v --tb=short`
- one exact file:
  - `BUNDLE_UNDER_TEST=/abs/path/to/bundle python -m pytest <resolved_test_root>/test_initialization.py -v --tb=short`
- a small subset:
  - `BUNDLE_UNDER_TEST=/abs/path/to/bundle python -m pytest <resolved_test_root>/test_initialization.py <resolved_test_root>/test_configuration.py <resolved_test_root>/test_graph.py -v --tb=short`
- the whole current suite:
  - `BUNDLE_UNDER_TEST=/abs/path/to/bundle python -m pytest <resolved_test_root> -v --tb=short`

If bundle-local tests exist and you are not using the runner, include them explicitly:
- `BUNDLE_UNDER_TEST=/abs/path/to/bundle python -m pytest <resolved_test_root> /abs/path/to/bundle/tests -v --tb=short`

Interpreting results:
- `N passed` means the executed subset passed
- `SKIPPED` can be normal when a bundle or optional feature is absent for the selected bundle id
- `FAILED` means there is a real problem to inspect
- do not claim validation passed unless pytest was actually run and returned success

## Draft execution pattern

In generated exec code, do roughly this:

1. define `logical_base = "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle"`
2. resolve it with `bundle_data.resolve_namespace(logical_base)`
3. treat `Path(res["ret"]["physical_path"])` as the suite root
4. if you need discovery instead of a known exact file, collect candidates:
   - `sorted(test_root.rglob("test_*.py"))`
5. choose the smallest relevant exact file or subset
6. run:
   - `python -m pytest <chosen_test_file_or_dir>`
7. write:
   - test summary
   - stdout/stderr
   into files under `OUTPUT_DIR`

Concrete execution sketch:

```python
from pathlib import Path
import os
import subprocess
import sys

logical_base = "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle"
res = await agent_io_tools.tool_call(
    fn=bundle_data.resolve_namespace,
    params={"logical_ref": logical_base},
    call_reason="Resolve bundle pytest suite",
    tool_id="bundle_data.resolve_namespace",
)

assert res.get("ok"), res
test_root = Path(res["ret"]["physical_path"])

candidates = sorted(test_root.rglob("test_*.py"))
assert candidates, f"No pytest files found under {test_root}"

# Choose the smallest relevant file once you know which capability you are validating.
test_file = next(
    (p for p in candidates if p.name == "test_initialization.py"),
    candidates[0],
)

env = dict(os.environ)
env["BUNDLE_UNDER_TEST"] = str(bundle_root)
proc = subprocess.run(
    [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        "-v",
        "--tb=short",
    ],
    text=True,
    capture_output=True,
    env=env,
    cwd=str(test_root),
)
```
