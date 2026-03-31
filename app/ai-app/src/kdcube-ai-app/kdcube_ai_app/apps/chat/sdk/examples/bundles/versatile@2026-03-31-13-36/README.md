# versatile bundle

`versatile@2026-03-31-13-36` is the full-feature reference bundle for bundle builders.

It intentionally demonstrates the main SDK bundle surfaces together in one place, so a human or bundle-builder copilot can learn the platform from one concrete implementation before branching into narrower examples.

## What it demonstrates

| Capability | Where to look |
| --- | --- |
| Minimal bundle contract | `entrypoint.py`, `orchestrator/workflow.py`, `tools_descriptor.py`, `skills_descriptor.py` |
| React workflow | `entrypoint.py`, `orchestrator/workflow.py`, `agents/gate.py` |
| Economics / quotas | `entrypoint.py` via `BaseEntrypointWithEconomics` and `app_quota_policies` |
| Bundle-local tools | `tools/preference_tools.py` |
| Bundle-local skills | `skills/product/preferences/SKILL.md` |
| Shared local bundle storage | `preferences_store.py`, `entrypoint.py:on_bundle_load`, `orchestrator/workflow.py` |
| Storage backend (`AIBundleStorage`) | `tools/preference_tools.py:export_preferences_snapshot` |
| MCP tools | `tools_descriptor.py` |
| Direct isolated exec from bundle code | `entrypoint.py:preferences_exec_report` |
| Custom TSX widget | `ui/PreferencesBrowser.tsx`, `entrypoint.py:preferences_widget` |

## Bundle behavior

- The workflow is a normal gate → solver React loop.
- The bundle heuristically captures preference statements from user messages while the chat is running.
- Preferences are stored per user under the shared bundle storage root.
- The solver can consult those preferences with the bundle-local tool:
  - `preferences.get_preferences(recency, kwords)`
- Explicit updates can also be written through:
  - `preferences.set_preference(key, value, source)`
- A storage-backend snapshot can be exported through:
  - `preferences.export_preferences_snapshot(filename)`

## Preference storage layout

Inside `bundle_storage_root()`:

```text
preferences/
  users/
    <user_id>/
      current.json
      events.jsonl
```

This keeps the bundle readable for humans:
- `current.json` is the latest materialized view
- `events.jsonl` is the append-only observation history

## Widget + operations

This bundle exposes two entrypoint operations:

- `preferences_widget`
  - reads current preference data
  - renders `ui/PreferencesBrowser.tsx`
  - returns iframe-ready HTML
- `preferences_exec_report`
  - runs a tiny report job through the isolated exec runtime
  - writes a markdown report artifact from bundle storage content

## Tool surface

The bundle includes:

- SDK tools:
  - `io_tools`
  - `ctx_tools`
  - `exec_tools`
  - `rendering_tools`
- Bundle-local tools:
  - `preferences`
- MCP connectors:
  - `web_search`
  - `deepwiki`
  - `stack`
  - `docs`
  - `local`
  - `firecrawl`

## Skills

The bundle ships one bundle-local skill:

- `product.preferences`
  - teaches the solver when to consult stored user preferences before personalizing an answer

## Minimal vs versatile

| Shape | Required to pass the basic suite | Demonstrated here |
| --- | --- | --- |
| Minimal bundle | entrypoint, compiled graph, role models, tools descriptor, skills descriptor | yes |
| Custom tools | optional | yes |
| Custom skills | optional | yes |
| Economics | optional | yes |
| MCP | optional | yes |
| Shared local storage | optional | yes |
| Storage backend snapshot | optional | yes |
| Direct isolated exec from bundle code | optional | yes |
| Widget / operations | optional | yes |

## Running the shared bundle test suite

```bash
BUNDLE_UNDER_TEST=/abs/path/to/versatile@2026-03-31-13-36 \
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
pytest -q app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle
```

## Related docs

- `docs/sdk/bundle/bundle-index-README.md`
- `docs/sdk/bundle/bundle-dev-README.md`
- `docs/sdk/bundle/bundle-reference-versatile-README.md`
