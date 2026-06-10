# Conversation Event-Bus Simulator

This test fixture emulates the conversation external-event bus journey with
the same parties and integration sites used by the real runtime. It does not
start the web server, chat processor, browser, or ReAct runtime, but every
simulator method is named after the production site it represents.

Canonical design docs:

- `docs/service/comm/conversation-event-bus-orchestrator-README.md`
- `docs/sdk/events/conversation-event-lane-state-README.md`
- `docs/sdk/events/external-events-journey-and-handling-README.md`
- `docs/arch/proc/events-orchestration-README.md`

## Production Sites

| Simulator method | Production site | Responsibility |
| --- | --- | --- |
| `process_chat_message_ingress()` | `apps/chat/ingress/ingress_core.py::process_chat_message` | Accept one `external_events[]` batch. Reactive batches write lane events and one wake atomically; rejected wakes leave no lane event. |
| `EventLaneWakePublisher.publish_for_event()` | `sdk/events/event_bus/wakeup.py` | Build and send `ExternalEventLaneWakeup` through an injected queue sender. |
| `processor_resolve_wake()` | `apps/chat/processor.py::_resolve_queue_item_payload` | Resolve a wake to its lane event, lock `T`, and decide whether this wake schedules a turn or is ignored. |
| `base_workflow_construct_react_handler()` | `sdk/solutions/chatbot/base_workflow.py` before timeline load | Open `T.handler` for the current runtime turn. |
| `context_browser_reader_drain()` | `sdk/solutions/react/browser.py` initial/live external-event fold | Accept lane events into the live turn path only while `T.handler.status == open`. |
| `react_runtime_close_gate()` | `sdk/solutions/react/v2/runtime.py` and `v3/runtime.py` close gate | Close the handler only if ReAct processed all lane events accepted by the reader. |
| `base_workflow_finish_turn_persist_artifacts()` | `sdk/solutions/chatbot/base_workflow.py::finish_turn` artifact persistence section | Persist turn artifacts after the handler is closed. |
| `context_browser_post_save_external_event_handoff()` | `sdk/solutions/react/browser.py::ContextBrowser.post_save_external_event_handoff` | Inspect unconsumed reactive work after persistence and publish a wake through `EventLaneWakePublisher`. |
| `context_browser_close_external_event_handler()` | `sdk/solutions/react/browser.py::ContextBrowser.close_external_event_handler` called by `finish_turn` after artifacts persist | Run post-save handoff, then release `T.consumer`. |
| `base_workflow_finish_turn_close_external_event_handler()` | `sdk/solutions/chatbot/base_workflow.py::finish_turn` call into `ContextBrowser.close_external_event_handler()` | Preserve the real finalization order: BaseWorkflow owns the sequence; ContextBrowser writes the lane consumer release. |

The post-save handoff is a ContextBrowser/runtime site. It is not implemented
in `processor.py` by scanning the lane after a task returns.

## State

All shared decisions use `T`, the Redis-backed conversation lane state table:

```
T.handler.turn_id
T.handler.status = open | closed
T.handler.status_at

T.consumer.status = active | scheduled | none
T.consumer.status_at

T.last_processed_event_timestamp
T.last_processed_reactive_event_timestamp
```

Event envelope timestamps are the semantic event clock. The simulator never
uses Redis stream IDs as event order.

## Main Flow

```
process_chat_message_ingress()
  normalize accepted batch
  prepare lane records
  if batch contains reactive event:
    atomically:
      publish batch to Stream
      EventLaneWakePublisher.publish_for_event(first reactive event)
    if wake enqueue is rejected:
      reject request
      leave Stream unchanged
  else:
    publish batch to Stream
        |
        v
processor_resolve_wake()
  read wake
  resolve wake.event_lane.event_id from Stream
  lock(T)
    if wake.ts <= T.last_processed_reactive_event_timestamp:
      ignore wake
    else if T.consumer.status == active and T.consumer.status_at is fresh:
      ignore wake
    else if T.consumer.status == scheduled and T.consumer.status_at is fresh:
      ignore wake
    else:
      set T.consumer.status = scheduled
      set T.consumer.status_at = now
  unlock(T)
        |
        v
base_workflow_construct_react_handler()
  lock(T)
    set T.handler.turn_id = turn_id
    set T.handler.status = open
    set T.handler.status_at = now
  unlock(T)
        |
        v
context_browser_reader_drain() activation
  lock(T)
    if T.handler.status == open:
      set T.consumer.status = active
      set T.consumer.status_at = now
  unlock(T)
        |
        v
context_browser_reader_drain() initial and live lane drain
  read events from Stream without holding lock(T)
  lock(T)
    if T.handler.status == open:
      materialize fetched events into timeline
      acknowledge consumed lane events
      set T.last_processed_event_timestamp =
        max(T.last_processed_event_timestamp, accepted event timestamps)
      set T.last_processed_reactive_event_timestamp =
        max(T.last_processed_reactive_event_timestamp, accepted reactive timestamps)
      set T.consumer.status = active
      set T.consumer.status_at = now
    else:
      leave fetched events unconsumed
  unlock(T)
        |
        v
react_runtime_close_gate()
  handler_processed_event_timestamp =
    max event timestamp in the last timeline snapshot ReAct actually rendered

  lock(T)
    if handler_processed_event_timestamp < T.last_processed_event_timestamp:
      keep T.handler.status = open
      ReAct must continue from the newer timeline
    else:
      set T.handler.status = closed
      set T.handler.status_at = now
  unlock(T)
        |
        v
base_workflow_finish_turn_persist_artifacts()
  persist artifacts
        |
        v
base_workflow_finish_turn_close_external_event_handler()
  call ContextBrowser.close_external_event_handler()
        |
        v
context_browser_post_save_external_event_handoff()
  if any unconsumed reactive event has timestamp >
     T.last_processed_reactive_event_timestamp:
    EventLaneWakePublisher.publish_for_event(event)
        |
        v
context_browser_close_external_event_handler()
  lock(T)
    set T.consumer.status = none
    set T.consumer.status_at = now
  unlock(T)
```

## Required Scenarios

`test_event_bus_state.py` keeps the simulator coverage for:

- accepted reactive batch wakes the processor and finalizes after drain
- rejected reactive wake leaves no accepted lane event behind
- non-reactive events do not wake the processor but can be drained by an active reader
- events arriving during an active turn are handled by the active reader
- duplicate wake for already processed reactive work is ignored
- multiple wakeups before handler construction collapse to one scheduled consumer
- stale active consumer acknowledgement can be rescheduled
- reader cannot consume after handler close
- ContextBrowser post-save external-event handoff wakes remaining unconsumed reactive work
- reader holding `lock(T)` prevents the close gate from missing an event in hand
