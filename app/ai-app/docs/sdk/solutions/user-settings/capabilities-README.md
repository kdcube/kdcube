---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/capabilities-README.md
title: "Agent Capability Control And Selection"
summary: "How descriptor authority, a user's Agent Card base, and a conversation-local positive selection combine to govern runtime exposure and the capability picker."
status: current
tags: ["sdk", "solutions", "capabilities", "agent-selection", "control-card", "connection-hub", "picker", "widget"]
updated_at: 2026-09-22
keywords:
  [
    "agent_capabilities",
    "agent_selection_update",
    "agent capability Control Card",
    "Agent Card base",
    "conversation capability snapshot",
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
---
# Agent Capability Control And Selection

An application descriptor says what one hosted agent may use. KDCube turns
that declaration into a live Connection Hub boundary, lets each user keep a
positive Agent Card base inside it, and lets each conversation narrow that
base without rewriting it. The same effective projection governs the picker
and the next agent turn.

This page owns the KDCube capability-projection and picker contract. Connection
Hub owns Card persistence, revisioning, composition, drift, and enforcement;
see [Delegated Access Cards](../connections/delegated-cards/delegated-cards-README.md).

## Three authority layers, one effective projection

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
  positive base explicitly selected for this user and agent
  edited in Connection Hub
              |
              v  snapshot once for a new conversation
CONVERSATION BASE + CONVERSATION SELECTION
  finite positive projections stored for this conversation
  picker writes replace only the conversation selection
              |
              v  intersect on every read and turn
EFFECTIVE PROJECTION
  current Control ∩ current Agent Card ∩ conversation base ∩ conversation selection
              |
              +-- runtime tool/skill/service/target exposure
              +-- picker state for the same catalog rows
```

`agent_capability.sync` resolves the current Control Card and Agent Card
composition. On the first materialized read for a conversation, KDCube stores
that finite positive projection as both the conversation base and its initial
selection. On every later read and turn, KDCube intersects the current Card
projection with the stored base and stored selection. The runtime translates
that result into its existing internal disabled-map adapter and narrows the
tool config, skill config, named-service dispatcher, conversation targets,
resource operations, resource families, and subagent installation. The
adapter is not another authority source.

A finite positive base is required. A deny list cannot name a capability that
did not exist when the conversation started, so a later catalog or Agent Card
addition could otherwise enter an open conversation implicitly. Current Card
revocations still close access immediately. If a capability that belonged to
the original conversation base is later re-granted, it becomes effective again
only when the conversation selection still includes it.

Platform system tools are outside this user-selectable boundary. Every
descriptor capability is inside it. If the current Card projection or the
conversation projection cannot be resolved, selectable capabilities close for
the turn; preference-store availability never widens that result.

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
| `allowed_selected` | The current Control Card permits it and the Agent Card selects it. | Its conversation state is shown below. | Exposed only if the conversation base and selection also include it. |
| `allowed_unselected` | The current Control Card permits it, but the Agent Card does not select it. | Unchecked, disabled, and labeled **Not in Agent Card**. | Not exposed. |
| `not_allowed` | It appears in the live descriptive catalog but is outside the current Control authority, or the projection is unavailable. | Unchecked, disabled, and labeled **Not permitted**; expandable details remain readable. | Not exposed. |

Within `allowed_selected`, the conversation adds two visible facts:

- a row outside the conversation's frozen base is disabled and labeled **Not
  in conversation base**;
- a row inside that base is tagged **Inherited** while it matches the starting
  state, or **Changed here** after this conversation turns it off or back on.

The unscoped served picker shows the Agent Card base read-only. Its Connection
Hub action carries the stable resident Card id and opens that exact Card's
editor. A picker opened from a chat carries that conversation id and changes
only that conversation.

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
new descriptor default. PostgreSQL owns each conversation's immutable starting
base and mutable conversation selection.

Connection Hub gives these two Cards different owner surfaces. The resident
Agent Card editor changes only the positive base inside the linked descriptor
ceiling and writes a revision-checked Card update. The linked descriptor
Control Card displays its authority and presentation metadata as a
descriptor-managed ceiling; changing the descriptor, then synchronizing,
revises it. It is not shown as an empty generic resource Card.

Descriptor, Agent Card, and conversation changes remain independent:

| Change | Result |
| --- | --- |
| Descriptor removes a capability | The Control Card revision removes it; the next live intersection denies it without changing the Agent Card, conversation rows, or credential. |
| Descriptor adds a capability | It appears outside existing Agent Cards and conversations. An authorized user must add it to an Agent Card in Connection Hub; only a conversation first materialized afterward can inherit it. |
| User changes an Agent Card | Connection Hub writes a new Card revision. New conversations inherit the new base; open conversations remain bounded by their stored starting base and current revocations. |
| User changes a chat picker | `agent_selection_update` replaces only that conversation's positive selection. It never changes the Agent Card or another conversation. |
| Labels or other metadata change | The picker can show the new presentation without treating it as authority. |

The remint rule depends on the authorization representation. A static or
embedded credential grant never grows because a descriptor/catalog grew; it
needs an explicit new grant and, for snapshot credentials, re-consent or
re-minting. This hosted-agent capability relationship is pointer-backed: the
credential continues to identify the Agent Card, while the current Control
Card and Agent Card selection are resolved live. Revising descriptor authority
therefore does not remint that credential. A newly offered capability still
stays outside the Agent Card until the user chooses it in Connection Hub, and
outside existing conversation bases afterward.

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
  current Card projection, and the named conversation snapshot. With no
  conversation id it returns the Agent Card base as a read-only scope.
- `agent_selection_update` requires a conversation id for capability changes
  and saves the draft as that conversation's positive selection. The same
  request may update model, instruction, presentation, and cache preferences
  in PostgreSQL; those fields are not Card authority.

The picker body (`useCapabilityPickerBody` in
`@kdcube/components-react/chat`) is rendered in the composer popover, its
expanded modal, and the served `capabilities` widget. The shells share one
draft and save only on **Save changes**. Closing and reopening retains the
draft for the active chat; changing conversations drops unsaved UI state.
Saved capability changes apply to that conversation from its next message.
Cache timing choices do not change their scope. Model, instruction,
presentation, and cache preferences retain their own baseline and conversation
rules described below.

## Preferences, consent, and service descriptions

Model, instruction, presentation, and cold-cache-policy choices remain typed
preferences in `user_bundle_props`; see the
[User Settings Solution](user-settings-solution-README.md). A preference-store
failure falls back to configured preference defaults while the current Card
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
