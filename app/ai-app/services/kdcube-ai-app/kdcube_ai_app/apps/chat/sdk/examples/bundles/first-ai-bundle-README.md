# First AI Bundle (Minimal, streaming)

This guide shows the smallest possible AI bundle that works with the chat runtime.
It explains how to configure it and how to emit streams and steps.

---

## 1) Minimal bundle layout

```
my_bundle/
├── entrypoint.py
└── __init__.py   # optional
```

The entrypoint must export a class decorated with `@agentic_workflow(...)`.

---

## 2) Minimal entrypoint (BaseEntrypoint)

```python
# my_bundle/entrypoint.py
from typing import Dict, Any
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.apps.chat.sdk.comm.emitters import AIBEmitters

BUNDLE_ID = "demo.simple"

@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class SimpleWorkflow(BaseEntrypoint):
    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        text = (params.get("text") or "").strip()

        emit = AIBEmitters(self.comm)
        await emit.step(step="workflow_start", status="started", title="Kickoff")

        # stream answer
        for i, tok in enumerate(["Hello! ", "You wrote: ", f"{text}"]):
            await emit.delta(text=tok, index=i, marker="answer")

        await emit.step(step="workflow_complete", status="completed", title="Done")

        return {
            "final_answer": f"Hello! You wrote: {text}",
            "followups": ["Ask another question."]
        }
```

Bare minimum to integrate:
- A decorated class with a stable `BUNDLE_ID`.
- Implement `execute_core(...)` and return a dict with at least `final_answer`.
- Use `self.comm` (or `AIBEmitters`) to emit steps/deltas.

`BaseEntrypoint.run(...)` is already implemented and calls:
`pre_run_hook(...)` → `execute_core(...)` → `run_accounting(...)` → `post_run_hook(...)`.

---

## 3) Configuration (how the runtime loads the bundle)

Use `AGENTIC_BUNDLES_JSON` to register the bundle:

```bash
export AGENTIC_BUNDLES_JSON='{
  "default_bundle_id": "demo.simple",
  "bundles": {
    "demo.simple": {
      "id": "demo.simple",
      "name": "Demo Simple",
      "path": "/bundles/demo_simple",
      "module": "entrypoint",
      "singleton": false,
      "description": "Minimal example bundle"
    }
  }
}'
```

For Docker Compose you typically also set:

```bash
export AGENTIC_BUNDLES_ROOT=/bundles
```

Then mount your bundle to `/bundles/demo_simple`.

---

## 4) Streaming output (steps, deltas, events)

### Token deltas (keep it simple)

Recommended:
- `marker="thinking"` for agent thoughts
- `marker="answer"` for the main answer

```python
await emit.delta(text="Thinking... ", index=0, marker="thinking")
await emit.delta(text="Final answer ", index=0, marker="answer")
```

Other supported markers (use only if you own the client UI):
- `subsystem` (widget streams)
- `tool` (tool output)
- `canvas` (inline artifacts)
- `timeline_text` (compact timeline entries)

Custom markers are allowed, but the client must know how to render them.
See [comm-system.md](../../../doc/comm-system.md).

Subsystem widget docs:
- [code-exec-widget-README.md](../../runtime/solution/widgets/code-exec-widget-README.md)
- [exec.py](../../runtime/solution/widgets/exec.py)

Timeline text reference:
- [react.py](../../runtime/solution/react/react.py)

---

## 4.1) Connecting SDK agents (example: ctx.reconciler)

SDK agents accept streaming callbacks so you can wire them into your bundle’s emitter.
Here’s a minimal example using the context reconciler:

```python
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_reconciler import ctx_reconciler_stream
from kdcube_ai_app.apps.chat.sdk.comm.emitters import AIBEmitters

emit = AIBEmitters(self.comm)

async def thinking_delta(text: str):
    # stream reconciler thinking into the side channel
    await emit.delta(text=text, index=0, marker="thinking", agent="ctx.reconciler")

rr = await ctx_reconciler_stream(
    self.models_service,
    guess_package_json="{}",
    current_context_str="",
    search_hits_json="[]",
    bucket_cards_json="[]",
    limit_ctx=10,
    max_buckets=5,
    gate_decision={},
    on_thinking_delta=thinking_delta,
    timezone=self.comm_context.user.timezone,
)
```

Notes:
- The reconciler expects JSON strings for its inputs.
- `on_thinking_delta` is an async callback; you can route it to `thinking`,
  or to `subsystem` if you want a dedicated widget stream.
- Similar patterns apply to other SDK agents that accept streaming hooks.

## 4.4) Comm API reference

For the full event envelope and transport details, see:
- [README-comm.md](../../comm/README-comm.md)
- [comm-system.md](../../../doc/comm-system.md)

## 4.2) Context search (Conversation history)

Use `ContextBrowser.search(...)` to retrieve relevant turns from the conversation history.

```python
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.browser import ContextBrowser

ctx_browser = ContextBrowser(
    ctx_client=self.ctx_client,
    logger=self.logger,
    turn_view_class=TurnView,
)

targets = [
    {"where": "assistant", "query": "risk register"},
    {"where": "user", "query": "budget"},
]

best_tid, hits = await ctx_browser.search(
    targets=targets,
    user=user_id,
    conv=conversation_id,
    track=track_id,
    top_k=5,
    days=365,
    half_life_days=7.0,
    scoring_mode="hybrid",
    with_payload=True,
)
```

Notes:
- `ctx_client` is required; it already contains `conv_idx` and `model_service`.
- The `hits` list includes `turn_id`, scores, and (optionally) payloads.

## 4.3) Storage layout (artifacts & attachments)

If you need to inspect where artifacts/attachments land on disk or in S3, see:
- [sdk-store-README.md](../../storage/sdk-store-README.md)
- [conversation-artifacts-README.md](../../runtime/solution/context/conversation-artifacts-README.md)

### Steps (timeline)
Emit as `started` and `completed` for each phase:

```python
await emit.step(step="gate", status="started", title="Gate")
await emit.step(step="gate", status="completed", data={"decision": "allow"})
```

### Custom events
Use `event(...)` when you want a custom UI widget:

```python
await emit.event(type="chat.custom", title="My Widget", data={"items": [1,2,3]})
```

---

## 5) Which emitter should I use?

You have two equivalent options:

1) **Typed SDK emitter (recommended):** `AIBEmitters(self.comm)`
   - Validates payload shape and fills defaults.
2) **Raw communicator:** `self.comm.step(...)`, `self.comm.delta(...)`
   - Direct, minimal, no validation.

Both publish to the same Redis relay + channel stream and will appear in the UI.

---

## 6) Emitting directly to infrastructure (advanced)

If you need to emit outside the workflow (e.g., background tasks), build a communicator
from a `ChatTaskPayload` and the relay:

```python
from kdcube_ai_app.apps.chat.emitters import build_comm_from_comm_context, build_relay_from_env

comm = build_comm_from_comm_context(comm_context, relay=build_relay_from_env())
await comm.delta(text="hello", index=0, marker="answer")
```

This uses the same Redis relay as the normal workflow path.

---

## 7) Minimal request flow (how your bundle is called)

1) Client sends a message with `agentic_bundle_id` over its active channel (Socket.IO or SSE).
2) Chat gateway enqueues the task.
3) Processor loads your bundle and calls `BaseEntrypoint.run(...)`.
4) Your bundle emits steps/deltas to Redis relay.
5) Relay pushes events to the same client channel (or an integration relay for third-party destinations).

---

### Transport note

The runtime is channel-agnostic. The client chooses the channel (Socket.IO, SSE, or an integration relay),
and the bundle only emits events to the relay. This allows intermediate routers that forward events to
Telegram/Slack or other destinations without changing bundle code.

---

If you want a more advanced template with memory, context reconciliation,
clarification gating, and follow-ups, build on top of `BaseEntrypoint` and
add your own agents in `execute_core(...)`.

See also:
- [browser-README.md](../../runtime/solution/context/browser-README.md)
