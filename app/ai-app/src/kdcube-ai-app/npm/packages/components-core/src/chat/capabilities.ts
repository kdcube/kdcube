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
}

export interface AgentCapabilityToolGroup {
  alias: string
  name: string
  kind: string
  /** Locked-on platform groups (io/context). Never user-toggleable. */
  system: boolean
  tools: AgentCapabilityToolEntry[]
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

export interface AgentCapabilityNamespace {
  namespace: string
  alias: string
  operations: string[]
  tools: string[]
}

export interface AgentCapabilitySkill {
  id: string
  name: string
  description: string
  when_to_use: string[]
  namespace: string
}

export interface AgentCapabilitiesInventory {
  agent: string
  tools: AgentCapabilityToolGroup[]
  mcp: AgentCapabilityMcpServer[]
  named_services: AgentCapabilityNamespace[]
  skills: AgentCapabilitySkill[]
}

/** The saved deny-list. Absent key/entry = enabled (full configured set). */
export interface AgentSelectionDisabled {
  tools?: Record<string, true | string[]>
  mcp?: Record<string, true | string[]>
  named_services?: Record<string, true>
  skills?: string[]
}

/** A partial toggle patch (what one interaction changes). Dict categories take
 *  per-key toggles: `true`/non-empty name list disables, `false` re-enables;
 *  `skills` is a per-id boolean map. Keys absent from the patch keep state. */
export interface AgentSelectionPatch {
  tools?: Record<string, boolean | string[]>
  mcp?: Record<string, boolean | string[]>
  named_services?: Record<string, boolean>
  skills?: Record<string, boolean>
}

export type AgentCapabilitiesLoadStatus = 'idle' | 'loading' | 'ready' | 'error'

/** The `capabilities` branch of the chat state. */
export interface AgentCapabilitiesState {
  status: AgentCapabilitiesLoadStatus
  error: string | null
  agent: string | null
  inventory: AgentCapabilitiesInventory | null
  disabled: AgentSelectionDisabled
  saving: boolean
  saveError: string | null
}

export const initialCapabilitiesState: AgentCapabilitiesState = {
  status: 'idle',
  error: null,
  agent: null,
  inventory: null,
  disabled: {},
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
  return out
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

export function isSkillDisabled(disabled: AgentSelectionDisabled, skillId: string): boolean {
  return Boolean(disabled.skills?.includes(skillId))
}
