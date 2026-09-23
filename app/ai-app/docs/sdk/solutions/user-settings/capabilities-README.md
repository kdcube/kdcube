---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/capabilities-README.md
title: "Agent Capability Control And Selection"
summary: "How descriptor authority defines the capability ceiling, Agent Card selections define user defaults, and conversations choose within that live range."
status: current
tags: ["sdk", "solutions", "capabilities", "agent-selection", "control-card", "connection-hub", "picker", "widget"]
updated_at: 2026-09-23
keywords:
  [
    "agent_capabilities",
    "agent_selection_update",
    "agent capability Control Card",
    "Agent Card defaults",
    "conversation capability selection",
    "capability projection",
    "allowed_selected",
    "allowed_unselected",
    "not_allowed",
    "capability picker",
    "descriptor capability authority",
    "application resource",
    "conversation targets",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-cards/delegated-cards-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/user-settings-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/conversation/search-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/delegated-cards.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/testing/end-to-end-acceptance.md#resident-agent-control-card-agent-card-and-conversation-projection
---
# Agent Capability Control And Selection

An application descriptor says what one hosted agent may use. KDCube turns
that declaration into a live Connection Hub boundary, lets each user save
Agent Card defaults inside it, and lets each conversation choose independently
inside the same boundary. The same effective projection governs the picker and
the next agent turn.

This page owns the KDCube capability-projection and picker contract. Connection
Hub owns Card persistence, revisioning, composition, drift, enforcement, and
the administrator Card workflow. The KDCube host owns the privileged
write-through into the active application descriptor. The canonical Card
contract is
[Delegated Access Cards](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/delegated-cards.md),
and the complete administrator/user/conversation/Slack verification is
[Connection Hub End-To-End Acceptance](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/testing/end-to-end-acceptance.md#resident-agent-control-card-agent-card-and-conversation-projection).

## One authority ceiling, two selections

```text
APP + AGENT DESCRIPTOR
  capability_authority                  executable ceiling
  capability_metadata                   labels, descriptions, presentation
  delegated_catalog operation grants    service-operation -> grant vocabulary
              |
              v  agent_capability.sync
STABLE CREDENTIALLESS CONTROL CARD
  exact resource urn:kdcube:app:<tenant>:<project>:<app>:<agent>
  current descriptor authority; composition = AND
              |
              | linked when the user's stable Agent Card is created
              v
STABLE AGENT CARD
  positive defaults explicitly selected for this user and agent
  editable in the Agent Card picker or Connection Hub
              |
              v  copied as the initial value for a new conversation
CONVERSATION SELECTION
  positive selection stored for this conversation
  picker may choose any current Control-allowed capability
              |
              v  intersect with current Control on every read and turn
EFFECTIVE PROJECTION
  current Control ∩ conversation selection
              |
              +-- runtime tool/skill/service/target exposure
              +-- picker state for the same catalog rows
```

`agent_capability.sync` resolves the current Control Card and Agent Card. On
the first materialized read for a conversation, KDCube stores the Agent Card
selection as both provenance and the conversation's initial selection. The
Agent Card is a default, not a second ceiling: that conversation can later
select any capability permitted by the current Control Card, including one
that was not selected on the Agent Card when the conversation started.

The internal synchronization operation carries two separate values.
`descriptor_payload.capability_defaults` is the descriptor-owned Control Card
preset; `selected_capabilities` is the user's current Agent Card selection.
The defaults are part of the descriptor revision, so changing only the default
model or instruction still revises and rematerializes the Control Card. An
existing Agent Card keeps an explicit user choice and ordinary deselections;
synchronization fills a singleton model or instruction from the current
default only while that selection holds no value, and never overwrites a
chosen value. An explicit selection replacement does not fill during that
replacement; a later synchronization fills a singleton left empty.

On every read and turn, KDCube intersects the current Control authority with
the stored conversation selection. A Control removal closes access
immediately. A genuinely new Control capability becomes selectable but stays
off until the user selects it; neither an Agent Card nor an existing
conversation grows implicitly. A temporarily removed capability is different:
its saved user selection remains durable while it is unavailable and becomes
effective again if the administrator restores it. The runtime translates the
effective projection into its existing internal disabled-map adapter and
narrows the tool config, skill config, named-service dispatcher, conversation
targets, resource operations, resource families, and subagent installation.
The adapter is not another authority source.

For example, a conversation created while user-configured MCPs were permitted
may retain that older selection as history. If today's descriptor removes the
custom-MCP resource family, the current Control intersection removes those
tools from the turn and the operation boundary denies a direct attempt. The
older conversation cannot preserve yesterday's authority. Its stored choice
is retained as provenance, however, so restoring the same family makes that
choice effective again. A conversation that had the family deselected remains
deselected.

Platform system tools are outside this user-selectable boundary. Every
descriptor capability is inside it. If the current Control authority or the
required conversation selection cannot be resolved, selectable capabilities
close for the turn; preference-store availability never widens that result.

## Authority is data, presentation is metadata

The producer sends structurally separate fields:

- `capability_authority` contains only stable capability identities grouped by
  family;
- `capability_metadata` contains bounded display material such as titles and
  descriptions;
- standard `resource_grants`, `resource_operations`, and
  `named_service_operations` carry Connection Hub enforcement authority.

`tool_traits`, ANNOUNCE text, labels, descriptions, connected-account status,
and other presentation details do not become constraints merely because the
picker displays them. Conversely, a label cannot make a capability callable.
The descriptor revision evidence covers the authority independently of its
presentation.

For named services, the producer reads the operation and its grants directly
from the owning app's
`config.delegated_catalog.named_service_namespaces.*.tools` declaration. That
is the operation-to-grant map used by Connection Hub; there is no second map in
the picker or runtime.

## Picker authority and selection states

Each selectable row has exactly one authority state:

| State | Meaning | Picker behavior | Runtime behavior |
| --- | --- | --- | --- |
| `allowed_selected` | The current Control Card permits it and the current scope selects it. | Checked and mutable. The scope label distinguishes an Agent Card default from a conversation choice. | Exposed in the current scope. |
| `allowed_unselected` | The current Control Card permits it, but the current scope does not select it. | Unchecked and mutable. | Not exposed until selected in the current scope. |
| `not_allowed` | It appears in the live descriptive catalog but is outside the current Control authority, or the projection is unavailable. | Unchecked, disabled, and labeled **Not permitted**; expandable details remain readable. | Not exposed. |

Picker provenance distinguishes an Agent Card default from a choice overridden
for one conversation. That provenance is explanatory state, not authority and
not a lock. Every `allowed_selected` and `allowed_unselected` row remains
mutable in the scope being edited. Only `not_allowed` is immutable.

The picker names that provenance in user terms:

- **Current default** means the Agent Card's persisted selection.
- **New default** means the unsaved Agent Card draft differs from that
  persisted selection.
- **Starting value** means this conversation still uses the selection copied
  from the Agent Card revision recorded when the conversation was created.
- **This conversation** means this conversation's draft or saved selection
  differs from that starting value.

These labels explain where a value came from. They do not make a permitted
row read-only.

The unscoped served picker edits Agent Card defaults and names that state in
the surface. Saving replaces the positive Agent Card selection within the
Control ceiling. A picker opened from chat carries the current conversation id
and changes only that conversation. Every open fetches its own scope; while
that request is pending, the picker shows loading rather than presenting a
cached Agent Card or another conversation as current.

The first materialized conversation snapshot stores both its finite positive
starting value and the exact Agent Card revision that supplied it. The picker
displays that revision as provenance. Later Agent Card edits change defaults
for future conversations, not this conversation. Current Control revisions
still govern the live range: removals deny immediately and additions become
available for explicit selection.

When an Agent Card still selects an identity that a later Control revision no
longer declares, the picker retains and displays that saved identity under
**Missing from Control Card**. It is unavailable and excluded from the
effective projection, but it is not silently erased from the durable user
choice.

Selecting one operation selects only that operation. Sharing a claim with a
sibling operation does not select the sibling visually or operationally. A
parent row that is not allowed also makes its nested rows not allowed.

The descriptor can offer these user-selectable families:

| Family | Selection unit | Effect when absent from the projection |
| --- | --- | --- |
| Python tools | group or individual tool | Removed from the turn's tool config. |
| MCP | server or listed tool | Removed from the MCP tool config. |
| Named services | namespace or operation/action | Removed from the roster or rejected before provider dispatch. |
| App resources | resource or operation | Rejected at the governed resource operation boundary. |
| Skills | concrete skill id | Removed from every skill consumer. |
| Conversation targets | exact application resource | Rejected before conversation storage is read. |
| Delegated resource families | family id | Not projected to the resident caller. |
| Subagents | one `enabled` capability | Spawner, tool, and guidance are not installed. |

## Creation, edits, and descriptor changes

The Control Card id is deterministic for the grantor, descriptor issuer kind,
and exact app/agent resource. The Agent Card id is deterministic for
the grantor and `kdcube-agent:<app>:<agent>` caller profile. Neither id includes
the selected capability set.

On first bootstrap, KDCube creates or resolves both Cards and links the Control
Card to the Agent Card. When the user has no earlier choice, KDCube converts
the descriptor defaults into the initial positive Agent Card selection. An
existing saved deny map is applied to those defaults before that first Card is
created. Once the Card exists, its positive selection is the durable user
choice; later synchronization does not replace it from PostgreSQL or from a
new descriptor default. PostgreSQL owns each conversation's starting
provenance and mutable conversation selection.

Connection Hub gives these two Cards different owner surfaces. The resident
Agent Card editor and KDCube's full-page Agent Card picker change only the
positive user defaults inside the linked descriptor ceiling and write a
revision-checked Card update. The linked descriptor Control Card uses the same
full Card editor for the administrator-owned ceiling and defaults. It is not
an empty generic resource Card and it is not an ordinary-user surface.

Connection Hub owns the exact Control and Agent Card resource catalogs,
including descriptor-serializable rows and user-resource-family discovery
containers. See
[Delegated Access Cards](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/delegated-cards.md)
for that Card read-model contract.

A platform administrator's Control Card save writes through to the exact
application and agent entry in descriptor-owned bundle properties before the
live Card update is reported successful. It carries the reviewed defaults,
resource authority, named-service operations and bounded metadata under
`agent_capability_control_overrides`. Changing the source descriptor and then
synchronizing revises the same Control projection in the other direction.
The active descriptor remains the outer ceiling in both cases.

The concrete write path is:

1. Connection Hub's `DelegatedAccessPanel.tsx` calls `mergeBundleProps()` in
   `src/api/client.ts` for the exact application and agent.
2. The administrator-only request reaches KDCube
   `apps/chat/proc/rest/integrations/integrations.py::set_bundle_props`.
3. KDCube persists the merge through
   `infra/plugin/bundle_store.py::_put_bundle_props_locked`, which owns the
   authoritative descriptor-backed application properties.

Only after that request succeeds does Connection Hub submit the corresponding
revision-checked Control Card update. A descriptor-write refusal therefore
leaves the live Card unchanged.

Both Card editors group child tools and operations beneath their tool group,
MCP server, named service, or resource, and show declared descriptions in the
form. The Agent Card also retains user-owned connected accounts and custom MCP
configuration. Repeated operation labels therefore retain their owning
context without moving user-owned credentials into the Control Card.

Descriptor, Agent Card, and conversation changes remain independent:

| Change | Result |
| --- | --- |
| Descriptor removes a capability | The Control Card revision removes it; the next live intersection denies it without changing the credential. A retained Agent Card choice is shown as **Missing**. |
| Descriptor adds a genuinely new capability | It becomes selectable in Agent Card and conversation pickers but remains off in existing selections until explicitly chosen. |
| Descriptor restores a capability selected before its removal | Its preserved selection becomes effective again; scopes that had deselected it remain off. |
| Administrator edits the Control Card | Connection Hub writes the revision-checked ceiling/default change into descriptor-owned application properties, then updates the Card. The saved descriptor value survives runtime reload. |
| User changes Agent Card defaults | A revision-checked Card update replaces the positive defaults. New conversations start from them; existing conversation selections do not change. |
| User changes a chat picker | `agent_selection_update` replaces only that conversation's positive selection. It may select any current Control-allowed capability and never changes the Agent Card or another conversation. |
| Labels or other metadata change | The picker can show the new presentation without treating it as authority. |

The remint rule depends on the authorization representation. A static or
embedded credential grant never grows because a descriptor/catalog grew; it
needs an explicit new grant and, for snapshot credentials, re-consent or
re-minting. This hosted-agent capability relationship is pointer-backed: the
credential continues to identify the Agent Card, while the current Control
Card authority is resolved live. Revising descriptor authority therefore does
not remint that credential. A newly offered capability stays off in each
existing Agent Card and conversation selection until the user chooses it in
that scope.

## Resource selectors and data ownership

An application capability resource has five fixed parts:

```text
urn:kdcube:app:<tenant>:<project>:<application>:<agent>
```

`*` is valid only as a complete application or agent segment. It matches one
segment and never consumes `:`, so
`urn:kdcube:app:<tenant>:<project>:*:*` means every app and every agent in that
one tenant/project deployment. It cannot cross the tenant or project boundary.

Target breadth and data ownership are independent. A project may permit an
agent to target all apps and agents with that selector while each conversation
read still binds to the current authenticated/delegated user's rows. Reading a
different user's data additionally requires the explicit
`conversations:read:any_user` grant. Broad target selection does not imply that
grant.

For an explicit conversation target, registry membership is only existence
evidence. An unknown app returns `404 conversation_bundle_not_found`; a known
app outside the live Card/descriptor/user selection returns `403` with the
refusing-layer code. Registration alone never authorizes a target. The full
boundary is documented in [Conversation Search](../conversation/search-README.md).

## Operations and picker shells

Two app operations carry the surface:

- `agent_capabilities` reads the descriptive catalog, preference choices,
  current Control and Agent Card projection, and the named conversation
  selection. With no conversation id it returns the editable Agent Card
  defaults.
- `agent_selection_update` saves according to the explicit scope. With a
  conversation id it replaces that conversation's positive selection. Without
  one it replaces the Agent Card defaults through Connection Hub. The same
  request may update model, instruction, presentation, and cache preferences
  in PostgreSQL; those fields are not Card authority.

The picker body (`useCapabilityPickerBody` in
`@kdcube/components-react/chat`) is rendered in the composer popover, its
expanded modal, and the served `capabilities` widget. The shells share one
draft and save only on **Save changes**. Closing and reopening retains the
draft for the active chat; changing conversations drops unsaved UI state.
Saved composer changes apply to that conversation from its next message;
saved full-page changes become the defaults for new conversations. Cache
timing choices do not change their scope. Model, instruction, presentation,
and cache preferences retain their own baseline and conversation rules
described below.

## Preferences, consent, and service descriptions

Model, instruction, presentation, and cold-cache-policy choices remain typed
preferences in `user_bundle_props`; see the
[User Settings Solution](user-settings-solution-README.md). A preference-store
failure falls back to configured preference defaults while the current Control
and conversation capability projection remains in force.

Connected-account coverage is also separate. Coverage chips describe whether
the selected operation's provider claims are satisfied. Selecting an operation
does not bypass demand-driven consent, and two operations that require the same
claim remain distinct selections. See
[Delegated Accounts](../connections/delegated-accounts/delegated-accounts-README.md).

Expanded named-service cards render their labels, descriptions, object kinds,
operations, actions, and connected-account requirements from the provider's
self-description. Missing self-description is shown as unavailable descriptive
content; the UI does not invent authority or copy. Provider declaration details
live in [Named-Service Providers](../../namespace-services/providers-README.md).
