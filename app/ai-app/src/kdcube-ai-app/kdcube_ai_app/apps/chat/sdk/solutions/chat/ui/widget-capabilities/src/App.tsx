/**
 * Full-page capability picker — the served-widget presentation of the SAME
 * picker the chat composer's "+" menu drives (one body, third shell). Data
 * flows through the two chatbot operations (`agent_capabilities`,
 * `agent_selection_update`) with the widget handshake's auth; consent
 * actions deep-link into the Connection Hub settings widget.
 */

import { useEffect, useRef, useState } from 'react'
import {
  CapabilityPickerPage,
  useStandaloneCapabilitiesVm,
  type StandaloneCapabilitiesResponse,
  type StandaloneSelectionWriteOptions,
} from '@kdcube/components-react/chat'
import {
  ackCapabilitiesOpen,
  openConnectionsOnHost,
  parseCapabilitiesOpen,
} from '@kdcube/components-core/chat'
import type { AgentSelectionPatch, ConnectionsConsentOpen } from '@kdcube/components-core/chat'
import { settings } from './settings.ts'

const CONNECTION_HUB_BUNDLE_ID = 'connection-hub@1-0'

function operationUrl(alias: string): string {
  return (
    `${settings.getBaseUrl()}/api/integrations/bundles/` +
    `${encodeURIComponent(settings.getTenant())}/${encodeURIComponent(settings.getProject())}/` +
    `${encodeURIComponent(settings.getBundleId())}/operations/${alias}`
  )
}

function unwrapOperationBody(payload: unknown, alias: string): StandaloneCapabilitiesResponse | null {
  if (!payload || typeof payload !== 'object') return null
  const record = payload as Record<string, unknown>
  const nested = record[alias] ?? record.result ?? record
  return (nested && typeof nested === 'object' ? nested : record) as StandaloneCapabilitiesResponse
}

async function callOperation(alias: string, data: Record<string, unknown>): Promise<StandaloneCapabilitiesResponse> {
  const response = await fetch(operationUrl(alias), {
    method: 'POST',
    credentials: 'include',
    headers: settings.authHeaders({ 'Content-Type': 'application/json', Accept: 'application/json' }),
    body: JSON.stringify({ data }),
  })
  const payload = await response.json().catch(() => null)
  const body = unwrapOperationBody(payload, alias)
  if (!response.ok || !body || (body as { ok?: boolean }).ok === false) {
    const detail = body && typeof body === 'object'
      ? String((body as { message?: string; error?: string }).message || (body as { error?: string }).error || response.statusText)
      : response.statusText
    throw new Error(`${alias} failed (${response.status}): ${detail}`)
  }
  return body
}

function connectionsDeepLink(consent?: ConnectionsConsentOpen): string {
  if (consent?.url) return consent.url
  const params = new URLSearchParams({ tab: consent?.tab || 'delegated_to_kdcube' })
  for (const [key, value] of Object.entries(consent?.params || {})) {
    if (value) params.set(key, String(value))
  }
  return (
    `${settings.getBaseUrl()}/api/integrations/bundles/` +
    `${encodeURIComponent(settings.getTenant())}/${encodeURIComponent(settings.getProject())}/` +
    `${encodeURIComponent(CONNECTION_HUB_BUNDLE_ID)}/widgets/connections_settings?${params.toString()}`
  )
}

/** Host-first open: this widget lives in a scene window, so the hub should
 *  arrive as the scene's own Connection Hub window when the host declares
 *  `connections.hub.open` (`kdcube.surface.command` + ack-wait, the same
 *  emit the chat widget uses). No ack — the absolute deep link opens in a
 *  new tab. Never a silent no-op. */
function openConnections(consent?: ConnectionsConsentOpen): void {
  void openConnectionsOnHost(
    consent ? { tab: consent.tab, params: consent.params } : null,
    { source: 'capabilities-widget', widget: 'capabilities' },
  ).then((acked) => {
    if (acked) return
    window.open(connectionsDeepLink(consent), '_blank', 'noopener')
  })
}

function PickerApp() {
  // Scene hosts summon this widget with a `capabilities.open` surface
  // command: {agent_id?, conversation_id?, spotlight_tools?, section?} in
  // ui_event applies at runtime. Chat-originated commands carry the current
  // conversation; an independently mounted widget has no conversation and
  // edits the baseline for future conversations.
  const [agentId, setAgentId] = useState(settings.getAgentId())
  const [conversationId, setConversationId] = useState('')
  const [spotlight, setSpotlight] = useState<{ tools: string[]; nonce: number } | null>(null)
  const agentRef = useRef(agentId)
  const conversationRef = useRef(conversationId)
  agentRef.current = agentId
  conversationRef.current = conversationId

  const vm = useStandaloneCapabilitiesVm({
    agentId,
    fetchCapabilities: () => callOperation('agent_capabilities', {
      agent: agentRef.current,
      ...(conversationRef.current ? { conversation_id: conversationRef.current } : {}),
    }),
    submitUpdate: (patch: AgentSelectionPatch, options?: StandaloneSelectionWriteOptions) => {
      const { model, ...disabled } = patch
      const apply = options?.apply && options.apply !== 'now' ? options.apply : undefined
      return callOperation('agent_selection_update', {
        agent: agentRef.current,
        disabled,
        ...(model !== undefined ? { model } : {}),
        ...(apply ? { apply } : {}),
        ...(options?.cachePolicy ? { cache_policy: options.cachePolicy } : {}),
        ...(conversationRef.current ? { conversation_id: conversationRef.current } : {}),
      })
    },
    openConnections,
  }, { spotlight })

  const loadRef = useRef(vm.capabilities.load)
  loadRef.current = vm.capabilities.load
  useEffect(() => {
    const onSurfaceCommand = (event: MessageEvent) => {
      const command = parseCapabilitiesOpen(event.data)
      if (!command) return
      const payload = command.payload
      let reload = false
      if (payload.agent_id && payload.agent_id !== agentRef.current) {
        agentRef.current = payload.agent_id
        setAgentId(payload.agent_id)
        reload = true
      }
      const nextConversationId = payload.conversation_id ?? ''
      if (nextConversationId !== conversationRef.current) {
        conversationRef.current = nextConversationId
        setConversationId(nextConversationId)
        reload = true
      }
      if (payload.spotlight_tools?.length) {
        setSpotlight({ tools: payload.spotlight_tools, nonce: Date.now() })
      }
      if (payload.section) {
        const anchor = `[data-picker-section="${payload.section}"]`
        window.setTimeout(() => {
          document.querySelector(anchor)?.scrollIntoView({ block: 'start', behavior: 'smooth' })
        }, 120)
      }
      if (reload) window.setTimeout(() => void loadRef.current({ force: true }), 0)
      ackCapabilitiesOpen(command, 'applied')
    }
    window.addEventListener('message', onSurfaceCommand)
    return () => window.removeEventListener('message', onSurfaceCommand)
  }, [])

  return (
    <CapabilityPickerPage
      vm={vm}
      title="Capabilities"
      subtitle={conversationId
        ? `Choose what the ${agentId} agent may use in this conversation.`
        : `Choose what new conversations with the ${agentId} agent start with.`}
    />
  )
}

export default function App() {
  const [ready, setReady] = useState(false)
  useEffect(() => {
    void settings.setupParentListener().then(() => setReady(true))
  }, [])
  if (!ready) {
    return (
      <div className="k-menu-page">
        <div className="k-menu-status">Connecting…</div>
      </div>
    )
  }
  return <PickerApp />
}
