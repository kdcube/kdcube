---
id: ks:docs/sdk/bundle/bundle-venv-README.md
title: "Bundle Venv"
summary: "Reference for running selected bundle helpers in cached isolated virtualenv subprocesses: boundary rules, dependency installation, cache behavior, and reload implications."
tags: ["sdk", "bundle", "venv", "execution", "subprocess"]
keywords: ["isolated virtualenv helpers", "cached subprocess execution", "dependency installation boundary", "cross boundary data flow", "venv cache behavior", "reload implications", "python dependency isolation"]
see_also:
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-lifecycle-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
---
# Bundle Venv

`@venv(...)` runs selected bundle helpers in a cached per-bundle subprocess environment.

Use it for:

- dependency-heavy leaf jobs
- libraries you do not want in the shared proc interpreter
- isolated Python execution with serializable inputs and outputs

Do **not** use it for:

- normal bundle orchestration
- request handlers themselves
- passing live proc objects such as communicator instances, request objects, DB pools, Redis clients, or tool bindings

## Minimal pattern

```python
from kdcube_ai_app.infra.plugin.agentic_loader import venv


@venv(requirements="requirements.txt", timeout_seconds=120)
def build_report(payload: dict) -> dict:
    ...
```

Important:

- the decorated callable is the boundary
- arguments and return values must be serializable
- bundle code reload and venv rebuild are separate concerns

## Runtime model

At call time the runtime:

1. resolves the current bundle id and bundle root
2. creates or reuses a cached venv under bundle-managed local storage
3. installs the bundle requirements from the referenced `requirements.txt`
4. runs the decorated callable in a subprocess using that venv
5. deserializes the result back into proc

Cache rule:

- one cached venv per bundle id
- rebuild happens when the referenced `requirements.txt` content changes

## What crosses the boundary

Safe:

- plain dict/list/str/int/float/bool structures
- bytes-like payloads when the contract expects them
- bundle-defined dataclasses only if they live in normal importable bundle modules

Not safe:

- `self.comm`
- `self.comm_context`
- `get_current_comm()`
- `get_current_request_context()`
- DB pools
- Redis clients
- framework request objects
- proc-side globals such as tool/runtime registries

Practical rule:

- keep orchestration in proc
- pass plain data into the `@venv(...)` helper
- return plain data out

## Reload behavior

Changing bundle Python source:

- still requires the normal proc-side bundle reload path

Changing only `requirements.txt`:

- does **not** require a proc restart
- the cached venv rebuilds lazily on the next call to that decorated helper

Typical local loop:

```bash
kdcube reload <bundle_id> --workdir <runtime-workdir>
```

That reload is for bundle code and descriptor-backed config. The venv cache is separate.

## See also

- [bundle-platform-integration-README.md](bundle-platform-integration-README.md)
- [bundle-lifecycle-README.md](bundle-lifecycle-README.md)
- [bundle-interfaces-README.md](bundle-interfaces-README.md)
