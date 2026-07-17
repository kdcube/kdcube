/**
 * Conversation-scoped agent capabilities — the composer-menu read model and
 * explicit-save draft logic.
 *
 * The server owns the truth: `agent_capabilities` returns the agent's configured
 * inventory (python tool groups with per-tool docs, MCP servers, named-service
 * namespaces, expanded skills) plus the caller's saved selection (a DENY-LIST:
 * what the user turned off; empty = the full configured set). `agent_selection_update`
 * merge-writes partial toggles and clamps them against the live inventory.
 *
 * This module holds the wire types and the PURE selection logic the engine and
 * the menu share: applying a toggle patch to the local deny-list (the client
 * mirror of the server's merge semantics) and computing the patch a row toggle
 * produces. Toggles apply from the next message — the backend reads the saved
 * selection per turn, so there is no session invalidation.
 */

// ── wire types (agent_capabilities payload) ──────────────────────────────────

export interface AgentCapabilityToolEntry {
  name: string
  description: string
  /** Per-tool consent state (dotted claim policies). */
  consent?: AgentCapabilityConsent
}

/** READ-ONLY connected-account consent state for one pickable entry: the
 *  tool's declared claims against the user's connected accounts, computed
 *  server-side at catalog time (a menu render asks nothing). */
export interface AgentCapabilityConsent {
  provider_id: string
  connector_app_id?: string
  claims: string[]
  unmet: string[]
  covered: boolean
}

export interface AgentCapabilityToolGroup {
  alias: string
  name: string
  kind: string
  /** Locked-on platform groups (io/context). Never user-toggleable. */
  system: boolean
  tools: AgentCapabilityToolEntry[]
  /** Group-level consent state (bare-alias claim policies). */
  consent?: AgentCapabilityConsent
}

export interface AgentCapabilityMcpServer {
  server_id: string
  alias: string
  name: string
  /** The configured allow-list as-is (may be `["*"]`). */
  tools: string[]
  /** Concrete per-tool entries when knowable (configured names, or the
   *  runtime's cached listing). Present => per-tool toggles; absent => the
   *  server-level toggle only. */
  tool_entries?: AgentCapabilityToolEntry[]
}

/** One operation or named action inside a namespace's realm view. */
export interface AgentCapabilityRealmEntry {
  name: string
  /** Human title from the realm's own presentation ("Send email"); the
   *  grammar token (`name`) demotes to a hint when this is present. */
  label?: string
  description?: string
  /** Third-party line in human words: "via your connected Google account ·
   *  send mail" — declared provider/claim labels, never UI-invented. */
  via?: string
  /** Provider claims this entry needs — present only when the realm
   *  declared per-operation differentiation (e.g. mail read vs send). */
  claims?: string[]
  /** `false` = the realm advertises this entry but the agent's admin config
   *  excludes it (`namespaces.<ns>.allowed`). Rendered present-but-disabled
   *  — absence becomes information. Absent = enabled. */
  enabled_for_agent?: boolean
  /** Declared reason for an INTENTIONAL exclusion
   *  (`namespaces.<ns>.excluded.<op>.reason`): the capability rides another
   *  path by design ("Reading rides the context tools — the agent pulls refs
   *  directly"). Renders in place of the generic admin line. */
  excluded_note?: string
}

/** The resolved realm behind a configured namespace: what's inside and which
 *  provider claims it runs on (sourced from the realm's own declaration). */
export interface AgentCapabilityRealm {
  label?: string
  description?: string
  /** The realm's purpose sentence in user terms. */
  about?: string
  /** The works-with line, stated plainly: a third-party dependency, or what
   *  an internal realm operates on. Absent = the card renders no line. */
  third_party?: string
  /** Object kinds the service works in (name + one-liner). */
  objects?: { name: string; description?: string }[]
  operations?: AgentCapabilityRealmEntry[]
  actions?: AgentCapabilityRealmEntry[]
  /** Optional per-group heading overrides (read/write/actions) a realm may
   *  declare through its presentation. Absent = the neutral defaults. */
  group_labels?: { read?: string; write?: string; actions?: string }
  connected_accounts?: {
    provider_id: string
    connector_app_id?: string
    claims: string[]
  }[]
  /** Declared access requirements — the realm's own statement of what a user
   *  needs BEFORE chatting (internal access, memberships). One declaration
   *  feeds this proactive card row AND the reactive denial card. `surface`
   *  is only ever an honest affordance; absent = the description names who
   *  can grant it. `status` renders when the platform can cheaply resolve
   *  it; absent = shown as a declared requirement without a state chip. */
  requirements?: {
    id: string
    label?: string
    description: string
    actor?: string
    status?: 'granted' | 'missing'
    /** `target_surface` (when the host declares one) makes the affordance
     *  summon a real scene window; `url` is the absolute new-tab fallback. */
    surface?: { kind: string; url?: string; label?: string; target_surface?: string; ui_event?: Record<string, unknown> }
  }[]
}

export interface AgentCapabilityNamespace {
  namespace: string
  alias: string
  operations: string[]
  tools: string[]
  /** Namespace-level consent state (the realm's connected-account claims). */
  consent?: AgentCapabilityConsent
  /** Resolved realm view (label/description, operations, named actions). */
  realm?: AgentCapabilityRealm
}

export interface AgentCapabilitySkill {
  id: string
  name: string
  description: string
  when_to_use: string[]
  namespace: string
}

/** The sub-agents (subagent delegation) inventory entry. Present when the
 *  admin offered the ability (`react.agents.<id>.subagents: true`) — absent
 *  means the ability is outside the inventory and no row renders. The server
 *  owns the picker copy (`label` + `description`, the quality-vs-spend
 *  trade-off); the UI renders it verbatim. */
export interface AgentCapabilitySubagents {
  available: boolean
  label?: string
  description?: string
  /** The toggle's initial state when the user has NO stored preference: `true`
   *  (default) = on unless the user opts out; `false` = offered but off until
   *  the user opts in (an admin-set default-off ability). */
  default_on?: boolean
}

/** Whether the agent CONSUMES the two mid-turn conversation affordances: a
 *  followup the running turn folds in at a decision boundary, and a steer
 *  (cancel + finalize). A ReAct agent consumes both; a run-to-completion
 *  (ported) agent consumes neither — the platform promotes an unconsumed
 *  followup to the NEXT turn instead. The server declares this per agent via
 *  the capabilities provider. Absent from the inventory = UNKNOWN: the composer
 *  keeps today's behavior (both enabled). Only an explicit `false` gates an
 *  affordance off. */
export interface AgentConversationCaps {
  accepts_followup: boolean
  accepts_steer: boolean
}

/** An admin-allowed model row (mirrors the economics price-table naming). */
export interface AgentSupportedModel {
  model: string
  provider: string
  label: string
}

/** One admin-declared instruction-set option (id-based; resolution is the
 *  agent runtime's concern — the wire never carries instruction content). */
export interface AgentInstructionOption {
  id: string
  label: string
  description?: string
}

/** The declared instruction-profile inventory; absent = section hidden. */
export interface AgentInstructionProfiles {
  options: AgentInstructionOption[]
  default?: string | null
}

/** The user's single model pick (applies to the strong decision role). */
export interface AgentModelPick {
  provider: string
  model: string
}

/** Cold-cache selection-change policy values. The user pays for the cache, so
 *  the user holds the policy; admin config supplies default + allowed set. */
export type AgentSelectionChangePolicy = 'accept' | 'confirm' | 'defer_cold' | 'defer_conversation'

export interface AgentCachePolicy {
  effective: {
    model_switch: AgentSelectionChangePolicy
    capability_toggle: AgentSelectionChangePolicy
  }
  allowed: AgentSelectionChangePolicy[]
  default: {
    model_switch: AgentSelectionChangePolicy
    capability_toggle: AgentSelectionChangePolicy
  }
}

/** A deferred selection change awaiting its trigger. */
export interface AgentSelectionPending {
  disabled?: AgentSelectionPatch
  model?: AgentModelPick | null
  instructions?: string | null
  apply: 'next_conversation' | 'when_cold'
  since_conversation_id?: string
  created_at?: string
}

/** How one selection write lands: immediately, or parked until a trigger. */
export type AgentSelectionApplyMode = 'now' | 'next_conversation' | 'when_cold'

export interface AgentCapabilitiesInventory {
  agent: string
  tools: AgentCapabilityToolGroup[]
  mcp: AgentCapabilityMcpServer[]
  named_services: AgentCapabilityNamespace[]
  skills: AgentCapabilitySkill[]
  /** Admin-allowed model list; empty/absent keeps the model choice invisible. */
  supported_models?: AgentSupportedModel[]
  /** The configured default for the strong decision role (what runs with no pick). */
  default_model?: AgentModelPick | null
  /** Helper-agents entry; `null`/absent = the ability is not offered. */
  subagents?: AgentCapabilitySubagents | null
  /** Mid-turn affordance declaration (followup / steer). Absent = unknown, so
   *  the composer keeps today's behavior (both enabled) — see
   *  `agentAcceptsFollowup` / `agentAcceptsSteer`. */
  conversation?: AgentConversationCaps | null
  /** Admin-declared instruction-set options; absent = the section is hidden. */
  instruction_profiles?: AgentInstructionProfiles | null
}

/** The saved deny-list. Absent key/entry = enabled (full configured set). */
export interface AgentSelectionDisabled {
  tools?: Record<string, true | string[]>
  mcp?: Record<string, true | string[]>
  /** `true` denies the whole namespace; a key list denies individual
   *  operations (`object.search`) / named actions (`object.action.send`). */
  named_services?: Record<string, true | string[]>
  skills?: string[]
  /** The user's explicit sub-agent delegation preference: `true` = opted out
   *  (off), `false` = opted IN (on). Absent = no preference — the inventory's
   *  `default_on` decides the rendered state. Both booleans are stored (an
   *  explicit `false` is what enables an admin default-off ability). */
  subagents?: boolean
}

/** A partial toggle patch (what one interaction changes). Dict categories take
 *  per-key toggles: `true`/non-empty name list disables, `false` re-enables;
 *  `skills` is a per-id boolean map. Keys absent from the patch keep state. */
export interface AgentSelectionPatch {
  tools?: Record<string, boolean | string[]>
  mcp?: Record<string, boolean | string[]>
  named_services?: Record<string, boolean | string[]>
  skills?: Record<string, boolean>
  /** Sub-agents toggle: the explicit preference the user is writing — `true`
   *  opts out (off), `false` opts in (on); omitted keeps state. Both booleans
   *  are stored (an explicit `false` is a real opt-in, not a clear). Rides the
   *  selection map like the other categories. */
  subagents?: boolean
  /** The single model PICK: a `{provider, model}` sets it, `null` clears back
   *  to the configured default; omitted keeps the stored pick. */
  model?: AgentModelPick | null
  /** The single instruction-profile PICK (a declared option id): a string
   *  sets it, `null` clears back to the declared default; omitted keeps the
   *  stored pick. */
  instructions?: string | null
}

export type AgentCapabilitiesLoadStatus = 'idle' | 'loading' | 'ready' | 'error'

/** The `capabilities` branch of the chat state. */
export interface AgentCapabilitiesState {
  status: AgentCapabilitiesLoadStatus
  error: string | null
  agent: string | null
  inventory: AgentCapabilitiesInventory | null
  disabled: AgentSelectionDisabled
  /** The user's model pick; null = the configured default runs. */
  model: AgentModelPick | null
  /** The user's instruction-profile pick; null = the declared default runs. */
  instructions: string | null
  /** Effective cold-cache policy (user-held over admin default) + bounds. */
  cachePolicy: AgentCachePolicy | null
  /** A deferred change awaiting its trigger (badged in the menu). */
  pending: AgentSelectionPending | null
  /** Local selection differs from the persisted conversation record. */
  dirty: boolean
  saving: boolean
  saveError: string | null
}

export const initialCapabilitiesState: AgentCapabilitiesState = {
  status: 'idle',
  error: null,
  agent: null,
  inventory: null,
  disabled: {},
  instructions: null,
  model: null,
  cachePolicy: null,
  pending: null,
  dirty: false,
  saving: false,
  saveError: null,
}

// ── pure selection logic ─────────────────────────────────────────────────────

const DICT_CATEGORIES = ['tools', 'mcp', 'named_services'] as const

/** Apply a toggle patch to a deny-list — the client mirror of the server's
 *  merge-write, used for the explicit-save draft. */
export function applySelectionPatch(
  disabled: AgentSelectionDisabled,
  patch: AgentSelectionPatch,
): AgentSelectionDisabled {
  const out: AgentSelectionDisabled = {
    ...(disabled.tools ? { tools: { ...disabled.tools } } : {}),
    ...(disabled.mcp ? { mcp: { ...disabled.mcp } } : {}),
    ...(disabled.named_services ? { named_services: { ...disabled.named_services } } : {}),
    ...(disabled.skills ? { skills: [...disabled.skills] } : {}),
    ...(disabled.subagents !== undefined ? { subagents: disabled.subagents } : {}),
  }
  for (const category of DICT_CATEGORIES) {
    const raw = patch[category]
    if (!raw) continue
    const target: Record<string, true | string[]> = { ...(out[category] as Record<string, true | string[]> | undefined) }
    for (const [key, value] of Object.entries(raw)) {
      if (value === true) {
        target[key] = true
      } else if (Array.isArray(value) && value.length > 0) {
        target[key] = value.map(String)
      } else {
        delete target[key]
      }
    }
    if (Object.keys(target).length > 0) out[category] = target as never
    else delete out[category]
  }
  if (patch.skills) {
    const skills = new Set(out.skills ?? [])
    for (const [skillId, value] of Object.entries(patch.skills)) {
      if (value) skills.add(skillId)
      else skills.delete(skillId)
    }
    if (skills.size > 0) out.skills = [...skills]
    else delete out.skills
  }
  if (patch.subagents !== undefined) {
    // Store the explicit preference both ways — a `false` is an opt-IN, not a
    // clear (it's what activates an admin default-off ability).
    out.subagents = patch.subagents
  }
  return out
}

/** Merge two toggle patches (later wins per key) so explicit save sends one
 *  request carrying only what changed. */
export function mergeSelectionPatches(
  base: AgentSelectionPatch,
  next: AgentSelectionPatch,
): AgentSelectionPatch {
  const out: AgentSelectionPatch = {}
  for (const category of DICT_CATEGORIES) {
    const merged = { ...(base[category] ?? {}), ...(next[category] ?? {}) }
    if (Object.keys(merged).length > 0) out[category] = merged as never
  }
  const skills = { ...(base.skills ?? {}), ...(next.skills ?? {}) }
  if (Object.keys(skills).length > 0) out.skills = skills
  if (next.subagents !== undefined) out.subagents = next.subagents
  else if (base.subagents !== undefined) out.subagents = base.subagents
  if (next.model !== undefined) out.model = next.model
  else if (base.model !== undefined) out.model = base.model
  if (next.instructions !== undefined) out.instructions = next.instructions
  else if (base.instructions !== undefined) out.instructions = base.instructions
  return out
}

/** True when `pick` selects `row` (model id; provider when both carry one). */
export function isModelPicked(pick: AgentModelPick | null, row: AgentSupportedModel): boolean {
  if (!pick) return false
  if (pick.model !== row.model) return false
  return !pick.provider || !row.provider || pick.provider === row.provider
}

export function isToolDisabled(disabled: AgentSelectionDisabled, alias: string, name: string): boolean {
  const entry = disabled.tools?.[alias]
  if (entry === true) return true
  return Array.isArray(entry) && entry.includes(name)
}

export type ToolGroupToggleState = 'on' | 'off' | 'partial'

export function toolGroupState(
  group: AgentCapabilityToolGroup,
  disabled: AgentSelectionDisabled,
): ToolGroupToggleState {
  if (group.system) return 'on'
  const entry = disabled.tools?.[group.alias]
  if (!entry) return 'on'
  if (entry === true) return 'off'
  const names = group.tools.map((tool) => tool.name)
  const offCount = names.filter((name) => entry.includes(name)).length
  if (offCount === 0) return 'on'
  return names.length > 0 && offCount >= names.length ? 'off' : 'partial'
}

/** The patch a master group toggle produces: fully on → disable the group;
 *  off/partial → re-enable everything under it. */
export function toolGroupTogglePatch(
  group: AgentCapabilityToolGroup,
  disabled: AgentSelectionDisabled,
): AgentSelectionPatch {
  return { tools: { [group.alias]: toolGroupState(group, disabled) === 'on' } }
}

/** The patch toggling ONE tool inside a group produces. Collapses to the group
 *  form when the result covers everything (`true`) or nothing (`false`), so the
 *  stored record stays minimal. */
export function toolTogglePatch(
  group: AgentCapabilityToolGroup,
  disabled: AgentSelectionDisabled,
  toolName: string,
): AgentSelectionPatch {
  const names = group.tools.map((tool) => tool.name)
  const entry = disabled.tools?.[group.alias]
  let nextOff: string[]
  if (entry === true) {
    // Whole group was off; turning one tool back on leaves the rest off.
    nextOff = names.filter((name) => name !== toolName)
  } else {
    const current = Array.isArray(entry) ? entry.filter((name) => names.includes(name)) : []
    nextOff = current.includes(toolName)
      ? current.filter((name) => name !== toolName)
      : [...current, toolName]
  }
  if (nextOff.length === 0) return { tools: { [group.alias]: false } }
  if (names.length > 0 && names.every((name) => nextOff.includes(name))) {
    return { tools: { [group.alias]: true } }
  }
  return { tools: { [group.alias]: nextOff } }
}

export function isMcpServerDisabled(disabled: AgentSelectionDisabled, serverId: string): boolean {
  return disabled.mcp?.[serverId] === true
}

export function isMcpToolDisabled(disabled: AgentSelectionDisabled, serverId: string, name: string): boolean {
  const entry = disabled.mcp?.[serverId]
  if (entry === true) return true
  return Array.isArray(entry) && entry.includes(name)
}

function mcpEntryNames(server: AgentCapabilityMcpServer): string[] {
  return (server.tool_entries ?? []).map((tool) => tool.name)
}

/** Tri-state like a python tool group; servers without known tool entries are
 *  simply on/off. */
export function mcpServerState(
  server: AgentCapabilityMcpServer,
  disabled: AgentSelectionDisabled,
): ToolGroupToggleState {
  const entry = disabled.mcp?.[server.server_id]
  if (!entry) return 'on'
  if (entry === true) return 'off'
  const names = mcpEntryNames(server)
  const offCount = names.filter((name) => entry.includes(name)).length
  if (offCount === 0) return 'on'
  return names.length > 0 && offCount >= names.length ? 'off' : 'partial'
}

export function mcpServerTogglePatch(
  server: AgentCapabilityMcpServer,
  disabled: AgentSelectionDisabled,
): AgentSelectionPatch {
  return { mcp: { [server.server_id]: mcpServerState(server, disabled) === 'on' } }
}

/** Toggle ONE MCP tool. Collapses to the whole-server form when the result
 *  covers everything (`true`) or nothing (`false`). Only meaningful when the
 *  server has known `tool_entries` (the clamp rejects name-lists otherwise). */
export function mcpToolTogglePatch(
  server: AgentCapabilityMcpServer,
  disabled: AgentSelectionDisabled,
  toolName: string,
): AgentSelectionPatch {
  const names = mcpEntryNames(server)
  const entry = disabled.mcp?.[server.server_id]
  let nextOff: string[]
  if (entry === true) {
    nextOff = names.filter((name) => name !== toolName)
  } else {
    const current = Array.isArray(entry) ? entry.filter((name) => names.includes(name)) : []
    nextOff = current.includes(toolName)
      ? current.filter((name) => name !== toolName)
      : [...current, toolName]
  }
  if (nextOff.length === 0) return { mcp: { [server.server_id]: false } }
  if (names.length > 0 && names.every((name) => nextOff.includes(name))) {
    return { mcp: { [server.server_id]: true } }
  }
  return { mcp: { [server.server_id]: nextOff } }
}

export function isNamespaceDisabled(disabled: AgentSelectionDisabled, namespace: string): boolean {
  return disabled.named_services?.[namespace] === true
}

/** The deny key for one namespace internals entry: operations use their own
 *  token; a named action rides `object.action.<name>` (the grammar it runs
 *  through — denying the action blocks that action name specifically). */
export function namespaceEntryKey(kind: 'operation' | 'action', name: string): string {
  return kind === 'action' ? `object.action.${name}` : name
}

export function isNamespaceEntryDisabled(
  disabled: AgentSelectionDisabled,
  namespace: string,
  entryKey: string,
): boolean {
  const entry = disabled.named_services?.[namespace]
  if (entry === true) return true
  return Array.isArray(entry) && entry.includes(entryKey)
}

/** Namespace row state over its internals: `off` when the whole realm is
 *  denied (or every entry is), `partial` when some entries are. */
export function namespaceState(
  namespace: string,
  entryKeys: string[],
  disabled: AgentSelectionDisabled,
): ToolGroupToggleState {
  const entry = disabled.named_services?.[namespace]
  if (!entry) return 'on'
  if (entry === true) return 'off'
  const offCount = entryKeys.filter((key) => entry.includes(key)).length
  if (offCount === 0) return 'on'
  return entryKeys.length > 0 && offCount >= entryKeys.length ? 'off' : 'partial'
}

/** The patch a namespace master toggle produces: fully on → deny the whole
 *  realm; off/partial → re-enable everything under it. */
export function namespaceTogglePatch(
  namespace: string,
  entryKeys: string[],
  disabled: AgentSelectionDisabled,
): AgentSelectionPatch {
  return { named_services: { [namespace]: namespaceState(namespace, entryKeys, disabled) === 'on' } }
}

/** The patch toggling ONE operation/action inside a namespace. Collapses to
 *  the namespace form when the result covers everything (`true`) or nothing
 *  (`false`), so the stored record stays minimal. */
export function namespaceEntryTogglePatch(
  namespace: string,
  entryKeys: string[],
  disabled: AgentSelectionDisabled,
  entryKey: string,
): AgentSelectionPatch {
  const entry = disabled.named_services?.[namespace]
  let nextOff: string[]
  if (entry === true) {
    // The whole realm was off; turning one entry back on leaves the rest off.
    nextOff = entryKeys.filter((key) => key !== entryKey)
  } else {
    const current = Array.isArray(entry) ? entry.filter((key) => entryKeys.includes(key)) : []
    nextOff = current.includes(entryKey)
      ? current.filter((key) => key !== entryKey)
      : [...current, entryKey]
  }
  if (nextOff.length === 0) return { named_services: { [namespace]: false } }
  if (entryKeys.length > 0 && entryKeys.every((key) => nextOff.includes(key))) {
    return { named_services: { [namespace]: true } }
  }
  return { named_services: { [namespace]: nextOff } }
}

// ── service-card grant-style groups (read / write / actions) ─────────────────

/** The three human capability groups a service card presents — the same
 *  read/write/action shape the delegated consent window uses. Grammar tokens
 *  (`object.upsert`) demote to an expandable "details" under each group. */
export type RealmGroupId = 'read' | 'write' | 'actions'

/** Default human group headings. A realm may override per-group copy via its
 *  presentation; these are the neutral fallbacks. */
export const REALM_GROUP_LABELS: Record<RealmGroupId, string> = {
  read: 'Read',
  write: 'Create & update',
  actions: 'Actions',
}

export const REALM_GROUP_ORDER: RealmGroupId[] = ['read', 'write', 'actions']

/** Canonical operation → group classification. Named actions always fall in
 *  the actions group; operation tokens map by their verb, with a write-leaning
 *  heuristic fallback so an undeclared mutating op never reads as "read". */
const REALM_OPERATION_GROUP: Record<string, RealmGroupId> = {
  'provider.about': 'read',
  'provider.capabilities': 'read',
  'object.list': 'read',
  'object.search': 'read',
  'object.get': 'read',
  'object.schema': 'read',
  'object.upsert': 'write',
  'object.host_file': 'write',
  'object.delete': 'write',
  'object.action': 'actions',
}

export function classifyRealmEntry(kind: 'operation' | 'action', name: string): RealmGroupId {
  if (kind === 'action') return 'actions'
  const token = String(name || '').trim().toLowerCase()
  const mapped = REALM_OPERATION_GROUP[token]
  if (mapped) return mapped
  if (/(delete|remove|upsert|write|create|update|host_file|host|send|post|upload|archive|publish)/.test(token)) {
    return 'write'
  }
  return 'read'
}

export interface RealmEntryLike {
  name: string
  label?: string
  description?: string
  via?: string
  claims?: string[]
  enabled_for_agent?: boolean
  /** Declared reason for an intentional exclusion (see
   *  `AgentCapabilityRealmEntry.excluded_note`). */
  excluded_note?: string
}

export interface RealmGroupView {
  id: RealmGroupId
  label: string
  /** Human summary line (member titles joined) — no grammar tokens. */
  summary: string
  /** The toggleable entries in this group (excluded ones are not here). */
  entries: { item: RealmEntryLike; key: string }[]
  /** Deny keys for this group (subset of the namespace's entry keys). */
  keys: string[]
}

/** Humanize a member title without leaking the grammar token. Shared by the
 *  picker for realm entries, tool rows, and MCP tool rows — every child row
 *  titles itself in words; the token demotes to a mono hint. */
export function humanizeEntryTitle(item: RealmEntryLike): string {
  const label = String(item.label || '').trim()
  if (label) return label
  const token = String(item.name || '').trim()
  const tail = token.split('.').pop() || token
  return tail.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Split a realm's operations + actions into the read/write/actions groups
 *  (toggleable) plus the advertised-but-excluded entries (collapsed line). */
export function buildRealmGroups(
  realm: { operations?: RealmEntryLike[]; actions?: RealmEntryLike[]; group_labels?: Partial<Record<RealmGroupId, string>> } | null | undefined,
): { groups: RealmGroupView[]; excluded: RealmEntryLike[] } {
  const buckets: Record<RealmGroupId, { item: RealmEntryLike; key: string }[]> = {
    read: [], write: [], actions: [],
  }
  const excluded: RealmEntryLike[] = []
  const push = (kind: 'operation' | 'action', item: RealmEntryLike) => {
    if (item.enabled_for_agent === false) {
      excluded.push(item)
      return
    }
    const key = namespaceEntryKey(kind, item.name)
    buckets[classifyRealmEntry(kind, item.name)].push({ item, key })
  }
  for (const op of realm?.operations ?? []) push('operation', op)
  for (const action of realm?.actions ?? []) push('action', action)
  const groups: RealmGroupView[] = []
  for (const id of REALM_GROUP_ORDER) {
    const entries = buckets[id]
    if (!entries.length) continue
    const summary = entries.map(({ item }) => humanizeEntryTitle(item)).filter(Boolean).join(' · ')
    groups.push({
      id,
      label: realm?.group_labels?.[id] || REALM_GROUP_LABELS[id],
      summary: summary || `${entries.length} operation${entries.length === 1 ? '' : 's'}`,
      entries,
      keys: entries.map(({ key }) => key),
    })
  }
  return { groups, excluded }
}

/** The patch a GROUP master toggle produces: on → deny every group key;
 *  off/partial → re-enable every group key. Collapses to the whole-namespace
 *  form when the result covers everything (`true`) or nothing (`false`). */
export function namespaceGroupTogglePatch(
  namespace: string,
  entryKeys: string[],
  groupKeys: string[],
  disabled: AgentSelectionDisabled,
): AgentSelectionPatch {
  const entry = disabled.named_services?.[namespace]
  const currentOff = new Set<string>(
    entry === true
      ? entryKeys
      : Array.isArray(entry)
        ? entry.filter((key) => entryKeys.includes(key))
        : [],
  )
  const groupOn = namespaceState(namespace, groupKeys, disabled) === 'on'
  for (const key of groupKeys) {
    if (groupOn) currentOff.add(key)
    else currentOff.delete(key)
  }
  const nextOff = entryKeys.filter((key) => currentOff.has(key))
  if (nextOff.length === 0) return { named_services: { [namespace]: false } }
  if (entryKeys.length > 0 && entryKeys.every((key) => nextOff.includes(key))) {
    return { named_services: { [namespace]: true } }
  }
  return { named_services: { [namespace]: nextOff } }
}

export function isSkillDisabled(disabled: AgentSelectionDisabled, skillId: string): boolean {
  return Boolean(disabled.skills?.includes(skillId))
}

/** The effective OFF state of the sub-agents toggle. Tri-state: an explicit
 *  stored preference wins (`true` = off, `false` = on); with no stored
 *  preference the inventory's `default_on` decides — so an admin default-off
 *  ability renders off until the user opts in. */
export function isSubagentsDisabled(
  disabled: AgentSelectionDisabled,
  defaultOn: boolean = true,
): boolean {
  if (disabled.subagents !== undefined) return disabled.subagents === true
  return !defaultOn
}

/** The patch the sub-agents row toggle produces — the explicit boolean the
 *  user is now choosing: turning it OFF writes `true`, turning it ON writes
 *  `false` (an explicit opt-in). `defaultOn` seeds the current state when the
 *  user has no stored preference yet. */
export function subagentsTogglePatch(
  disabled: AgentSelectionDisabled,
  defaultOn: boolean = true,
): AgentSelectionPatch {
  return { subagents: !isSubagentsDisabled(disabled, defaultOn) }
}

// ── mid-turn conversation affordances (followup / steer) ─────────────────────

/** Whether the composer should present a mid-turn message as JOINING the running
 *  turn (a followup the agent folds in). Backward-compat: an absent inventory or
 *  an absent `conversation` block reads as UNKNOWN → today's behavior (`true`).
 *  Only an explicit `accepts_followup: false` gates it off — the composer then
 *  presents the message as queued for the NEXT turn (never silently dropped). */
export function agentAcceptsFollowup(
  inventory: Pick<AgentCapabilitiesInventory, 'conversation'> | null | undefined,
): boolean {
  const conv = inventory?.conversation
  if (!conv) return true
  return conv.accepts_followup !== false
}

/** Whether the composer should offer the steer (cancel + finalize) affordance.
 *  Backward-compat identical to `agentAcceptsFollowup`: absent = `true`; only an
 *  explicit `accepts_steer: false` hides/disables the steer control. */
export function agentAcceptsSteer(
  inventory: Pick<AgentCapabilitiesInventory, 'conversation'> | null | undefined,
): boolean {
  const conv = inventory?.conversation
  if (!conv) return true
  return conv.accepts_steer !== false
}

/** Which presentation a spotlight request should open the capability picker
 *  in. The deep-linked user needs the READABLE form when the target is a
 *  namespace entry (service cards carry prose) or a long target list; a
 *  short dedicated-tool spotlight stays in the compact popover. */
export function preferredMenuPresentation(
  spotlightTargets: string[] | undefined,
  inventory: Pick<AgentCapabilitiesInventory, 'named_services'> | null | undefined,
): 'popover' | 'modal' {
  const targets = (spotlightTargets ?? []).map((item) => String(item || '').trim()).filter(Boolean)
  if (!targets.length) return 'popover'
  if (targets.length > 3) return 'modal'
  const namespaceTokens = new Set(
    (inventory?.named_services ?? []).flatMap((entry) => [entry.namespace, entry.alias]).filter(Boolean),
  )
  return targets.some((target) => namespaceTokens.has(target)) ? 'modal' : 'popover'
}
