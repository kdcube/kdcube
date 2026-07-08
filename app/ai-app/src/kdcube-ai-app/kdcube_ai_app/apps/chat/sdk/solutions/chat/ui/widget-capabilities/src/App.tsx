/**
 * Full-page capability picker — the served-widget presentation of the SAME
 * picker the chat composer's "+" menu drives (one body, third shell). Data
 * flows through the two chatbot operations (`agent_capabilities`,
 * `agent_selection_update`) with the widget handshake's auth; consent
 * actions deep-link into the Connection Hub settings widget.
 */

import { useEffect, useState } from 'react'
import {
  CapabilityPickerPage,
  useStandaloneCapabilitiesVm,
  type StandaloneCapabilitiesResponse,
  type StandaloneSelectionWriteOptions,
} from '@kdcube/components-react/chat'
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

function openConnections(consent: ConnectionsConsentOpen): void {
  const params = new URLSearchParams({ tab: consent.tab || 'delegated_to_kdcube' })
  for (const [key, value] of Object.entries(consent.params || {})) {
    if (value) params.set(key, String(value))
  }
  const url = consent.url
    || (
      `${settings.getBaseUrl()}/api/integrations/bundles/` +
      `${encodeURIComponent(settings.getTenant())}/${encodeURIComponent(settings.getProject())}/` +
      `${encodeURIComponent(CONNECTION_HUB_BUNDLE_ID)}/widgets/connections_settings?${params.toString()}`
    )
  window.open(url, '_blank', 'noopener')
}

function PickerApp() {
  const agentId = settings.getAgentId()
  const vm = useStandaloneCapabilitiesVm({
    agentId,
    fetchCapabilities: () => callOperation('agent_capabilities', { agent: agentId }),
    submitUpdate: (patch: AgentSelectionPatch, options?: StandaloneSelectionWriteOptions) => {
      const { model, ...disabled } = patch
      const apply = options?.apply && options.apply !== 'now' ? options.apply : undefined
      return callOperation('agent_selection_update', {
        agent: agentId,
        disabled,
        ...(model !== undefined ? { model } : {}),
        ...(apply ? { apply } : {}),
        ...(options?.cachePolicy ? { cache_policy: options.cachePolicy } : {}),
      })
    },
    openConnections,
  })
  return (
    <CapabilityPickerPage
      vm={vm}
      title="Tools & skills"
      subtitle={`Everything the ${agentId} agent may use for you — narrow it here.`}
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
