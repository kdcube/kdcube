---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/subagents/subagents-runtime-bootstrap-and-reduce-README.md
title: "Subagents Runtime Bootstrap And Reduce"
summary: "Concrete runtime contract for launching subagents with a scoped serializable runtime spec, isolated workspace, comm recording, and host-side reduction."
status: draft
tags: ["sdk", "agents", "subagents", "runtime", "portable-spec", "comm", "recording", "workspace"]
updated_at: 2026-06-20
keywords:
  [
    "subagent runtime",
    "subagent bootstrap",
    "portable spec",
    "PORTABLE_SPEC_JSON",
    "COMM_SPEC",
    "agent_id",
    "comm_recorded_events.json",
    "reduce",
    "side files",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/comm-recording-event-sinks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/micro-agents-and-subagents-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-iso-runtime.md
---
# Subagents Runtime Bootstrap And Reduce

This document describes the runtime contract for subagents. The same contract
applies whether the subagent runs in the same process, an `asyncio` task, a
worker thread, a local subprocess, or an isolated/external runtime.

The core rule is:

```text
enter subagent scope
  -> override the serializable runtime identity
  -> build child runtime spec inside that scope
  -> run the subagent in its own workspace/fence
  -> reduce child side files and selected outputs into the coordinator
```

No live Python object is the contract. The contract is the scoped portable spec,
the child workspace/outdir, and the reducer handoff.

## Vocabulary

| Term | Meaning |
| --- | --- |
| host agent | The ReAct agent or coordinator that launches the subagent. |
| subagent | A scoped agent execution with its own `agent_id`, prompt/config surface, workspace, accounting context, and comm envelope identity. |
| fence | The runtime boundary around the subagent: async task, thread, subprocess, Docker/Fargate runtime, or a logical workspace boundary. |
| bootstrap | Reconstructing runtime services inside the fence from `PORTABLE_SPEC_JSON`, `COMM_SPEC`, and descriptor-backed settings. |
| reduce | Merging selected child outputs and side files back into the host/coordinator after the fence finishes. |
| side file | A JSON file written by the child runtime for host merge, such as `comm_recorded_events.json` or `delta_aggregates.json`. |

## Existing Runtime Pieces

The platform already has the mechanics this design uses:

| Piece | Existing contract |
| --- | --- |
| Portable context | `PORTABLE_SPEC_JSON` carries `contextvars.comm_ctx`, `contextvars.accounting`, runtime descriptors, model config, and integration descriptors. |
| Runtime bootstrap | Child runtimes restore contextvars and rebuild trusted services from descriptors. |
| Comm spec | `COMM_SPEC` rebuilds a `ChatCommunicator` in child runtimes. |
| Comm recording | Portable recording selectors cross in `COMM_SPEC`; child runtime records into its own buffer. |
| Side-file handoff | Child writes `comm_recorded_events.json` and `delta_aggregates.json`; host merges by deduplicating record ids/chunks. |
| Accounting context | Accounting context is restored in the child and can export `agent_id` as a stored usage-event context field. |

Primary anchors:

```text
kdcube_ai_app/apps/chat/sdk/runtime/snapshot.py
kdcube_ai_app/apps/chat/sdk/runtime/bootstrap.py
kdcube_ai_app/apps/chat/sdk/runtime/execution.py
kdcube_ai_app/apps/chat/emitters.py
```

## Identity Scope

A subagent scope must bind one owning `agent_id`. This is the only agent
identity that needs to be propagated for comm recording selection.

Inside the subagent scope:

```text
RuntimeCtx.agent_id          = "research.react.agent"
comm.service.agent_id        = "research.react.agent"
accounting.context.agent_id  = "research.react.agent"
BUNDLE_CALL_CONTEXT.agent_id = "research.react.agent"        optional mirror
```

The comm envelope then has the same stable identity:

```json
{
  "type": "react.tool.call",
  "metadata": {
    "agent_id": "research.react.agent"
  },
  "event": {
    "agent": "research.react.agent",
    "step": "react.tool.call",
    "status": "completed"
  }
}
```

`metadata.agent_id` is the stable producer identity used by recording and
telemetry correlation. `event.agent` is the visible actor/source label. In a
subagent scope they normally have the same value, but the selector contract
uses only `metadata.agent_id` for stable agent identity.

No `parent_agent_id` or `root_agent_id` is part of this contract. A coordinator
that needs ancestry can store it in its own durable state or in a separate
correlation id, but the runtime identity that crosses the fence is the active
subagent `agent_id`.

## Bootstrap Flow

The host prepares the subagent runtime from inside the subagent scope:

```text
Host coordinator
  |
  | create subagent scope:
  |   agent_id = "research.react.agent"
  |   workspace_ref = "subagent/research/..."
  |   recording selector includes metadata.agent_id when needed
  |
  | bind scoped RuntimeCtx / comm service / accounting context
  |
  | build PORTABLE_SPEC_JSON
  | build COMM_SPEC
  | create subagent workdir/outdir
  v
Subagent fence
  |
  | bootstrap runtime from spec
  | restore comm_ctx / accounting context
  | rebuild ChatCommunicator from COMM_SPEC
  | run subagent loop/tools
  | write side files
  v
Host reducer
  |
  | merge side files
  | merge selected artifacts/workspace deltas
  | send or retain recorded events
  v
Coordinator state
```

The host must build the portable spec after the subagent identity is bound.
Building the spec before the override captures the parent identity and produces
incorrect accounting/comm attribution inside the child.

## Serializable Spec

The subagent bootstrap spec is the normal portable runtime spec plus scoped
identity.

```text
PORTABLE_SPEC_JSON
  model_config
  integrations
  accounting_storage
  contextvars
    run_ctx
    comm_ctx
      REQUEST_CONTEXT
        actor tenant/project/user
        routing session/conversation/turn/bundle
        bundle_call_context
      BUNDLE_ID
      BUNDLE_CALL_CONTEXT
        agent_id = "research.react.agent"       optional mirror
        workspace_ref = "subagent/research/..." optional
      NAMED_SERVICE_DISCOVERY
    accounting
      context
        tenant_id / project_id / user_id / session_id
        conversation_id / turn_id / app_bundle_id / component
        agent_id = "research.react.agent"
      enrichment

COMM_SPEC
  service
    tenant / project / user / bundle_id
    agent_id = "research.react.agent"
  conversation
    session_id / conversation_id / turn_id
  recording
    enabled
    portable selectors
    scopes
    max_events
```

The spec stays JSON-safe. It contains ids and descriptors, not live clients,
callbacks, model services, or database handles.

## Workspace Fence

A subagent should have a distinct workspace/ref surface, even when it runs in
the same process. The fence protects three things:

- files created or edited by the subagent;
- artifacts and side files produced by the subagent;
- reducible outputs selected by the coordinator.

Typical layout:

```text
host turn out/
  subagents/
    research.react.agent/
      work/
      out/
        delta_aggregates.json
        comm_recorded_events.json
        artifacts/
        reduce.json
```

`reduce.json` is the future coordinator-owned summary/result contract. It
should contain bounded, explicit values that the coordinator asked for. It is
not a dump of the subagent prompt, full timeline, or arbitrary workspace.

## Comm Recording In Subagents

Recording works the same way inside a subagent as inside an isolated tool:

```text
host comm
  -> active comm.record(...) scope
  -> export COMM_SPEC with portable recording selector
  -> child comm reconstructs recording scope
  -> child comm records matched events locally
  -> child writes comm_recorded_events.json
  -> host merges child records into host comm
```

Selectors can filter by subagent identity using `metadata.agent_id`:

```python
comm.record({
    "include": {
        "types": ["react.tool.call", "accounting.usage"],
        "agent_ids": ["research.react.agent"]
    }
})
```

`agent_ids`, `agent_id`, and `metadata.agent_id` are equivalent selector keys.
They read the comm envelope or recorded item `metadata.agent_id`. The existing
`agents` selector remains the visible `event.agent` selector.

The host reducer can also filter after merge:

```python
records = comm.export_recorded_events({
    "include": {"metadata.agent_id": ["research.react.agent"]}
})
```

This lets a subagent coordinator request exactly the recorded event subset it
needs without adding ancestry fields to every event.

## Reduce Boundary

The reducer is the only host-side code that consumes child runtime outputs.

Inputs:

```text
subagent out/
  comm_recorded_events.json
  delta_aggregates.json
  reduce.json
  artifacts/
  workspace patch or explicit changed files
```

Reducer actions:

1. Merge comm side files into host comm.
2. Merge delta aggregates into host comm.
3. Read `reduce.json` when present and validate it against the coordinator's
   expected schema.
4. Promote selected artifacts or workspace deltas into the coordinator's
   workspace/timeline.
5. Emit a bounded coordinator event when the host needs to show or persist the
   subagent result.

Host-side comm merge is already available:

```python
comm.merge_recorded_events_from_file(outdir / "comm_recorded_events.json")
comm.merge_delta_cache_from_file(outdir / "delta_aggregates.json")
```

The same pattern should be used for subagent workspaces. Subagent-specific
reduce files can be added next to these existing side files without changing
the comm side-file contract.

## Runtime Boundary Matrix

| Boundary | Bootstrap requirement | Reduce requirement |
| --- | --- | --- |
| Same async task | bind subagent context before call | direct host merge of in-memory state or explicit side files |
| New `asyncio` task | create task inside copied/bound context | direct host merge after task result |
| Worker thread | run thread target under `contextvars.copy_context()` or pass portable spec | return result plus side-file directory |
| Local subprocess | pass `PORTABLE_SPEC_JSON`, `COMM_SPEC`, workdir/outdir | read side files from child outdir |
| Docker/Fargate supervisor | pass normal isolated runtime payload with scoped spec | copy output directory back, then merge side files |
| Dedicated subagent workspace service | pass scoped serializable spec over service boundary | service returns reduce manifest and side-file/artifact refs |

The safe default is to treat every subagent as if it may cross a process
boundary. That forces the implementation to rely on the serialized spec and
reduce files rather than process-local objects.

## Minimal Host Algorithm

```python
async def run_subagent(coordinator, *, agent_id: str, task: dict):
    with bind_subagent_scope(agent_id=agent_id, task=task):
        coordinator.comm.record({
            "include": {
                "agent_ids": [agent_id],
                "types": ["react.tool.call", "accounting.usage"]
            }
        }, scope={"owner": "subagent", "agent_id": agent_id})

        portable_spec = build_portable_spec()
        comm_spec = coordinator.comm._export_comm_spec_for_runtime()
        workdir, outdir = create_subagent_workspace(agent_id)

        result = await launch_subagent_runtime(
            portable_spec=portable_spec,
            comm_spec=comm_spec,
            workdir=workdir,
            outdir=outdir,
        )

    coordinator.comm.merge_recorded_events_from_file(outdir / "comm_recorded_events.json")
    coordinator.comm.merge_delta_cache_from_file(outdir / "delta_aggregates.json")
    reduced = read_and_validate_reduce(outdir / "reduce.json")
    return reduced
```

The helper names above are illustrative. The contract is the ordering:

1. bind scope;
2. build spec;
3. run;
4. merge/reduce.

## Failure Semantics

| Case | Expected behavior |
| --- | --- |
| Subagent completes | reducer merges side files and selected outputs. |
| Managed error | child should write side files before returning the error where possible. |
| Graceful cancellation | child should best-effort flush side files. |
| Hard kill / node loss | side files may be absent; reducer treats missing files as no-op and reports the subagent failure separately. |
| Nonportable recording selector | selector does not cross into child runtime; host can still record host-visible events. |

The reducer must be idempotent. Comm recorded items already deduplicate by
`record_id`. Workspace/artifact reduce should use explicit ids or revision
checks.

## What Should Be Implemented Next

1. A `bind_subagent_scope(agent_id=..., workspace_ref=...)` helper that updates
   `RuntimeCtx.agent_id`, comm service `agent_id`, accounting context
   `agent_id`, and optional bundle call context mirrors in one place.
2. A subagent workspace allocator that creates deterministic work/out roots.
3. A subagent launcher that always builds `PORTABLE_SPEC_JSON` and `COMM_SPEC`
   inside the subagent scope.
4. A reducer helper that merges existing comm side files and validates
   subagent-specific `reduce.json`.
5. Tests for:
   - agent id captured only after scope binding;
   - subprocess/isolated bootstrap restores `accounting.context.agent_id`;
   - `comm_recorded_events.json` merges into host comm;
   - `comm.export_recorded_events({"include": {"agent_ids": [...]}})` returns
     only the selected subagent events.
