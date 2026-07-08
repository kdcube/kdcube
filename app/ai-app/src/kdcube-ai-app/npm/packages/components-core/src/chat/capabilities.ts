/**
 * Per-user agent capabilities — the composer-menu read model + selection logic.
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
}

/** The resolved realm behind a configured namespace: what's inside and which
 *  provider claims it runs on (sourced from the realm's own declaration). */
export interface AgentCapabilityRealm {
  label?: string
  description?: string
  /** The realm's purpose sentence in user terms. */
  about?: string
  /** The service's third-party dependency, stated plainly. */
  third_party?: string
  /** Object kinds the service works in (name + one-liner). */
  objects?: { name: string; description?: string }[]
  operations?: AgentCapabilityRealmEntry[]
  actions?: AgentCapabilityRealmEntry[]
  connected_accounts?: {
    provider_id: string
    connector_app_id?: string
    claims: string[]
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

/** An admin-allowed model row (mirrors the economics price-table naming). */
export interface AgentSupportedModel {
  model: string
  provider: string
  label: string
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
}

/** The saved deny-list. Absent key/entry = enabled (full configured set). */
export interface AgentSelectionDisabled {
  tools?: Record<string, true | string[]>
  mcp?: Record<string, true | string[]>
  /** `true` denies the whole namespace; a key list denies individual
   *  operations (`object.search`) / named actions (`object.action.send`). */
  named_services?: Record<string, true | string[]>
  skills?: string[]
}

/** A partial toggle patch (what one interaction changes). Dict categories take
 *  per-key toggles: `true`/non-empty name list disables, `false` re-enables;
 *  `skills` is a per-id boolean map. Keys absent from the patch keep state. */
export interface AgentSelectionPatch {
  tools?: Record<string, boolean | string[]>
  mcp?: Record<string, boolean | string[]>
  named_services?: Record<string, boolean | string[]>
  skills?: Record<string, boolean>
  /** The single model PICK: a `{provider, model}` sets it, `null` clears back
   *  to the configured default; omitted keeps the stored pick. */
  model?: AgentModelPick | null
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
  /** Effective cold-cache policy (user-held over admin default) + bounds. */
  cachePolicy: AgentCachePolicy | null
  /** A deferred change awaiting its trigger (badged in the menu). */
  pending: AgentSelectionPending | null
  saving: boolean
  saveError: string | null
}

export const initialCapabilitiesState: AgentCapabilitiesState = {
  status: 'idle',
  error: null,
  agent: null,
  inventory: null,
  disabled: {},
  model: null,
  cachePolicy: null,
  pending: null,
  saving: false,
  saveError: null,
}

// ── pure selection logic ─────────────────────────────────────────────────────

const DICT_CATEGORIES = ['tools', 'mcp', 'named_services'] as const

/** Apply a toggle patch to a deny-list — the client mirror of the server's
 *  merge-write, used for optimistic updates while the debounced save is queued. */
export function applySelectionPatch(
  disabled: AgentSelectionDisabled,
  patch: AgentSelectionPatch,
): AgentSelectionDisabled {
  const out: AgentSelectionDisabled = {
    ...(disabled.tools ? { tools: { ...disabled.tools } } : {}),
    ...(disabled.mcp ? { mcp: { ...disabled.mcp } } : {}),
    ...(disabled.named_services ? { named_services: { ...disabled.named_services } } : {}),
    ...(disabled.skills ? { skills: [...disabled.skills] } : {}),
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
  return out
}

/** Merge two toggle patches (later wins per key) so debounced saves send one
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
  if (next.model !== undefined) out.model = next.model
  else if (base.model !== undefined) out.model = base.model
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

export function isSkillDisabled(disabled: AgentSelectionDisabled, skillId: string): boolean {
  return Boolean(disabled.skills?.includes(skillId))
}
