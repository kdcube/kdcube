---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/tool-call-validation-README.md
title: "Tool Call Validation Phase"
summary: "Pre-execution ReAct policy phase for validating and preparing external tool calls."
tags: ["sdk", "agents", "react", "event-source", "tool-call-validation"]
keywords: ["tool_call_validation", "final_params", "exec preflight", "rendering input preparation"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/event-source-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/events-blocks-and-rendering-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/external-exec-README.md
---
# Tool Call Validation Phase

`tool_call_validation` runs after ReAct has selected an external tool and after
visible `ref:` parameters are bound, but before the generic `react.tool.call`
block is emitted and before the tool executes.

The target is one mutable call-validation object for the occurrence:

```python
{
    "tool_id": "...",
    "event_source_id": "...",
    "tool_call_id": "...",
    "event_id": "...",
    "base_params": {...},
    "final_params": {...},
    "state": state,
    "turn_id": "...",
    "outdir": "...",
    "workdir": "...",
    "blocks": [],
    "notice_rows": [],
    "state_updates": {},
    "retry_decision": False,
    "stop": False,
}
```

Policies mutate this target. The caller applies the target actions: emitted
notice rows become normal ReAct notices, `state_updates` merge into the runtime
state, and `stop=true` prevents execution.

## Current Built-In Uses

| Tool family | Policy |
|---|---|
| `exec_tools.execute_code_python` | `exec_tools.tool_call_validation.exec_preflight` |
| `rendering_tools.write_*` | `rendering_tools.tool_call_validation.prepare_inputs` |

Exec validation normalizes execution contract/code paths, rejects contaminated
code channels, writes needed local timeline state, and can request a retry
before execution. Rendering validation normalizes output paths and ensures
referenced local assets are present under the current turn artifact root.
Renderer `content=ref:...` parameters must resolve to text in the renderer's
requested input format. For `fi:` refs, validation must use the real file
content and must not fall back to a timeline-rendered preview block. External
owner objects should be imported with `react.pull` before rendering.

## Rule

Validation owns source-specific input preparation. It should not produce result
blocks. Result-side representation belongs to `block_production`.
