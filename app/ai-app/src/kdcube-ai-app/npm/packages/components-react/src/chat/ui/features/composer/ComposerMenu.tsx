/**
 * ComposerMenu — the composer "+" menu: per-user agent capability toggles.
 *
 * A registered user narrows which of the agent's CONFIGURED tools and skills it
 * uses. The inventory + saved selection lazy-load on first open
 * (`agent_capabilities`); each row toggle applies optimistically and saves via
 * the engine's debounced `agent_selection_update` merge-write. Toggles apply
 * from the next message.
 *
 * Structure is a SECTIONS REGISTRY: ordered descriptors, each rendering its own
 * rows. The four capability sections (skills / tool groups / MCP servers /
 * named-service namespaces) ship built-in; hosts extend the menu by passing
 * more descriptors (`extraSections`) — a future connectors entry is just a new
 * descriptor, no menu changes.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type {
  AgentCapabilitiesInventory,
  AgentSelectionDisabled,
  AgentSelectionPatch,
  NamespaceStyleMap,
} from '@kdcube/components-core/chat'
import {
  isMcpToolDisabled,
  isNamespaceDisabled,
  isSkillDisabled,
  isToolDisabled,
  mcpServerState,
  mcpServerTogglePatch,
  mcpToolTogglePatch,
  toolGroupState,
  toolGroupTogglePatch,
  toolTogglePatch,
} from '@kdcube/components-core/chat'
import { namespaceStyleForKey } from '@kdcube/components-core'
import { useChatViewModel } from '../../context.tsx'
import type { ChatViewModel } from '../../viewModel.ts'

export interface ComposerMenuSectionContext {
  vm: ChatViewModel
  close: () => void
}

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
  checked,
  onToggle,
  expandable,
  expanded,
  onExpand,
  child = false,
}: {
  label: ReactNode
  sub?: string
  checked: 'on' | 'off' | 'partial'
  onToggle: () => void
  expandable?: boolean
  expanded?: boolean
  onExpand?: () => void
  child?: boolean
}) {
  return (
    <div className={`k-menu-row ${child ? 'k-menu-row-child' : ''}`}>
      <button
        type="button"
        role="menuitemcheckbox"
        aria-checked={checked === 'on' ? 'true' : checked === 'partial' ? 'mixed' : 'false'}
        className="k-menu-row-main"
        title={sub || undefined}
        onClick={onToggle}
      >
        <span className="k-menu-row-text">
          <span className="k-menu-row-label">{label}</span>
          {sub ? <span className="k-menu-row-sub">{sub}</span> : null}
        </span>
        <span className="k-menu-row-state">{checked === 'off' ? null : <CheckIcon state={checked === 'partial' ? 'partial' : 'on'} />}</span>
      </button>
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

function firstLine(text: string): string {
  return String(text || '').split('\n')[0].trim()
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

function ToolGroupsSection({ inventory, disabled, toggle }: CapabilityRowsProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const groups = inventory.tools.filter((group) => !group.system)
  if (!groups.length) return null
  return (
    <div>
      <SectionTitle>Tools</SectionTitle>
      {groups.map((group) => {
        const state = toolGroupState(group, disabled)
        const isOpen = Boolean(expanded[group.alias])
        return (
          <div key={group.alias}>
            <MenuRow
              label={group.name || group.alias}
              checked={state}
              onToggle={() => toggle(toolGroupTogglePatch(group, disabled))}
              expandable={group.tools.length > 0}
              expanded={isOpen}
              onExpand={() => setExpanded((current) => ({ ...current, [group.alias]: !isOpen }))}
            />
            {isOpen
              ? group.tools.map((tool) => (
                  <MenuRow
                    key={tool.name}
                    child
                    label={<code>{tool.name}</code>}
                    sub={firstLine(tool.description)}
                    checked={isToolDisabled(disabled, group.alias, tool.name) ? 'off' : 'on'}
                    onToggle={() => toggle(toolTogglePatch(group, disabled, tool.name))}
                  />
                ))
              : null}
          </div>
        )
      })}
    </div>
  )
}

function McpSection({ inventory, disabled, toggle }: CapabilityRowsProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  if (!inventory.mcp.length) return null
  return (
    <div>
      <SectionTitle>MCP servers</SectionTitle>
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
            />
            {isOpen
              ? entries.map((tool) => (
                  <MenuRow
                    key={tool.name}
                    child
                    label={<code>{tool.name}</code>}
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

function ServicesSection({ inventory, disabled, toggle, namespaceStyles }: CapabilityRowsProps) {
  if (!inventory.named_services.length) return null
  return (
    <div>
      <SectionTitle>Services</SectionTitle>
      {inventory.named_services.map((entry) => (
        <MenuRow
          key={entry.namespace}
          label={namespaceLabel(entry.namespace, namespaceStyles)}
          checked={isNamespaceDisabled(disabled, entry.namespace) ? 'off' : 'on'}
          onToggle={() => toggle({ named_services: { [entry.namespace]: !isNamespaceDisabled(disabled, entry.namespace) } })}
        />
      ))}
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
      <SectionTitle>Connectors</SectionTitle>
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
    Section: (props: CapabilityRowsProps) => ReactNode,
  ): ComposerMenuSectionDescriptor => ({
    id,
    order,
    render: ({ vm }) => {
      const { inventory, disabled, toggle } = vm.capabilities
      if (!inventory) return null
      return (
        <Section
          inventory={inventory}
          disabled={disabled}
          toggle={toggle}
          namespaceStyles={namespaceStyles}
        />
      )
    },
  })
  return [
    capabilitySection('skills', 10, SkillsSection),
    capabilitySection('tools', 20, ToolGroupsSection),
    capabilitySection('mcp', 30, McpSection),
    capabilitySection('services', 40, ServicesSection),
    {
      id: 'connectors',
      order: 50,
      render: (ctx) => <ConnectorsSection {...ctx} />,
    },
  ]
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
  const [open, setOpen] = useState(false)
  const anchorRef = useRef<HTMLDivElement | null>(null)
  const capabilities = vm.capabilities

  useEffect(() => {
    if (open) capabilities.load()
  }, [open, capabilities])

  useEffect(() => {
    if (!open) return
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open])

  const sections = useMemo(() => {
    return [...builtInSections(namespaceStyles), ...extraSections]
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
  }, [namespaceStyles, extraSections])

  // Registered users only; the ops behind the menu require an authenticated caller.
  if (!vm.authed) return null

  const close = () => setOpen(false)

  let body: ReactNode
  if (capabilities.status === 'loading' || capabilities.status === 'idle') {
    body = (
      <div className="k-menu-status">
        <span className="k-menu-spinner" aria-hidden="true" />
        Loading tools &amp; skills…
      </div>
    )
  } else if (capabilities.status === 'error') {
    body = (
      <button type="button" className="k-menu-status" onClick={() => capabilities.load({ force: true })}>
        Couldn&rsquo;t load tools &amp; skills. Tap to retry.
      </button>
    )
  } else {
    const rendered = sections
      .map((section) => ({ id: section.id, node: section.render({ vm, close }) }))
      .filter((section) => section.node !== null && section.node !== undefined && section.node !== false)
    body = rendered.length ? (
      <>
        {rendered.map((section, index) => (
          <div key={section.id}>
            {index > 0 ? <div className="k-menu-divider" role="separator" /> : null}
            {section.node}
          </div>
        ))}
        <div className="k-menu-foot">
          {capabilities.saveError
            ? 'Changes couldn’t be saved. They’ll retry with your next change.'
            : 'Changes apply from your next message.'}
        </div>
      </>
    ) : (
      <div className="k-menu-status">This agent uses its full configured set.</div>
    )
  }

  return (
    <div ref={anchorRef} className="k-composer-menu-anchor">
      <button
        type="button"
        className={`k-iconbtn ${open ? 'k-iconbtn-active' : ''}`}
        title="Tools & skills"
        aria-label="Tools & skills"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M12 5v14M5 12h14" />
        </svg>
      </button>
      {open ? (
        <>
          <div className="k-menu-backdrop" onClick={close} aria-hidden="true" />
          <div className="k-composer-menu" role="menu" aria-label="Tools and skills">
            {body}
          </div>
        </>
      ) : null}
    </div>
  )
}
