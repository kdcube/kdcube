---
name: bundles
id: bundles
description: |
  Draft guidance for validating generated bundles against a small reusable pytest smoke suite.
  Use this skill whenever the task is about bundle code generation, modification, extraction,
  repair, review, or validation and the agent needs the current bundle contract and smoke-test
  expectations in front of it.
version: 1.0.0
category: testing
tags:
  - bundles
  - pytest
  - validation
  - exec
  - react-doc
when_to_use:
  - The user asks to generate a new bundle
  - The user asks to modify an existing bundle and verify it still works
  - The user asks to review, repair, extract, or troubleshoot a bundle
  - The user asks how a bundle should be structured or imported
  - The agent needs a quick bundle smoke test before handoff
  - The agent needs to locate the reusable bundle test fixtures in react.doc
author: kdcube
created: 2026-03-20
namespace: tests
---

# Bundle Tests

## Purpose

This skill tells you where the draft reusable bundle smoke tests live and how to run them from isolated exec.
Keep it loaded whenever bundle code is being authored or discussed in detail, not only when pytest is about to run.

Companion loading rule:
- For bundle tasks, load this skill together with `sk:product.kdcube`.
- `sk:product.kdcube` gives the platform/runtime model.
- `sk:tests.bundles` gives the current bundle contract and validation expectations.
- This skill alone is not the contract. Before writing code, read the actual fixture material, not only this skill.
- Use the tests to understand the expected contract first, then write code to satisfy that contract.
- When platform or framework symbols are needed, confirm them from current docs/examples/source before coding.
- Do not invent platform symbols or import paths.
- Prefer the smallest implementation that can satisfy the currently confirmed contract; validate early, then expand.
- For bundle authoring/modification, do not start with `react.write` or `react.patch` after reading only skills.
  First read the actual current tests that define the contract.
- If the bundle uses platform-integrated SDK/runtime/agent patterns, also read at least one current source/example/doc file that proves that pattern before writing code.

## Pre-write checklist

Before the first bundle file write, make sure all of the following are true:

- You have read the actual current pytest/README material that defines the bundle contract.
- You know the exact test file you plan to run, or you have a concrete discovery plan to find it.
- For every requested platform-integrated feature, you have read at least one current source/example/doc file that proves the needed pattern.
- The exact import paths and runtime symbols you intend to use are confirmed in visible evidence.
- You know the smallest bundle shape that should pass the current contract.

If any item above is still missing, do not write bundle code yet. Gather the missing evidence first.

## Where the tests are

The reusable test fixtures are exposed by `react.doc` under their real knowledge-space path:
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests/...`

That is a real path under one common `ks:` root, not a separate test-only namespace.

These files are intentionally **not** indexed for `react.search_knowledge`.
Do not assume one fixed file path up front.
Instead:
- keep `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests` as the logical base
- use generated exec code plus `bundle_data.resolve_namespace(...)` to browse the subtree
- inspect the discovered README / pytest files that are relevant
- treat the resolved directory as a subtree root, not as a directory that necessarily contains pytest files directly

## How to use them

1. Read this skill if it is relevant:
   - `react.read(["sk:tests.bundles"])`
2. Read the actual fixture material before writing bundle code:
   - start from `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests`
   - discover the relevant README / pytest files by browsing from generated exec code if the exact file is not already known
   - bring the exact discovered file(s) back into visible context with `react.read(...)`
3. In generated exec code, if discovery is needed:
   - resolve `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests` with `bundle_data.resolve_namespace(...)`
   - browse the returned `physical_path` recursively
   - identify the relevant descendant README / pytest files for bundle validation
   - emit exact logical refs or a short listing into `OUTPUT_DIR/...`
4. Only after the tests are actually read, write or patch the bundle code.
5. Set environment variable `BUNDLE_UNDER_TEST` to the generated bundle root.
6. Write the pytest results to `OUTPUT_DIR/...` so they come back to the agent clearly.

Implementation strategy:
- read the tests first to understand the minimum required shape
- if exact test paths are not obvious, do a narrow browse of the tests subtree first and then read the exact discovered files
- for platform-integrated bundle code, read at least one current source/example/doc file that proves the needed SDK pattern before implementing it
- implement the smallest version that can satisfy that shape
- run the smoke test early
- only add non-essential structure after the minimal contract passes

## Default authoring loop

Use this as the normal workflow for bundle generation, repair, and modification:

1. Read the tests and fixture README first.
2. Read the current source/examples/docs that prove the requested feature patterns.
3. If exact files are still unknown, use a small exec browse to discover candidate files, then `react.read` the exact discovered paths.
4. Write the smallest implementation that should satisfy both the tests and the explicit user request.
5. Run the smoke test immediately.
6. If it fails, read the exact traceback and the exact failing test/source/import targets before patching.
7. Repeat with small corrections until the smoke test passes.
8. Only then add optional polish, additional helpers, or non-essential features.

Do not jump from skills directly to large speculative code generation.
Do not patch repeatedly from guesses when the traceback already tells you what exact file or import to inspect.

## Test-oriented exploration strategy

When preparing to write or repair a bundle, use this exploration strategy:

1. Read the tests and their README first.
2. Extract any exact file paths, import paths, symbol names, or structural expectations from the tests.
3. If the tests imply a platform pattern that is still unclear, read the current source/example/doc files that prove that pattern.
4. If exact files are still unknown, use isolated exec to search the relevant subtree like you would locally:
   - resolve the subtree with `bundle_data.resolve_namespace(...)`
   - recursively list files
   - search for imports, base classes, descriptor patterns, decorators, or symbol names
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
- logical base: `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests`
- discovered relative path: whatever the exec-time browse step found
- follow-up logical ref: `f"{logical_base}/{relative_path}"`

## Expected bundle-under-test contract

The current draft smoke test expects:
- `__init__.py`
- `entrypoint.py`
- `tools_descriptor.py`
- `skills_descriptor.py`

It also checks that:
- `entrypoint.py` imports successfully
- `tools_descriptor.py` imports successfully
- `skills_descriptor.py` imports successfully
- `entrypoint.py` defines non-empty `BUNDLE_ID`
- `entrypoint.py` exposes a bundle workflow class derived from `BaseEntrypoint`

Current entrypoint import contract:
- use `from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint`
- or `from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import BaseEntrypointWithEconomics`
- do not generate or keep legacy imports like `from kdcube_ai_app.apps.chat.sdk.workflow import AIWorkflow`
- do not claim the bundle passes unless pytest was actually run and returned success

## Draft execution pattern

In generated exec code, do roughly this:

1. define `logical_base = "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests"`
2. resolve it with `bundle_data.resolve_namespace(logical_base)`
3. treat `Path(res["ret"]["physical_path"])` as the subtree root
4. first prefer the known reusable smoke test:
   - `test_root / "bundles" / "test_generated_bundle_smoke.py"`
5. if you need discovery instead of the known exact file, search recursively:
   - `sorted(test_root.rglob("test_*.py"))`
   - do **not** use a non-recursive top-level glob like `test_root.glob("test_*.py")`
6. run:
   - `python -m pytest <discovered_test_file>`
7. set:
   - `BUNDLE_UNDER_TEST=<generated bundle root>`
8. write:
   - test summary
   - stdout/stderr
   into files under `OUTPUT_DIR`

Concrete execution sketch:

```python
from pathlib import Path
import os
import subprocess
import sys

logical_base = "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests"
res = await agent_io_tools.tool_call(
    fn=bundle_data.resolve_namespace,
    params={"logical_ref": logical_base},
    call_reason="Resolve bundle test fixtures path",
    tool_id="bundle_data.resolve_namespace",
)

assert res.get("ok"), res
test_root = Path(res["ret"]["physical_path"])

preferred = test_root / "bundles" / "test_generated_bundle_smoke.py"
if preferred.exists():
    test_file = preferred
else:
    candidates = sorted(test_root.rglob("test_*.py"))
    assert candidates, f"No pytest files found under {test_root}"
    test_file = candidates[0]

env = dict(os.environ)
env["BUNDLE_UNDER_TEST"] = str(bundle_root)

proc = subprocess.run(
    [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short"],
    text=True,
    capture_output=True,
    env=env,
)
```
