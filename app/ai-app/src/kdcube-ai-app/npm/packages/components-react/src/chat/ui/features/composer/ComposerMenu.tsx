/**
 * ComposerMenu — the composer "+" menu: conversation capability settings.
 *
 * A registered user narrows which of the agent's CONFIGURED tools and skills it
 * uses. The inventory + saved selection lazy-load on first open
 * (`agent_capabilities`); row toggles update a local draft and the explicit
 * Save changes command sends one scoped `agent_selection_update` merge-write.
 *
 * Structure is a SECTIONS REGISTRY: ordered descriptors, each rendering its own
 * rows. The four capability sections (skills / tool groups / MCP servers /
 * named-service namespaces) ship built-in; hosts extend the menu by passing
 * more descriptors (`extraSections`) — a future connectors entry is just a new
 * descriptor, no menu changes.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { agentGrantConsentOpen, chatActions, consentOpenForClaims, openCapabilitiesOnHost, openSurfaceOnHost } from '@kdcube/components-core/chat'
import type { AgentCapabilityConsent, ConnectionsConsentOpen } from '@kdcube/components-core/chat'
import { useAppDispatch } from '../../support/hooks.ts'
import type {
  AgentCapabilitiesInventory,
  AgentSelectionDisabled,
  AgentSelectionPatch,
  AgentSelectionPending,
  NamespaceStyleMap,
  RealmGroupView,
} from '@kdcube/components-core/chat'
import {
  buildRealmGroups,
  humanizeEntryTitle,
  isMcpToolDisabled,
  isModelPicked,
  mergeSelectionPatches,
  isNamespaceEntryDisabled,
  isSkillDisabled,
  isSubagentsDisabled,
  isToolDisabled,
  namespaceEntryTogglePatch,
  namespaceGroupTogglePatch,
  namespaceState,
  namespaceTogglePatch,
  preferredMenuPresentation,
  mcpServerState,
  mcpServerTogglePatch,
  mcpToolTogglePatch,
  subagentsTogglePatch,
  toolGroupState,
  toolGroupTogglePatch,
  toolTogglePatch,
} from '@kdcube/components-core/chat'
import { namespaceStyleForKey } from '@kdcube/components-core'
import { CanvasExpandButton } from '../../components/CanvasModal.tsx'
import { useChatViewModel } from '../../context.tsx'
import type { ChatViewModel } from '../../viewModel.ts'

export interface ComposerMenuSectionContext {
  vm: ChatViewModel
  close: () => void
}

/** Cost notice shown when the user picks a DIFFERENT model: provider prompt
 *  caches are per model, so the next turn rebuilds the cache — said here in
 *  user words (time + money), never cache mechanics. Exported so hosts and
 *  tests reference the shipped copy. */
export const MODEL_SWITCH_CACHE_NOTICE =
  'Switching the model makes the next reply re-read the whole conversation, so it takes a little longer and costs more.'

/** Milder cost notice for the first tool/skill toggle in one menu-open: the
 *  tool catalog renders inside the cached prompt slice. */
export const CAPABILITY_TOGGLE_CACHE_NOTICE =
  'Changing tools or skills makes the next reply re-read part of the conversation, which adds a small one-time cost.'

/** The confirm picker's choices — the decision moment IS the policy picker. */
export const CONFIRM_APPLY_NOW = 'Apply now'
export const CONFIRM_APPLY_NEXT_CONVERSATION = 'Apply from next conversation'
export const CONFIRM_APPLY_WHEN_COLD = 'Apply later, when it costs nothing extra'
export const CONFIRM_REMEMBER = 'Remember my choice'
export const PENDING_NEXT_CONVERSATION_NOTICE = 'Your change is saved — it applies from your next conversation.'
export const PENDING_WHEN_COLD_NOTICE = 'Your change is saved — it applies at the next moment it costs nothing extra.'

/** One menu section. Ordered ascending by `order`; each renders its own rows
 *  (or null to stay hidden). Extension point: new capability surfaces slot in
 *  as descriptors without touching the menu shell. */
export interface ComposerMenuSectionDescriptor {
  id: string
  order?: number
  render: (ctx: ComposerMenuSectionContext) => ReactNode
}

function CheckIcon({ state = 'on' }: { state?: 'on' | 'partial' }) {
  return (
    <svg className="k-menu-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {state === 'partial' ? <path d="M6 12h12" /> : <path d="M4.5 12.5l5 5 10-11" />}
    </svg>
  )
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ transform: open ? 'rotate(90deg)' : undefined, transition: 'transform 120ms ease' }}>
      <path d="M9 6l6 6-6 6" />
    </svg>
  )
}

function MenuRow({
  label,
  sub,
  hint,
  checked,
  onToggle,
  expandable,
  expanded,
  onExpand,
  child = false,
  spotlight = false,
  aside,
}: {
  label: ReactNode
  sub?: string
  /** Tooltip-only detail (config identifiers and the like) — never rendered
   *  in the default view. `sub` wins as the tooltip when both are set. */
  hint?: string
  checked: 'on' | 'off' | 'partial'
  onToggle: () => void
  expandable?: boolean
  expanded?: boolean
  onExpand?: () => void
  child?: boolean
  spotlight?: boolean
  /** Trailing affordance beside the toggle (e.g. the consent state/button). */
  aside?: ReactNode
}) {
  return (
    <div className={`k-menu-row ${child ? 'k-menu-row-child' : ''}${spotlight ? ' k-menu-row-spotlight' : ''}`}>
      <button
        type="button"
        role="menuitemcheckbox"
        aria-checked={checked === 'on' ? 'true' : checked === 'partial' ? 'mixed' : 'false'}
        className="k-menu-row-main"
        title={sub || hint || undefined}
        onClick={onToggle}
      >
        <span className="k-menu-row-text">
          <span className="k-menu-row-label">{label}</span>
          {sub ? <span className="k-menu-row-sub">{sub}</span> : null}
        </span>
        <span className="k-menu-row-state">{checked === 'off' ? null : <CheckIcon state={checked === 'partial' ? 'partial' : 'on'} />}</span>
      </button>
      {aside}
      {expandable ? (
        <button
          type="button"
          className="k-menu-expand"
          aria-label={expanded ? `Collapse ${typeof label === 'string' ? label : 'group'}` : `Expand ${typeof label === 'string' ? label : 'group'}`}
          aria-expanded={expanded}
          onClick={onExpand}
        >
          <ChevronIcon open={Boolean(expanded)} />
        </button>
      ) : null}
    </div>
  )
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <div className="k-menu-title">{children}</div>
}

/** The inline confirm picker: mechanism text + the three apply choices +
 *  "remember my choice". Non-blocking (part of the menu, no modal); the
 *  defer choices render only when the admin-allowed set contains them. */
function ConfirmPicker({
  text,
  allowed,
  remember,
  onRemember,
  onDecide,
}: {
  text: string
  allowed: string[]
  remember: boolean
  onRemember: (value: boolean) => void
  onDecide: (apply: 'now' | 'next_conversation' | 'when_cold') => void
}) {
  return (
    <div className="k-menu-confirm" role="group" aria-label="Apply this change">
      <div className="k-menu-confirm-text">{text}</div>
      <div className="k-menu-confirm-actions">
        <button type="button" className="k-btn k-sm k-primary" onClick={() => onDecide('now')}>
          {CONFIRM_APPLY_NOW}
        </button>
        {allowed.includes('defer_conversation') ? (
          <button type="button" className="k-btn k-sm" onClick={() => onDecide('next_conversation')}>
            {CONFIRM_APPLY_NEXT_CONVERSATION}
          </button>
        ) : null}
        {allowed.includes('defer_cold') ? (
          <button type="button" className="k-btn k-sm" onClick={() => onDecide('when_cold')}>
            {CONFIRM_APPLY_WHEN_COLD}
          </button>
        ) : null}
      </div>
      <label className="k-menu-confirm-remember">
        <input type="checkbox" checked={remember} onChange={(event) => onRemember(event.target.checked)} />
        {CONFIRM_REMEMBER}
      </label>
    </div>
  )
}

function PendingTag() {
  return <span className="k-menu-tag">pending</span>
}

/** Compact per-row consent state: covered rows show a quiet tag; rows with
 *  unmet claims get a consent button that opens the hub seeded with exactly
 *  what to approve. Two families, one affordance: connected-account claims
 *  ("connected" / consent plan) and a per-agent delegated grant ("granted" /
 *  the pending agent-grant pane, one click). */
function ConsentAside({
  consent,
  onConsent,
  label = 'Consent',
  title,
}: {
  consent?: AgentCapabilityConsent
  onConsent?: (open: ConnectionsConsentOpen) => void
  label?: string
  title?: string
}) {
  if (!consent || !consent.claims?.length) return null
  const agentGrant = Boolean(consent.agent_client_id)
  if (consent.covered) {
    return (
      <span
        className="k-menu-tag k-menu-tag-ok"
        title={agentGrant
          // The tag covers ONE leg: what the user granted THIS AGENT. Tools
          // that act on the user's connected accounts also run under those
          // accounts' own consents (Delegated to KDCube), asked per claim at
          // the moment a call needs them.
          ? `You granted this agent: ${consent.claims.join(', ')}. Tools that act on your connected accounts also use those accounts' own consents, asked when a call needs them.`
          : `Account access granted: ${consent.claims.join(', ')}`}
      >
        {agentGrant ? 'granted to agent' : 'connected'}
      </span>
    )
  }
  if (!onConsent) {
    return (
      <span
        className="k-menu-tag k-menu-tag-consent"
        title={`Needs ${agentGrant ? 'your grant' : 'account access'}: ${consent.unmet.join(', ')}`}
      >
        needs consent
      </span>
    )
  }
  if (agentGrant) {
    return (
      <button
        type="button"
        className="k-menu-consent"
        title={title || `Grant this agent access: ${consent.unmet.join(', ')}`}
        onClick={() => onConsent(agentGrantConsentOpen({
          agentClientId: consent.agent_client_id || '',
          resource: consent.resource || '',
          claims: consent.unmet,
        }))}
      >
        {label === 'Consent' ? 'Grant' : label}
      </button>
    )
  }
  return (
    <button
      type="button"
      className="k-menu-consent"
      title={title || `Approve account access: ${consent.unmet.join(', ')}`}
      onClick={() => onConsent(consentOpenForClaims({
        providerId: consent.provider_id || '',
        connectorAppId: consent.connector_app_id,
        claims: consent.unmet,
      }))}
    >
      {label}
    </button>
  )
}

/** Union of a group's unmet claims — the group-level "consent all" ask, the
 *  legitimate union spot because the user explicitly chose the whole set. */
function groupConsentUnion(group: { consent?: AgentCapabilityConsent; tools: { consent?: AgentCapabilityConsent }[] }): AgentCapabilityConsent | undefined {
  const states = [group.consent, ...group.tools.map((tool) => tool.consent)].filter(
    (state): state is AgentCapabilityConsent => Boolean(state && state.claims?.length),
  )
  if (!states.length) return undefined
  const claims: string[] = []
  const unmet: string[] = []
  for (const state of states) {
    for (const claim of state.claims) if (!claims.includes(claim)) claims.push(claim)
    for (const claim of state.unmet) if (!unmet.includes(claim)) unmet.push(claim)
  }
  return {
    provider_id: states[0].provider_id,
    connector_app_id: states[0].connector_app_id,
    claims,
    unmet,
    covered: unmet.length === 0,
  }
}

function firstLine(text: string): string {
  return String(text || '').split('\n')[0].trim()
}

/** Every child row titles itself in words (the declared label, else the
 *  humanized token); the raw token demotes to a small mono hint — tokens are
 *  never titles, even inside expanded details. */
function EntryTitle({ name, label }: { name: string; label?: string }) {
  return (
    <>
      {humanizeEntryTitle({ name, label })}
      <code className="k-menu-entry-token">{name}</code>
    </>
  )
}

function namespaceLabel(namespace: string, styles: NamespaceStyleMap): string {
  const style = namespaceStyleForKey(namespace, styles)
  const label = (style?.label || namespace).trim()
  return label ? label.charAt(0).toUpperCase() + label.slice(1) : namespace
}

interface CapabilityRowsProps {
  inventory: AgentCapabilitiesInventory
  disabled: AgentSelectionDisabled
  toggle: (patch: AgentSelectionPatch) => void
  namespaceStyles: NamespaceStyleMap
  pending?: AgentSelectionPending | null
  /** Tool names to highlight + scroll to (`alias.tool`, or a bare group
   *  alias). Set when the consent banner opens the menu to turn tools off. */
  spotlight?: string[]
  /** Opens the Connection Hub consent plan seeded with the given claims —
   *  the picker's proactive consent affordance. Present only when the host
   *  routes `open-connections`. */
  onConsent?: (consent: ConnectionsConsentOpen) => void
}

/** Radio-style single model pick from the admin-allowed `supported_models`
 *  list. The active row is the user's pick, else the configured default (which
 *  carries a "default" tag); choosing the default row clears the pick. Hidden
 *  entirely when the admin declared no list. */
function ModelsSection({ vm }: ComposerMenuSectionContext) {
  const { inventory, model: pick, toggle, pending } = vm.capabilities
  const supported = inventory?.supported_models ?? []
  if (!supported.length) return null
  const defaultModel = inventory?.default_model ?? null
  const pendingModel = pending && pending.model !== undefined
  return (
    <div>
      <SectionTitle>Model</SectionTitle>
      {supported.map((row) => {
        const isDefaultRow = Boolean(
          defaultModel
          && defaultModel.model === row.model
          && (!defaultModel.provider || !row.provider || defaultModel.provider === row.provider),
        )
        const active = pick ? isModelPicked(pick, row) : isDefaultRow
        return (
          <MenuRow
            key={`${row.provider}:${row.model}`}
            label={
              <>
                {row.label}
                {isDefaultRow ? <span className="k-menu-tag">default</span> : null}
                {pendingModel && pending?.model && pending.model.model === row.model ? <PendingTag /> : null}
              </>
            }
            hint={`${row.provider} · ${row.model}`}
            checked={active ? 'on' : 'off'}
            onToggle={() => {
              if (active) return
              toggle({
                model: isDefaultRow ? null : { provider: row.provider, model: row.model },
              })
            }}
          />
        )
      })}
    </div>
  )
}

/** Radio-style single instruction-set pick from the admin-declared
 *  `instruction_profiles` options (ids only — resolution is the agent's
 *  runtime concern). The active row is the user's pick, else the declared
 *  default (tagged); choosing the default row clears the pick. Hidden when
 *  the agent declares no profiles. */
function InstructionsSection({ vm }: ComposerMenuSectionContext) {
  const { inventory, instructions: pick, toggle, pending } = vm.capabilities
  const profiles = inventory?.instruction_profiles ?? null
  const options = profiles?.options ?? []
  if (!options.length) return null
  const defaultId = profiles?.default ?? null
  const pendingInstructions = pending && pending.instructions !== undefined
  return (
    <div>
      <SectionTitle>Instructions</SectionTitle>
      {options.map((row) => {
        const isDefaultRow = Boolean(defaultId && defaultId === row.id)
        const active = pick ? pick === row.id : isDefaultRow
        return (
          <MenuRow
            key={row.id}
            label={
              <>
                {row.label}
                {isDefaultRow ? <span className="k-menu-tag">default</span> : null}
                {pendingInstructions && pending?.instructions === row.id ? <PendingTag /> : null}
              </>
            }
            sub={firstLine(row.description ?? '')}
            checked={active ? 'on' : 'off'}
            onToggle={() => {
              if (active) return
              toggle({ instructions: isDefaultRow ? null : row.id })
            }}
          />
        )
      })}
    </div>
  )
}

/** Human copy for the presentation facets. Unknown facets render their raw
 *  name/value — the server may declare facets this build predates. */
const PRESENTATION_FACET_COPY: Record<string, { label: string; values: Record<string, string> }> = {
  tool_catalog: {
    label: 'Tool catalog',
    values: {
      full: 'Full descriptions with examples',
      compact: 'Exact tool ids and parameters, short form',
    },
  },
  skills_form: {
    label: 'Skill instructions',
    values: {
      full: 'Complete skill guides',
      compact: 'Condensed skill guides',
    },
  },
}

/** Radio-style value pick per declared presentation facet (how prompt
 *  surfaces render — decoupled from WHICH instruction set is picked). The
 *  active value is the user's pick, else the admin default (tagged);
 *  choosing the default clears that facet's pick. Hidden when the agent
 *  declares no facets. */
function PresentationSection({ vm }: ComposerMenuSectionContext) {
  const { inventory, presentation: picks, toggle, pending } = vm.capabilities
  const facets = inventory?.presentation_facets ?? null
  const names = facets ? Object.keys(facets) : []
  if (!names.length) return null
  const pendingPresentation = (pending && pending.presentation) || null
  return (
    <div>
      <SectionTitle>Presentation</SectionTitle>
      {names.map((facet) => {
        const block = facets![facet]
        const copy = PRESENTATION_FACET_COPY[facet]
        const picked = picks?.[facet] ?? null
        return (
          <div key={facet}>
            <div className="k-menu-subtitle">{copy?.label ?? facet}</div>
            {(block.options ?? []).map((value) => {
              const isDefaultValue = block.default === value
              const active = picked ? picked === value : isDefaultValue
              return (
                <MenuRow
                  key={`${facet}:${value}`}
                  label={
                    <>
                      {copy?.values?.[value] ?? value}
                      {isDefaultValue ? <span className="k-menu-tag">default</span> : null}
                      {pendingPresentation?.[facet] === value ? <PendingTag /> : null}
                    </>
                  }
                  checked={active ? 'on' : 'off'}
                  onToggle={() => {
                    if (active) return
                    toggle({ presentation: { [facet]: value } })
                  }}
                />
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

function SkillsSection({ inventory, disabled, toggle }: CapabilityRowsProps) {
  if (!inventory.skills.length) return null
  return (
    <div>
      <SectionTitle>Skills</SectionTitle>
      {inventory.skills.map((skill) => (
        <MenuRow
          key={skill.id}
          label={skill.name}
          sub={firstLine(skill.description)}
          checked={isSkillDisabled(disabled, skill.id) ? 'off' : 'on'}
          onToggle={() => toggle({ skills: { [skill.id]: !isSkillDisabled(disabled, skill.id) } })}
        />
      ))}
    </div>
  )
}

function ToolGroupsSection({ inventory, disabled, toggle, pending, spotlight, onConsent }: CapabilityRowsProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const groups = inventory.tools.filter((group) => !group.system)
  const containerRef = useRef<HTMLDivElement | null>(null)
  // `alias.tool` highlights one tool row (auto-expanding its group); a bare
  // alias highlights the whole group row.
  const spotlightKey = (spotlight ?? []).join('|')
  const spotlightMap = useMemo(() => {
    const map = new Map<string, Set<string> | 'group'>()
    for (const raw of spotlight ?? []) {
      const name = String(raw || '').trim()
      if (!name) continue
      const dot = name.indexOf('.')
      if (dot < 0) {
        map.set(name, 'group')
        continue
      }
      const alias = name.slice(0, dot)
      const tool = name.slice(dot + 1)
      const entry = map.get(alias)
      if (entry === 'group') continue
      if (entry instanceof Set) entry.add(tool)
      else map.set(alias, new Set([tool]))
    }
    return map
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spotlightKey])
  useEffect(() => {
    if (!spotlightMap.size) return
    setExpanded((current) => {
      const next = { ...current }
      spotlightMap.forEach((entry, alias) => {
        if (entry !== 'group') next[alias] = true
      })
      return next
    })
    const timer = window.setTimeout(() => {
      containerRef.current?.querySelector('.k-menu-row-spotlight')?.scrollIntoView({ block: 'nearest' })
    }, 60)
    return () => window.clearTimeout(timer)
  }, [spotlightMap])
  if (!groups.length) return null
  const pendingTools = pending?.disabled?.tools ?? {}
  const groupSpotlit = (alias: string) => spotlightMap.get(alias) === 'group'
  const toolSpotlit = (alias: string, toolName: string) => {
    const entry = spotlightMap.get(alias)
    return entry instanceof Set && entry.has(toolName)
  }
  return (
    <div ref={containerRef}>
      <SectionTitle>Tools</SectionTitle>
      {groups.map((group) => {
        const state = toolGroupState(group, disabled)
        const isOpen = Boolean(expanded[group.alias])
        return (
          <div key={group.alias}>
            <MenuRow
              label={
                <>
                  {group.name || group.alias}
                  {group.alias in pendingTools ? <PendingTag /> : null}
                </>
              }
              checked={state}
              onToggle={() => toggle(toolGroupTogglePatch(group, disabled))}
              expandable={group.tools.length > 0}
              expanded={isOpen}
              onExpand={() => setExpanded((current) => ({ ...current, [group.alias]: !isOpen }))}
              spotlight={groupSpotlit(group.alias)}
              aside={(
                <ConsentAside
                  consent={groupConsentUnion(group)}
                  onConsent={onConsent}
                  title={`Approve ${group.name || group.alias} account access`}
                />
              )}
            />
            {isOpen
              ? group.tools.map((tool) => (
                  <MenuRow
                    key={tool.name}
                    child
                    label={<EntryTitle name={tool.name} />}
                    sub={firstLine(tool.description)}
                    checked={isToolDisabled(disabled, group.alias, tool.name) ? 'off' : 'on'}
                    onToggle={() => toggle(toolTogglePatch(group, disabled, tool.name))}
                    spotlight={toolSpotlit(group.alias, tool.name)}
                    aside={<ConsentAside consent={tool.consent} onConsent={onConsent} />}
                  />
                ))
              : null}
          </div>
        )
      })}
    </div>
  )
}

function McpSection({ inventory, disabled, toggle, onConsent }: CapabilityRowsProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  if (!inventory.mcp.length) return null
  return (
    <div>
      <SectionTitle>Extensions</SectionTitle>
      {inventory.mcp.map((server) => {
        const entries = server.tool_entries ?? []
        const isOpen = Boolean(expanded[server.server_id])
        return (
          <div key={server.server_id}>
            <MenuRow
              label={server.name || server.server_id}
              checked={mcpServerState(server, disabled)}
              onToggle={() => toggle(mcpServerTogglePatch(server, disabled))}
              expandable={entries.length > 0}
              expanded={isOpen}
              onExpand={() => setExpanded((current) => ({ ...current, [server.server_id]: !isOpen }))}
              aside={<ConsentAside consent={server.consent} onConsent={onConsent} />}
            />
            {isOpen
              ? entries.map((tool) => (
                  <MenuRow
                    key={tool.name}
                    child
                    label={<EntryTitle name={tool.name} />}
                    sub={firstLine(tool.description)}
                    checked={isMcpToolDisabled(disabled, server.server_id, tool.name) ? 'off' : 'on'}
                    onToggle={() => toggle(mcpToolTogglePatch(server, disabled, tool.name))}
                  />
                ))
              : null}
          </div>
        )
      })}
    </div>
  )
}

/** One line inside an expanded namespace: an operation or a named action —
 *  TOGGLEABLE like a tool row (per-entry deny, routed through the same
 *  selection flow), with the flat consent chip family on claims-bearing
 *  entries. Per-entry consent seeds the plan with exactly that entry's
 *  claims; entries without declared claims lean on the namespace chip. */
function RealmEntryRow({
  namespace,
  entryKeys,
  entryKey,
  entry,
  disabled,
  toggle,
  consent,
  onConsent,
}: {
  namespace: string
  entryKeys: string[]
  entryKey: string
  entry: { name: string; label?: string; description?: string; via?: string; claims?: string[] }
  disabled: AgentSelectionDisabled
  toggle: (patch: AgentSelectionPatch) => void
  consent?: AgentCapabilityConsent
  onConsent?: (open: ConnectionsConsentOpen) => void
}) {
  const claims = (entry.claims ?? []).filter(Boolean)
  let aside: ReactNode = null
  if (claims.length && consent) {
    const unmet = claims.filter((claim) => (consent.unmet ?? []).includes(claim))
    aside = (
      <ConsentAside
        consent={{
          provider_id: consent.provider_id,
          connector_app_id: consent.connector_app_id,
          claims,
          unmet,
          covered: unmet.length === 0,
        }}
        onConsent={onConsent}
        title={`Approve account access: ${unmet.join(', ')}`}
      />
    )
  }
  const sub = [entry.description, entry.via].filter(Boolean).join(' · ')
  return (
    <MenuRow
      child
      label={<EntryTitle name={entry.name} label={entry.label} />}
      sub={sub || undefined}
      checked={isNamespaceEntryDisabled(disabled, namespace, entryKey) ? 'off' : 'on'}
      onToggle={() => toggle(namespaceEntryTogglePatch(namespace, entryKeys, disabled, entryKey))}
      aside={aside}
    />
  )
}

/** A quiet one-liner inside the expanded service card (the third-party
 *  dependency, the object kinds, or the honest "hasn't described itself"
 *  state). Renders only declared text. */
function ServiceCardLine({ text, title }: { text: string; title?: string }) {
  return (
    <div className="k-menu-row k-menu-row-child">
      <span className="k-menu-row-static">
        <span className="k-menu-card-line" title={title || text}>{text}</span>
      </span>
    </div>
  )
}

/** A declared access requirement inside the expanded service card — the
 *  proactive twin of the denial card: what the agent needs BEFORE chatting.
 *  The text and the affordance sit IN-FLOW (the chip never overlaps the text):
 *  the description flexes and truncates, the affordance keeps its own space.
 *  The affordance prefers an on-scene summon when the host declared a target
 *  surface for the requirement's widget, and falls back to opening its
 *  absolute URL in a new tab. */
function RequirementLine({ requirement }: {
  requirement: {
    id: string
    label?: string
    description: string
    status?: string
    surface?: { kind: string; url?: string; label?: string; target_surface?: string; ui_event?: Record<string, unknown> }
  }
}) {
  const heading = requirement.label || requirement.id
  const surface = requirement.surface
  const url = surface?.kind === 'url' ? surface.url || '' : ''
  const targetSurface = String(surface?.target_surface || '').trim()
  const affordanceLabel = surface?.label || 'Open'
  const openFallback = () => {
    if (!url) return
    try {
      window.open(url, '_blank', 'noopener,noreferrer')
    } catch {
      /* pop-up blocked or standalone context — nothing else to do */
    }
  }
  const onActivate = () => {
    if (targetSurface) {
      void openSurfaceOnHost(targetSurface, surface?.ui_event ?? {}, { source: 'capabilities-requirement' })
        .then((acked) => {
          if (!acked) openFallback()
        })
      return
    }
    openFallback()
  }
  const hasAffordance = Boolean(targetSurface || url)
  const statusChip = requirement.status === 'granted'
    ? <span className="k-menu-tag k-menu-tag-ok">granted</span>
    : requirement.status === 'missing'
      ? <span className="k-menu-tag k-menu-tag-consent">missing</span>
      : null
  return (
    <div className="k-menu-row k-menu-row-child k-menu-requirement">
      <span className="k-menu-requirement-text" title={`${heading} — ${requirement.description}`}>
        <span className="k-menu-requirement-head">{heading}</span>
        <span className="k-menu-requirement-desc">{requirement.description}</span>
      </span>
      {/* A purely informational requirement stays one quiet sentence — the
          aside (and its flex gap) exists only when there is a state to show
          or a surface that genuinely changes access. */}
      {statusChip || hasAffordance ? (
        <span className="k-menu-requirement-aside">
          {statusChip}
          {hasAffordance ? (
            <button type="button" className="k-menu-consent" onClick={onActivate}>
              {affordanceLabel}
            </button>
          ) : null}
        </span>
      ) : null}
    </div>
  )
}

/** A namespace's toggleable internals: allowed operations by their own token,
 *  named actions as `object.action.<name>` (denying the action blocks that
 *  action name in the grammar's dispatch). */
type RealmEntryItem = {
  name: string
  label?: string
  description?: string
  via?: string
  claims?: string[]
  enabled_for_agent?: boolean
  excluded_note?: string
}

/** An advertised-but-excluded realm entry: present, greyed, honest. No
 *  toggle, no consent chip — nothing clickable a user cannot act on. A
 *  DECLARED exclusion renders its own reason ("Reading rides the context
 *  tools — the agent pulls refs directly") — the capability is served, by
 *  design, through another path. The admin fix path is NOT restated here:
 *  it lives once, on the summary line's tooltip. */
function ExcludedEntryRow({ entry }: { entry: RealmEntryItem }) {
  const note = String(entry.excluded_note || '').trim()
  const sub = [entry.description, note].filter(Boolean).join(' · ')
  return (
    <div className="k-menu-row k-menu-row-child k-menu-row-excluded">
      <span className="k-menu-row-static">
        <span className="k-menu-row-text">
          <span className="k-menu-row-label">
            <EntryTitle name={entry.name} label={entry.label} />
          </span>
          {sub ? <span className="k-menu-row-sub">{sub}</span> : null}
        </span>
      </span>
    </div>
  )
}

/** A capability GROUP (Read / Create & update / Actions) inside a service
 *  card: the group heading + a human summary of what it covers, toggleable as
 *  ONE unit. The grammar entries demote to an expandable "Details" the curious
 *  user opens — no tokens until then. */
function RealmGroupRow({
  namespace,
  entryKeys,
  group,
  disabled,
  toggle,
  consent,
  onConsent,
}: {
  namespace: string
  entryKeys: string[]
  group: RealmGroupView
  disabled: AgentSelectionDisabled
  toggle: (patch: AgentSelectionPatch) => void
  consent?: AgentCapabilityConsent
  onConsent?: (open: ConnectionsConsentOpen) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="k-menu-group">
      <MenuRow
        child
        label={group.label}
        sub={group.summary}
        checked={namespaceState(namespace, group.keys, disabled)}
        onToggle={() => toggle(namespaceGroupTogglePatch(namespace, entryKeys, group.keys, disabled))}
        expandable={group.entries.length > 0}
        expanded={open}
        onExpand={() => setOpen((value) => !value)}
      />
      {open
        ? group.entries.map(({ item, key }) => (
            <RealmEntryRow
              key={key}
              namespace={namespace}
              entryKeys={entryKeys}
              entryKey={key}
              entry={item}
              disabled={disabled}
              toggle={toggle}
              consent={consent}
              onConsent={onConsent}
            />
          ))
        : null}
    </div>
  )
}

/** One quiet, expandable line standing in for the whole excluded wall: the
 *  greyed rows leave the default view; the count invites the curious in. */
function ExcludedSummary({ namespace, excluded }: { namespace: string; excluded: RealmEntryItem[] }) {
  const [open, setOpen] = useState(false)
  if (!excluded.length) return null
  const count = excluded.length
  // When EVERY excluded entry declares its reason, the collapsed line says the
  // work is covered elsewhere; otherwise the visible line stays neutral and
  // the admin fix path lives HERE, once, in the tooltip (never in the rows).
  const allDeclared = excluded.every((item) => String(item.excluded_note || '').trim())
  return (
    <div className="k-menu-excluded-summary">
      <button
        type="button"
        className="k-menu-row-static k-menu-excluded-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        title={allDeclared
          ? 'Covered through other tools — each row says which'
          : `An app admin can enable these for this agent (namespaces.${namespace}.allowed)`}
      >
        <span className="k-menu-card-line">
          {count} more operation{count === 1 ? '' : 's'} {allDeclared
            ? 'covered through other tools'
            : 'available to add'}
        </span>
        <span className="k-menu-excluded-chevron"><ChevronIcon open={open} /></span>
      </button>
      {open
        ? excluded.map((item) => (
            <ExcludedEntryRow key={item.name} entry={item} />
          ))
        : null}
    </div>
  )
}

function ServicesSection({ inventory, disabled, toggle, namespaceStyles, spotlight, onConsent }: CapabilityRowsProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  if (!inventory.named_services.length) return null
  // The consent banner's "turn off the tools" for a named-service tool names
  // the NAMESPACE — the entry the user sees here.
  const spotlit = new Set((spotlight ?? []).map((item) => String(item || '').trim()).filter(Boolean))
  return (
    <div>
      <SectionTitle>Services</SectionTitle>
      {inventory.named_services.map((entry) => {
        const realm = entry.realm
        const { groups, excluded } = buildRealmGroups(realm)
        const entryKeys = groups.flatMap((group) => group.keys)
        const isOpen = Boolean(expanded[entry.namespace])
        const objectsLine = (realm?.objects ?? [])
          .map((kind) => kind.name.split('.').pop() || kind.name)
          .filter(Boolean)
          .join(' · ')
        return (
          <div key={entry.namespace}>
            <MenuRow
              label={realm?.label || namespaceLabel(entry.namespace, namespaceStyles)}
              sub={realm?.about || realm?.description || undefined}
              checked={namespaceState(entry.namespace, entryKeys, disabled)}
              onToggle={() => toggle(namespaceTogglePatch(entry.namespace, entryKeys, disabled))}
              expandable
              expanded={isOpen}
              onExpand={() => setExpanded((current) => ({ ...current, [entry.namespace]: !isOpen }))}
              spotlight={spotlit.has(entry.namespace) || spotlit.has(entry.alias)}
              aside={(
                <ConsentAside
                  consent={entry.consent}
                  onConsent={onConsent}
                  title={`Approve ${realm?.label || entry.namespace} account access`}
                />
              )}
            />
            {isOpen && realm?.third_party ? <ServiceCardLine text={realm.third_party} /> : null}
            {isOpen
              ? (realm?.requirements ?? []).map((requirement) => (
                  <RequirementLine key={requirement.id} requirement={requirement} />
                ))
              : null}
            {isOpen && objectsLine ? (
              <ServiceCardLine
                text={`Objects: ${objectsLine}`}
                title={(realm?.objects ?? [])
                  .map((kind) => `${kind.name}${kind.description ? ` — ${kind.description}` : ''}`)
                  .join('\n')}
              />
            ) : null}
            {isOpen && !realm ? (
              <ServiceCardLine text="This service turns on and off as one." />
            ) : null}
            {isOpen
              ? groups.map((group) => (
                  <RealmGroupRow
                    key={group.id}
                    namespace={entry.namespace}
                    entryKeys={entryKeys}
                    group={group}
                    disabled={disabled}
                    toggle={toggle}
                    consent={entry.consent}
                    onConsent={onConsent}
                  />
                ))
              : null}
            {isOpen ? <ExcludedSummary namespace={entry.namespace} excluded={excluded} /> : null}
          </div>
        )
      })}
    </div>
  )
}

/** Sub-agents entry: ONE top-level toggle over subagent delegation, rendered
 *  only when the inventory carries the `subagents` entry (the admin offered the
 *  ability). Tri-state: the toggle's state with no stored preference follows the
 *  payload's `default_on` (an admin can offer it default-OFF); toggling writes
 *  the explicit boolean (opt-out `true` / opt-in `false`) through the same
 *  local-draft + explicit-save selection flow as every other category. Label and
 *  description come from the payload — the server owns the quality-vs-spend
 *  copy. */
function HelperAgentsSection({ inventory, disabled, toggle, pending }: CapabilityRowsProps) {
  const entry = inventory.subagents
  if (!entry?.available) return null
  const defaultOn = entry.default_on !== false
  const off = isSubagentsDisabled(disabled, defaultOn)
  const pendingSubagents = pending?.disabled?.subagents !== undefined
  return (
    <div>
      <MenuRow
        label={
          <>
            {entry.label || 'Sub-agents'}
            {pendingSubagents ? <PendingTag /> : null}
          </>
        }
        sub={entry.description || undefined}
        checked={off ? 'off' : 'on'}
        onToggle={() => toggle(subagentsTogglePatch(disabled, defaultOn))}
      />
    </div>
  )
}

/** Connection-Hub entry: an ACTION row (opens the host's connections surface),
 *  the first non-toggle descriptor proving the registry contract. Renders only
 *  when the host registered an `open-connections` handler. */
function ConnectorsSection({ vm, close }: ComposerMenuSectionContext) {
  if (!vm.connections.available()) return null
  return (
    <div>
      <SectionTitle>Connections</SectionTitle>
      <div className="k-menu-row">
        <button
          type="button"
          role="menuitem"
          className="k-menu-row-main"
          onClick={() => {
            vm.connections.open('composer-menu')
            close()
          }}
        >
          <span className="k-menu-row-text">
            <span className="k-menu-row-label">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ marginRight: 6, verticalAlign: '-2px', display: 'inline' }}>
                <path d="M12 22v-3M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z" />
              </svg>
              Manage connections…
            </span>
            <span className="k-menu-row-sub">Connected accounts for tools like Gmail and Slack</span>
          </span>
          <span className="k-menu-row-state">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M9 6l6 6-6 6" />
            </svg>
          </span>
        </button>
      </div>
    </div>
  )
}

function builtInSections(namespaceStyles: NamespaceStyleMap): ComposerMenuSectionDescriptor[] {
  const capabilitySection = (
    id: string,
    order: number,
    /* An empty group renders nothing at all: the descriptor returns null so the
     * picker body filters it out entirely — no header, no divider, no blank row.
     * The check lives HERE (not only inside the Section) because the body sees a
     * `<Section/>` element as truthy and can't tell it will render null. */
    hasItems: (inventory: AgentCapabilitiesInventory) => boolean,
    Section: (props: CapabilityRowsProps) => ReactNode,
  ): ComposerMenuSectionDescriptor => ({
    id,
    order,
    render: ({ vm, close }) => {
      const { inventory, disabled, toggle, pending } = vm.capabilities
      if (!inventory || !hasItems(inventory)) return null
      const onConsent = vm.connections.available()
        ? (consent: ConnectionsConsentOpen) => {
            vm.connections.open('composer-menu', consent)
            close()
          }
        : undefined
      return (
        <Section
          inventory={inventory}
          disabled={disabled}
          toggle={toggle}
          namespaceStyles={namespaceStyles}
          pending={pending}
          spotlight={vm.state.toolSpotlight?.tools}
          onConsent={onConsent}
        />
      )
    },
  })
  return [
    {
      id: 'model',
      order: 5,
      render: (ctx: ComposerMenuSectionContext) =>
        ctx.vm.capabilities.inventory?.supported_models?.length
          ? <ModelsSection {...ctx} />
          : null,
    },
    {
      id: 'instructions',
      order: 7,
      render: (ctx: ComposerMenuSectionContext) =>
        ctx.vm.capabilities.inventory?.instruction_profiles?.options?.length
          ? <InstructionsSection {...ctx} />
          : null,
    },
    {
      id: 'presentation',
      order: 8,
      render: (ctx: ComposerMenuSectionContext) =>
        Object.keys(ctx.vm.capabilities.inventory?.presentation_facets ?? {}).length
          ? <PresentationSection {...ctx} />
          : null,
    },
    capabilitySection('skills', 10, (inv) => inv.skills.length > 0, SkillsSection),
    capabilitySection('tools', 20, (inv) => inv.tools.some((group) => !group.system), ToolGroupsSection),
    capabilitySection('mcp', 30, (inv) => inv.mcp.length > 0, McpSection),
    capabilitySection('services', 40, (inv) => inv.named_services.length > 0, ServicesSection),
    capabilitySection('subagents', 45, (inv) => Boolean(inv.subagents?.available), HelperAgentsSection),
    {
      id: 'connectors',
      order: 50,
      render: (ctx) => (ctx.vm.connections.available() ? <ConnectorsSection {...ctx} /> : null),
    },
  ]
}

function modelKey(pick: { provider?: string; model?: string } | null | undefined): string {
  return pick?.model ? `${pick.provider ?? ''}:${pick.model}` : ''
}

/** The picker's interaction core, shared by every presentation (popover,
 *  in-chat modal, full-page widget): capabilities lifecycle, cache-cost
 *  notices, the confirm flow, and the section render. State lives in the
 *  CALLER's component (this is a hook), so switching shells mid-interaction
 *  keeps the confirm/notice state — one body, any shell.
 *  `active` gates load/reset (popover open state; a full page passes true). */
export function useCapabilityPickerBody({
  vm,
  namespaceStyles = {},
  extraSections = [],
  close,
  active,
  presentation = 'popover',
}: {
  vm: ChatViewModel
  namespaceStyles?: NamespaceStyleMap
  extraSections?: ComposerMenuSectionDescriptor[]
  close: () => void
  active: boolean
  presentation?: string
}): ReactNode {
  const capabilities = vm.capabilities

  /* Cache-cost notices. A fresh conversation has nothing cached yet, so the
   * notices would be noise there — suppressed via the already-exposed turn
   * list. Both reset per activation: the model notice tracks the pick that was
   * active when the picker opened (returning to it clears the notice); the
   * toggle notice shows once after the first capability toggle. */
  const conversationHasTurns = vm.state.turns.length > 0
  const openInitialModelRef = useRef<string | null>(null)
  const [toggledThisOpen, setToggledThisOpen] = useState(false)
  const [confirmState, setConfirmState] = useState<
    { klass: 'model_switch' | 'capability_toggle'; patch: AgentSelectionPatch } | null
  >(null)
  const [rememberChoice, setRememberChoice] = useState(false)

  /* Surfaced live: with the default `confirm` policy a row click routes to
   * this picker, which renders after ALL sections — outside the menu's
   * scrolled 420px viewport when the user is mid-list (exactly where the
   * spotlight put them). The row's check stays put by design until the
   * decision, so an off-screen picker made the click look dead. Bring the
   * question to the click — and re-anchor when the presentation switches. */
  const confirmRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!confirmState) return
    const timer = window.setTimeout(() => {
      confirmRef.current?.scrollIntoView({ block: 'nearest' })
    }, 30)
    return () => window.clearTimeout(timer)
  }, [confirmState, presentation])

  const capabilitiesRef = useRef(capabilities)
  capabilitiesRef.current = capabilities
  useEffect(() => {
    if (active) {
      // Opening the menu is the moment fresh consent/grant state matters — a
      // grant or revoke made in Connection Hub must show here. Re-fetch on the
      // open TRANSITION (ref keeps this out of the per-state-change loop),
      // except while local toggles are unsaved or saving — a reload would
      // clobber them; those opens keep the cached inventory.
      const caps = capabilitiesRef.current
      caps.load(caps.dirty || caps.saving ? {} : { force: true })
    }
    if (!active) {
      openInitialModelRef.current = null
      setToggledThisOpen(false)
      setConfirmState(null)
      setRememberChoice(false)
    }
  }, [active])

  useEffect(() => {
    if (active && capabilities.status === 'ready' && openInitialModelRef.current === null) {
      openInitialModelRef.current = modelKey(capabilities.model)
    }
  }, [active, capabilities.status, capabilities.model])

  /* Decision routing — the decision moment IS the policy picker. On a fresh
   * conversation (nothing cached) every change just applies. On a warm-ish
   * conversation the user's standing policy decides: accept applies with the
   * passive notice, defer_* writes the change as a pending delta, confirm
   * opens the inline choice (Apply now / next conversation / when cold). */
  const routeToggle = (patch: Parameters<typeof capabilities.toggle>[0]) => {
    const klass: 'model_switch' | 'capability_toggle' =
      patch.model !== undefined ? 'model_switch' : 'capability_toggle'
    const policy = capabilities.cachePolicy?.effective?.[klass]
    if (!conversationHasTurns || !policy || policy === 'accept') {
      if (klass === 'capability_toggle') setToggledThisOpen(true)
      capabilities.toggle(patch)
      return
    }
    if (policy === 'defer_conversation' || policy === 'defer_cold') {
      capabilities.decide(patch, {
        apply: policy === 'defer_cold' ? 'when_cold' : 'next_conversation',
      })
      return
    }
    // confirm
    setConfirmState((prev) => (
      prev
        ? { klass: prev.klass === 'model_switch' || klass === 'model_switch' ? 'model_switch' : 'capability_toggle', patch: mergeSelectionPatches(prev.patch, patch) }
        : { klass, patch }
    ))
  }
  const vmForSections: ChatViewModel = {
    ...vm,
    capabilities: { ...vm.capabilities, toggle: routeToggle },
  }

  const resolveConfirm = (apply: 'now' | 'next_conversation' | 'when_cold') => {
    if (!confirmState) return
    const rememberedPolicy = apply === 'now'
      ? 'accept'
      : apply === 'when_cold' ? 'defer_cold' : 'defer_conversation'
    capabilities.decide(confirmState.patch, {
      apply,
      ...(rememberChoice ? { cachePolicy: { [confirmState.klass]: rememberedPolicy } } : {}),
    })
    setConfirmState(null)
    setRememberChoice(false)
  }

  const allowedPolicies = capabilities.cachePolicy?.allowed ?? []
  const pending: AgentSelectionPending | null = capabilities.pending ?? null

  const modelNoticeVisible =
    conversationHasTurns
    && openInitialModelRef.current !== null
    && modelKey(capabilities.model) !== openInitialModelRef.current
  const toggleNoticeVisible = conversationHasTurns && toggledThisOpen

  const sections = useMemo(() => {
    return [...builtInSections(namespaceStyles), ...extraSections]
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
  }, [namespaceStyles, extraSections])

  if (capabilities.status === 'loading' || capabilities.status === 'idle') {
    return (
      <div className="k-menu-status">
        <span className="k-menu-spinner" aria-hidden="true" />
        Loading tools &amp; skills…
      </div>
    )
  }
  if (capabilities.status === 'error') {
    return (
      <button type="button" className="k-menu-status" onClick={() => capabilities.load({ force: true })}>
        Couldn&rsquo;t load tools &amp; skills. Tap to retry.
      </button>
    )
  }
  const rendered = sections
    .map((section) => ({ id: section.id, node: section.render({ vm: vmForSections, close }) }))
    .filter((section) => section.node !== null && section.node !== undefined && section.node !== false)
  /* The save control leads the body (sticky, so it never scrolls out of
   * view): the sections below can be taller than any shell's viewport, and a
   * save button only reachable by scrolling reads as "my change is done"
   * the moment the row shows its check. Dirty state additionally warns +
   * gently pulses the button so unsaved changes are unmissable. */
  const saveBar = (
    <div
      className={`k-menu-savebar${capabilities.dirty && !capabilities.saving ? ' is-dirty' : ''}${capabilities.saveError ? ' is-error' : ''}`}
    >
      <span className="k-menu-savebar-copy" role="status">
        {capabilities.saveError
          ? 'Changes couldn’t be saved.'
          : capabilities.saving
            ? 'Saving changes…'
            : capabilities.dirty
              ? 'Unsaved changes'
              : 'Saved changes apply from your next message.'}
      </span>
      <button
        type="button"
        className="k-btn k-sm k-primary"
        disabled={!capabilities.dirty || capabilities.saving || Boolean(confirmState)}
        onClick={() => capabilities.save()}
      >
        {capabilities.saving ? 'Saving…' : 'Save changes'}
      </button>
    </div>
  )
  return rendered.length ? (
    <>
      {saveBar}
      {rendered.map((section, index) => (
        <div key={section.id} data-picker-section={section.id}>
          {index > 0 ? <div className="k-menu-divider" role="separator" /> : null}
          {section.node}
          {section.id === 'model' && confirmState?.klass === 'model_switch' ? (
            <div ref={confirmRef}>
              <ConfirmPicker
                text={MODEL_SWITCH_CACHE_NOTICE}
                allowed={allowedPolicies}
                remember={rememberChoice}
                onRemember={setRememberChoice}
                onDecide={resolveConfirm}
              />
            </div>
          ) : null}
          {section.id === 'model' && !confirmState && modelNoticeVisible ? (
            <div className="k-menu-notice" role="note">{MODEL_SWITCH_CACHE_NOTICE}</div>
          ) : null}
        </div>
      ))}
      {confirmState?.klass === 'capability_toggle' ? (
        <div ref={confirmRef}>
          <ConfirmPicker
            text={CAPABILITY_TOGGLE_CACHE_NOTICE}
            allowed={allowedPolicies}
            remember={rememberChoice}
            onRemember={setRememberChoice}
            onDecide={resolveConfirm}
          />
        </div>
      ) : null}
      {!confirmState && toggleNoticeVisible ? (
        <div className="k-menu-notice" role="note">{CAPABILITY_TOGGLE_CACHE_NOTICE}</div>
      ) : null}
      {pending ? (
        <div className="k-menu-notice" role="note">
          {pending.apply === 'when_cold' ? PENDING_WHEN_COLD_NOTICE : PENDING_NEXT_CONVERSATION_NOTICE}
        </div>
      ) : null}
    </>
  ) : (
    <div className="k-menu-status">Everything this agent uses is always on.</div>
  )
}

/** Full-page presentation (the served capability widget): the SAME picker
 *  body inside a readable page column with the expanded wrap rules. */
export function CapabilityPickerPage({
  vm,
  namespaceStyles = {},
  extraSections = [],
  title = 'Capabilities',
  subtitle,
}: {
  vm: ChatViewModel
  namespaceStyles?: NamespaceStyleMap
  extraSections?: ComposerMenuSectionDescriptor[]
  title?: string
  subtitle?: string
}) {
  const body = useCapabilityPickerBody({
    vm,
    namespaceStyles,
    extraSections,
    close: () => {},
    active: true,
    presentation: 'page',
  })
  return (
    <div className="k-menu-page">
      <div className="k-menu-page-head">
        <div className="k-menu-page-title">{title}</div>
        {subtitle ? <div className="k-menu-page-sub">{subtitle}</div> : null}
      </div>
      <div className="k-menu-expanded" role="menu" aria-label="Tools and skills">
        {body}
      </div>
    </div>
  )
}

export function ComposerMenu({
  disabled = false,
  namespaceStyles = {},
  extraSections = [],
}: {
  disabled?: boolean
  namespaceStyles?: NamespaceStyleMap
  extraSections?: ComposerMenuSectionDescriptor[]
}) {
  const vm = useChatViewModel()
  const dispatch = useAppDispatch()
  const [open, setOpen] = useState(false)
  /* One picker, two in-chat presentations: the compact popover for quick
   * toggles and a wide in-widget modal where the service-card prose wraps
   * instead of ellipsizing. The SAME body node renders into whichever shell
   * is active; all interaction state (checkboxes via the store, spotlight,
   * the confirm picker) lives in useCapabilityPickerBody above the shells,
   * so switching mid-interaction keeps it. */
  const [view, setView] = useState<'popover' | 'modal'>('popover')
  const anchorRef = useRef<HTMLDivElement | null>(null)
  const capabilities = vm.capabilities

  /* A consent banner's "turn off the tools" option requests a spotlight:
   * open the menu; the tools section highlights + scrolls to the tools.
   * A namespace target (service card, long prose) or a long target list
   * opens the READABLE expanded form directly. Closing clears the request. */
  const spotlightNonce = vm.state.toolSpotlight?.nonce ?? 0
  useEffect(() => {
    if (!spotlightNonce) return
    const targets = vm.state.toolSpotlight?.tools
    const preferred = preferredMenuPresentation(targets, capabilities.inventory)
    if (preferred === 'modal') {
      // The readable form: a host that declared the `capabilities.open`
      // contract opens the picker as a real scene window (resizable,
      // dockable); its ack replaces the in-chat modal. No ack -> the modal.
      void openCapabilitiesOnHost(
        {
          spotlight_tools: targets,
          agent_id: vm.agentId,
          conversation_id: vm.state.conversationId ?? undefined,
        },
        { source: 'chat-spotlight' },
      ).then((acked) => {
        if (acked) {
          dispatch(chatActions.clearToolSpotlight())
          return
        }
        setView('modal')
        setOpen(true)
      })
      return
    }
    setView(preferred)
    setOpen(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spotlightNonce])
  const wasOpenRef = useRef(false)
  useEffect(() => {
    if (open) {
      wasOpenRef.current = true
      return
    }
    if (wasOpenRef.current) {
      wasOpenRef.current = false
      dispatch(chatActions.clearToolSpotlight())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    if (!open) setView('popover')
  }, [open])

  useEffect(() => {
    if (!open) return
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open])

  const close = () => setOpen(false)

  /* Choices here are PER AGENT, so every shell names the agent it configures
   * (the inventory's agent id, else the widget's configured one). */
  const agentName = vm.capabilities.inventory?.agent || vm.agentId || ''
  const agentScopeLine = agentName
    ? `what the ${agentName} agent may use for you`
    : 'what this agent may use for you'

  const body = useCapabilityPickerBody({
    vm,
    namespaceStyles,
    extraSections,
    close,
    active: open,
    presentation: view,
  })

  // Registered users only; the ops behind the menu require an authenticated caller.
  if (!vm.authed) return null

  return (
    <div ref={anchorRef} className="k-composer-menu-anchor">
      <button
        type="button"
        className={`k-iconbtn ${open ? 'k-iconbtn-active' : ''}`}
        title="Capabilities"
        aria-label="Capabilities"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M12 5v14M5 12h14" />
        </svg>
      </button>
      {open && view === 'popover' ? (
        <>
          <div className="k-menu-backdrop" onClick={close} aria-hidden="true" />
          <div className="k-composer-menu" role="menu" aria-label="Tools and skills">
            <div className="k-menu-head">
              <span className="k-menu-head-label">
                Capabilities
                {agentName ? <span className="k-menu-head-agent">· {agentName} agent</span> : null}
              </span>
              <CanvasExpandButton
                onClick={() => {
                  void openCapabilitiesOnHost(
                    {
                      agent_id: vm.agentId,
                      conversation_id: vm.state.conversationId ?? undefined,
                    },
                    { source: 'composer-expand' },
                  ).then((acked) => {
                    if (acked) setOpen(false)
                    else setView('modal')
                  })
                }}
                title="Expand"
              />
            </div>
            {body}
          </div>
        </>
      ) : null}
      {open && view === 'modal'
        ? createPortal(
            <div className="k-canvas-modal-backdrop" onClick={close}>
              <div
                className="k-canvas-modal k-menu-modal"
                onClick={(event) => event.stopPropagation()}
                role="dialog"
                aria-modal="true"
                aria-label="Tools and skills"
              >
                <div className="k-canvas-modal-head">
                  <div className="k-canvas-modal-title">
                    <span className="k-text">Capabilities</span>
                    <span className="k-micro">{agentScopeLine}</span>
                  </div>
                  <button
                    type="button"
                    className="k-iconbtn"
                    onClick={() => setView('popover')}
                    aria-label="Collapse to menu"
                    title="Collapse to menu"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    className="k-iconbtn"
                    onClick={close}
                    aria-label="Close (Esc)"
                    title="Close (Esc)"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M18 6L6 18M6 6l12 12" />
                    </svg>
                  </button>
                </div>
                <div className="k-canvas-modal-body k-menu-modal-body">
                  <div className="k-menu-expanded" role="menu" aria-label="Tools and skills">
                    {body}
                  </div>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  )
}
