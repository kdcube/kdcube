---
id: ks:tests/bundles/README.md
title: "Bundle Tests"
summary: "Draft reusable pytest smoke tests for validating a generated bundle before handoff."
tags: ["tests", "bundles", "pytest", "react-doc"]
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

From the agent's perspective, do not start with a hardcoded `ks:tests/bundles/...` path.
Start from:
- `ks:tests`

Then browse the exec-visible subtree and inspect the discovered README / pytest files that are relevant.

## Expected input

The test harness expects:
- environment variable `BUNDLE_UNDER_TEST`
- value = absolute path to the generated bundle root directory

Example bundle-under-test layout:
- `__init__.py`
- `entrypoint.py`
- `tools_descriptor.py`
- `skills_descriptor.py`

## How the agent should use this

1. Keep a logical base such as `ks:tests`.
2. Use exec-only namespace resolution on that base to get an exec-visible physical path.
3. Browse that subtree, identify the relevant pytest file, and run it under that physical path.
4. Set `BUNDLE_UNDER_TEST` to the generated bundle root path.
5. Write the pytest result summary to an `OUTPUT_DIR` file.

## Example pytest command in generated exec code

```python
import os
import subprocess
import sys

env = dict(os.environ)
env["BUNDLE_UNDER_TEST"] = str(bundle_root)

proc = subprocess.run(
    [sys.executable, "-m", "pytest", str(test_file)],
    text=True,
    capture_output=True,
    env=env,
)
```

This is a draft fixture for agent E2E testing. It is intentionally small.
