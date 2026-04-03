---
id: ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests/bundles/README.md
title: "Bundle Tests"
summary: "Draft reusable pytest smoke tests for validating a generated bundle before handoff."
tags: ["tests", "bundles", "pytest", "kdcube-copilot"]
keywords: ["BUNDLE_UNDER_TEST", "generated bundle", "smoke tests", "entrypoint", "descriptor"]
---
# Bundle Tests

This directory contains a very small draft pytest harness that a copilot-style agent can run
against a generated bundle before handoff.

## What this is for

Use these tests when the agent:
- generated a new bundle under the current turn workspace
- needs a quick structural/import smoke test
- wants a repeatable validation step before final answer

## Discovery rule

From the agent's perspective, do not start with a hardcoded exact fixture path.
Start from:
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests`

Then browse the exec-visible subtree and inspect the discovered README / pytest files that are relevant.
Treat that resolved path as the subtree root, not as a directory that necessarily contains pytest files directly.

Current reusable smoke test location:
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests/bundles/test_generated_bundle_smoke.py`

## Expected input

The test harness expects:
- environment variable `BUNDLE_UNDER_TEST`
- value = absolute path to the generated bundle root directory

Example bundle-under-test layout:
- `__init__.py`
- `entrypoint.py`
- `tools_descriptor.py`
- `skills_descriptor.py`

Current entrypoint contract:
- import `BaseEntrypoint` from `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`
- or import `BaseEntrypointWithEconomics` from `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint_with_economic.py`
- do not use legacy imports such as `kdcube_ai_app.apps.chat.sdk.workflow`

## How the agent should use this

1. Keep a logical base such as `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests`.
2. Use exec-only namespace resolution on that base to get an exec-visible physical path.
3. Treat the resolved physical path as the subtree root.
4. Prefer the known reusable smoke test file:
   `bundles/test_generated_bundle_smoke.py`
5. If exact-path usage is not appropriate, browse that subtree recursively and identify the relevant descendant pytest file.
   Do not use a non-recursive top-level glob such as `test_root.glob("test_*.py")`.
6. Run pytest on the chosen test file.
7. Set `BUNDLE_UNDER_TEST` to the generated bundle root path.
8. Write the pytest result summary to an `OUTPUT_DIR` file.

## Example pytest command in generated exec code

```python
import os
import subprocess
import sys

env = dict(os.environ)
env["BUNDLE_UNDER_TEST"] = str(bundle_root)

preferred = test_root / "bundles" / "test_generated_bundle_smoke.py"
if preferred.exists():
    test_file = preferred
else:
    candidates = sorted(test_root.rglob("test_*.py"))
    assert candidates, f"No pytest files found under {test_root}"
    test_file = candidates[0]

proc = subprocess.run(
    [sys.executable, "-m", "pytest", str(test_file)],
    text=True,
    capture_output=True,
    env=env,
)
```

This is a draft fixture for agent E2E testing. It is intentionally small.
