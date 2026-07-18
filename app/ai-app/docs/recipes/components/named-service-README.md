# Recipe: Named-Service App

A named-service app makes a domain realm usable by generic agents and UI
surfaces. It lets unfamiliar apps interoperate through object refs such as
`mem:record:...`, `task:issue:...`, `conv:fi:...`, and `cnv:...` without copying the
realm's storage or action rules.

This is not the only way apps interact in KDCube. Apps can also expose API/MCP,
Event Bus, Data Bus, cron/jobs, or UI surfaces. Use named services when the
realm needs generic ReAct exploration/exploitation, Pinboard pins, Chat context,
Scene open actions, and provenance.

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

## The Human Layer (Required Authoring Step)

A realm ships with TWO readers: the agent (reads `about`/`object_schema` and
works the grammar) and the user (reads the capability picker's service card
and narrows the realm per operation/action). The card renders declared text
only, so authoring includes the presentation:

```text
spec.metadata
  presentation.about          purpose sentence in user terms
  presentation.works_with     internal realm: what it operates on
    (or presentation.third_party for a provider-backed realm)
  presentation.operations     {op: {label, description}} in user terms
  presentation.actions        {action: {label, description}}
  object_kinds                {kind: one-liner}
```

Declare this metadata on BOTH spec declarations when the provider has two —
the instance-spec helper and the `@named_service_provider(...)` registration;
one place only leaves raw grammar tokens on the card (see
[Providers — The Presentation Layer](../../sdk/namespace-services/providers-README.md)).

Provider-backed realms (acting through a user's external account) also
declare `metadata.connected_accounts` — provider/connector ids, claims,
`claims_by_operation` where the realm truly differentiates, and human
`claim_labels`. Field-by-field reference:
[Providers — The Presentation Layer](../../sdk/namespace-services/providers-README.md);
what users see and control:
[Per-User Agent Capabilities](../../sdk/solutions/user-settings/capabilities-README.md);
consent semantics (demand-driven at the attempt, scoped claims, deep links):
[Delegated Accounts](../../sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md).

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

## Agent Access Is Granted Per Agent

A realm's agent consumers — external MCP clients and agents hosted in KDCube
apps alike — enter under the user's delegated-by grant for that specific agent
(`kdcube-agent:<app>:<agent>`), checked at the tool attempt when the
deployment's delegated catalog publishes the namespace; a missing grant raises
a one-click consent demand in chat. Provider-backed realms keep the
connected-account consent as a second, per-call-checked layer. The realm
implements neither check — both ride the platform boundary. Model:
[Agents Acting On Behalf Of The User](../../sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).

## Current Gaps

- Data Bus subjects and Event Bus event names should converge on URI-like naming.

## Related Docs

- [Architecture Of What You Build](../../arch/architecture-of-what-you-build-README.md)
- [Component Recipes](./README.md)
- [Components Ecosystem Architecture](../../sdk/solutions/ecosystem-component/components-ecosystem-README.md)
- [Ecosystem Component Contract](../../sdk/solutions/ecosystem-component/ecosystem-component-README.md)
- [Namespace Services](../../sdk/namespace-services/README.md)
- [Namespace Service Providers](../../sdk/namespace-services/providers-README.md)
- [Namespace Service Clients](../../sdk/namespace-services/clients-README.md)
- [Namespace Service Integration](../../sdk/namespace-services/integration-README.md)
- [Resolver And Policy Registration](../../sdk/solutions/event-hub/resolver-and-policy-registration-README.md)
