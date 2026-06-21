# Recipe: Named-Service App

A named-service app gives a namespace behavior. It lets unfamiliar apps interoperate through object URIs such as `mem:record:...`, `task:issue:...`, `fi:...`, and `cnv:...`.

## Runtime Shape

```text
provider app
  declares namespace: task
  implements operations
    object.search
    object.get
    object.action
    block.produce
    block.render
  emits events
    task-scoped updates
    accounting.usage
    UI events for target surfaces

consumer app
  holds object_ref only
  calls named-service operation
  receives normalized response
  renders through shared contracts
```

## Object Actions

Scene open routing uses provider-backed actions:

```text
user drops task:issue:123 onto task area
  scene calls object.action(open, object_ref)
  provider returns:
    ui_event.target_surface = task_tracker.issue_editor
    ui_event.payload = object/open data
  scene matches target_surface to a configured command contract
  scene posts command to the target widget
```

The scene does not decide what a task, memory, or file means. The provider decides the default open effect and target surface.

## Read And Render Pipeline

```text
react.pull(object_ref)
  named-service object.get
  materializes bytes or structured object
  preserves meta.object_ref

react.read(materialized_path)
  block.produce for namespace if declared
  output blocks retain meta.object_ref

timeline projection
  group blocks by meta.object_ref namespace
  call block.render for declared render policies
  provider may patch/replace owned blocks within limits
```

`block.render` is optional. When declared, it gives the namespace provider a late rendering hook for model-facing or UI-facing projection.

## Events

Named-service providers should emit events with enough identity for consumers:

```json
{
  "type": "task.issue.changed",
  "metadata": {
    "object_ref": "task:issue:123",
    "agent_id": "agent-or-surface-id"
  }
}
```

Today some widget-local event names still exist. The direction is namespace-owned event names and object URIs.

## Current Gaps

- Data Bus subjects and Event Bus event names should converge on URI-like naming.

## Related Docs

- [Namespace Services](../../sdk/namespace-services/README.md)
- [Namespace Service Providers](../../sdk/namespace-services/providers-README.md)
- [Namespace Service Clients](../../sdk/namespace-services/clients-README.md)
- [Namespace Service Integration](../../sdk/namespace-services/integration-README.md)
- [Resolver And Policy Registration](../../sdk/solutions/event-hub/resolver-and-policy-registration-README.md)
