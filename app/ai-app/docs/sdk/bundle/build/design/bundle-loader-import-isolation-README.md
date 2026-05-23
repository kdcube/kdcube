---
id: ks:docs/sdk/bundle/build/design/bundle-loader-import-isolation-README.md
title: "Bundle Loader Import Isolation"
summary: "Design note for in-process bundle loading, package-relative bundle-local imports, and why the loader does not make top-level bundle package names globally safe."
tags: ["sdk", "bundle", "loader", "imports", "runtime", "design"]
keywords: ["bundle loader import isolation", "sys.modules bundle collision", "package relative imports", "in process bundle loading", "bundle suite import lint"]
updated_at: 2026-05-21
see_also:
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/service/maintenance/connection-pooling-README.md
---
# Bundle Loader Import Isolation

This note records the current import-isolation decision for bundles loaded by
the chat processor/proc runtime.

## Runtime Boundary

Proc intentionally loads multiple bundles in one worker process.

That process also owns shared runtime resources:

- one async Redis pool per proc worker
- one Postgres pool per worker
- shared browser/runtime helpers and other heavy process-local services
- bundle loader caches, singleton instances, and interface manifests

Moving to one OS process per bundle would change the scaling unit. It would
multiply Redis/Postgres pools and process-local services by bundle count, not
only by worker count. That may be useful later for isolation, but it must be a
separate capacity design tied to gateway pool limits and deployment sizing.

## Import Rule

Because bundles share one interpreter, Python `sys.modules` is process-global.
Top-level names such as `services`, `apps`, `tools`, `resources`, `models`, or
`utils` are not bundle-scoped names.

Bundle-local code must therefore use package-relative imports:

```python
from .services.news import build_news_service
from .tools import report_tools
from ..storage import Store
```

Do not use top-level bundle-local imports:

```python
from services.news import build_news_service
from tools import report_tools
import utils
```

Those imports can resolve to another bundle's already-loaded module or poison
later imports for another bundle.

## Tool Modules

The same rule applies to bundle tool code.

It covers:

- `tools_descriptor.py` when imported by the workflow
- Python modules referenced from `TOOLS_SPECS` with `ref`
- bundle-local helpers imported by those tool modules

For bundle-local tools, prefer `ref` entries:

```python
TOOLS_SPECS = [
    {"ref": "tools/report_tools.py", "alias": "report", "use_sk": True},
]
```

Then the tool module can import same-bundle helpers with package-relative
imports:

```python
from ..services.storage import ReportStore
from ..resources.prompts import REPORT_PROMPT
```

Do not write bundle-local tool imports as:

```python
from services.storage import ReportStore
from resources.prompts import REPORT_PROMPT
```

The tool subsystem loads file-based `ref` modules through the shared dynamic
module loader. That loader gives the file a synthetic package name derived from
the bundle package root, so relative imports work in both:

- normal in-process tool execution
- isolated runtime supervisor execution after bundle snapshot restore and path
  rewriting

The package chain must be a Python package chain. Keep `__init__.py` in the
bundle root and in package directories such as `tools/`, `services/`, and
`resources/`.

Use `module` entries only for real installed Python modules, for example SDK
tools or external packages. A bundle-local tool should be a `ref` entry so the
runtime can rewrite it for distributed and isolated execution.

## Loader Behavior

The loader still supports descriptor/module shapes that rely on raw module
paths, including module values such as `entrypoint` and parent-subdir paths
such as `user-mgmt@1-0.service`.

For that reason the loader currently attempts the configured module import and
checks whether the resolved module file is inside the expected bundle root. If
the import returned another bundle's cached top-level module, the loader falls
back to direct virtual-package loading.

This fallback makes package-relative imports work. It does not make top-level
bundle-local imports safe.

The loader should not try to solve this by globally purging names like
`services` from `sys.modules`. That would make bundles order-dependent: a
later bundle could remove or replace a module still expected by an earlier
bundle during lazy imports.

## Guardrails

The supported guardrails are:

- authoring docs require package-relative imports for bundle-local code
- loader tests prove package-relative imports survive a foreign `services`
  package already present in `sys.modules`
- shared bundle-suite import lint rejects top-level imports whose root is a
  Python module or package owned by the bundle directory

These guardrails keep the current in-process scaling model while making import
collisions deterministic authoring errors.
