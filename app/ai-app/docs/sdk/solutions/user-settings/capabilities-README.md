---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/capabilities-README.md
title: "Agent Capability Control And Selection"
summary: "How an app descriptor becomes a live credentialless Control Card, how a resident agent Card stores the user's positive selection, and how their current intersection drives both runtime exposure and the three-state capability picker."
status: current
tags: ["sdk", "solutions", "capabilities", "agent-selection", "control-card", "connection-hub", "picker", "widget"]
updated_at: 2026-09-20
keywords:
  [
    "agent_capabilities",
    "agent_selection_update",
    "agent capability Control Card",
    "resident agent Card",
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
that declaration into a live Connection Hub boundary so a descriptor change,
a user's choice, the picker, and the next agent turn cannot disagree. The
descriptor remains the ceiling; the signed-in user chooses a positive subset
inside it.

This page owns the KDCube capability-projection and picker contract. Connection
Hub owns Card persistence, revisioning, composition, drift, and enforcement;
see [Delegated Access Cards](../connections/delegated-cards/delegated-cards-README.md).

## One live projection, two consumers

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
              | linked when the user's stable resident agent Card is created
              v
STABLE RESIDENT AGENT CARD
  positive capabilities explicitly selected by this user
              |
              v  live Card composition on every read/turn
EFFECTIVE PROJECTION = current Control authority ∩ resident selection
              |
              +-- runtime tool/skill/service/target exposure
              +-- picker state for the same catalog rows
```

The effective Card `projection` returned by `agent_capability.sync` is the one
capability list used for actual exposure. The current runtime translates that
positive list into its existing internal disabled-map adapter and then narrows
the tool config, skill config, named-service dispatcher, conversation targets,
resource operations, resource families, and subagent installation. That
adapter is not another authority source. `agent_capabilities` annotates the
picker catalog from the same sync result.

Platform system tools are outside this user-selectable boundary. Every
descriptor capability is inside it. If the live Card projection cannot be
resolved, selectable capabilities close for the turn and the picker marks
them not allowed; preference-store availability never widens that result.

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

## The three picker states

Each selectable row has exactly one authority state:

| State | Meaning | Picker behavior | Runtime behavior |
| --- | --- | --- | --- |
| `allowed_selected` | The current Control Card permits it and the resident Card selects it. | Checked and editable. | Exposed. |
| `allowed_unselected` | The current Control Card permits it, but the resident Card does not select it. | Unchecked and editable. | Not exposed. |
| `not_allowed` | It appears in the live descriptive catalog but is outside the current Control authority, or the projection is unavailable. | Unchecked, disabled, and labeled **Not permitted**; expandable details remain readable. | Not exposed. |

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
and exact app/agent resource. The resident agent Card id is deterministic for
the grantor and `kdcube-agent:<app>:<agent>` caller profile. Neither id includes
the selected capability set.

On first bootstrap, KDCube creates or resolves both Cards, links the Control
Card to the resident Card, and starts the resident selection empty. A
pre-Control-Card installation is the one compatibility exception: if a legacy
PostgreSQL deny map exists, it is converted once into the equivalent positive
selection so an existing user's choices survive migration. PostgreSQL does
not remain capability authority after that bootstrap.

Descriptor reconciliation and user selection are independent revisions:

| Change | Result |
| --- | --- |
| Descriptor removes a capability | The Control Card revision removes it; the next live intersection denies it without changing the resident selection or credential. |
| Descriptor adds a capability | It appears as `allowed_unselected`; no existing resident Card gains it. The user must select it explicitly. |
| User changes a visible selection | `agent_selection_update` replaces the visible positive selection on the resident Card. |
| Labels or other metadata change | The picker can show the new presentation without treating it as authority. |

The remint rule depends on the authorization representation. A static or
embedded credential grant never grows because a descriptor/catalog grew; it
needs an explicit new grant and, for snapshot credentials, re-consent or
re-minting. This hosted-agent capability relationship is pointer-backed: the
credential continues to identify the resident Card, while the current Control
Card and resident selection are resolved live. Revising descriptor authority
therefore does not remint that credential. A newly offered capability still
stays unselected until the user chooses it.

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

- `agent_capabilities` reads the descriptive catalog, preference choices, and
  current Card projection, then annotates every row with its authority state.
- `agent_selection_update` saves a capability draft as a positive resident
  Card selection. The same request may update model, instruction,
  presentation, and cache preferences in PostgreSQL; those fields are not
  Card authority.

The picker body (`useCapabilityPickerBody` in
`@kdcube/components-react/chat`) is rendered in the composer popover, its
expanded modal, and the served `capabilities` widget. The shells share one
draft and save only on **Save changes**. Closing and reopening retains the
draft for the active chat; changing conversations drops unsaved UI state. A
conversation id scopes model/instruction/presentation/cache preferences. The
resident Card capability selection is the user's live selection for that
app/agent and is not duplicated into a conversation row.

## Preferences, consent, and service descriptions

Model, instruction, presentation, and cold-cache-policy choices remain typed
preferences in `user_bundle_props`; see the
[User Settings Solution](user-settings-solution-README.md). A preference-store
failure falls back to configured preference defaults while Card capability
authority remains closed to its effective projection.

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
