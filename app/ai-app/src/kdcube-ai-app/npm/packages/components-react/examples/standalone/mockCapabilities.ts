/**
 * Mock mode for the standalone harness (`?mock=1`) — exercise the composer "+"
 * menu with NO backend. Patches `window.fetch` for the four endpoints the menu
 * path touches: `/profile` (a registered caller, so the menu shows),
 * the conversations list, `agent_capabilities` (a canned inventory), and
 * `agent_selection_update` (merge-applied in memory with the same semantics as
 * the engine's optimistic patch, so re-opens reflect the saved deny-list).
 *
 * The live stream is intentionally untouched — with no backend the connection
 * banner appears, which does not affect menu verification.
 */
import { applySelectionPatch } from '@kdcube/components-core/chat'
import type {
  AgentModelPick,
  AgentSelectionDisabled,
  AgentSelectionPatch,
  AgentSelectionPending,
} from '@kdcube/components-core/chat'

const MOCK_INVENTORY = {
  agent: 'main',
  tools: [
    {
      alias: 'io_tools', name: 'io', kind: 'python', system: true,
      tools: [{ name: 'tool_call', description: 'Execute a tool function.' }],
    },
    {
      alias: 'web_tools', name: 'web', kind: 'python', system: false,
      tools: [
        { name: 'web_search', description: 'Web discovery tool (multi-query). Finds and deduplicates pages.' },
        { name: 'web_fetch', description: 'Fetch-only URL dereferencer (no search).' },
      ],
    },
    {
      alias: 'gmail', name: 'gmail', kind: 'python', system: false,
      tools: [
        { name: 'search_gmail', description: 'Search the connected Gmail account.' },
        { name: 'read_gmail_message', description: 'Read one Gmail message body.' },
        { name: 'send_gmail', description: 'Send an email from the connected account.' },
      ],
    },
    {
      alias: 'rendering_tools', name: 'rendering', kind: 'python', system: false,
      tools: [
        { name: 'write_pdf', description: 'Render Markdown or HTML to PDF.' },
        { name: 'write_docx', description: 'Render Markdown to DOCX.' },
      ],
    },
  ],
  mcp: [
    {
      server_id: 'knowledge', alias: 'knowledge', name: 'knowledge', tools: ['*'],
      tool_entries: [
        { name: 'kb_search', description: 'Search the knowledge base.' },
        { name: 'kb_fetch', description: 'Fetch one document.' },
      ],
    },
  ],
  supported_models: [
    { model: 'claude-sonnet-4-6', provider: 'anthropic', label: 'Sonnet 4.6' },
    { model: 'claude-haiku-4-5-20251001', provider: 'anthropic', label: 'Haiku 4.5' },
  ],
  default_model: { provider: 'anthropic', model: 'claude-sonnet-4-6' },
  named_services: [
    { namespace: 'mem', alias: 'named_services', operations: ['provider.about', 'object.list'], tools: ['provider_about', 'list_objects'] },
    { namespace: 'task', alias: 'named_services', operations: ['provider.about', 'object.upsert'], tools: ['provider_about', 'upsert_object'] },
    { namespace: 'cnv', alias: 'named_services', operations: ['provider.about', 'object.upsert'], tools: ['provider_about', 'upsert_object'] },
  ],
  skills: [
    {
      id: 'public.docx-press', name: 'docx-press', namespace: 'public',
      description: 'Author Markdown that renders cleanly into DOCX via write_docx.',
      when_to_use: ['Generating Markdown for write_docx'],
    },
    {
      id: 'public.web-research', name: 'web research', namespace: 'public',
      description: 'Multi-source web research with citations.',
      when_to_use: ['Multi-source questions'],
    },
  ],
}

export function installCapabilitiesMock(): void {
  let disabled: AgentSelectionDisabled = {}
  let model: AgentModelPick | null = null
  let cachePolicy: Record<string, string> = {}
  let pending: AgentSelectionPending | null = null
  const params = new URLSearchParams(window.location.search)
  // `?policy=confirm|accept|defer_cold|defer_conversation` — the admin default
  // for BOTH classes in mock mode (platform default: confirm).
  const adminDefault = params.get('policy') || 'confirm'
  const effectivePolicy = () => ({
    model_switch: cachePolicy.model_switch || adminDefault,
    capability_toggle: cachePolicy.capability_toggle || adminDefault,
  })
  const cachePolicyBlock = () => ({
    effective: effectivePolicy(),
    allowed: ['accept', 'confirm', 'defer_cold', 'defer_conversation'],
    default: { model_switch: adminDefault, capability_toggle: adminDefault },
  })
  const realFetch = window.fetch.bind(window)

  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input instanceof Request ? input.url : input)
    if (url.endsWith('/profile')) {
      return json({ session_id: 'mock-session', user_id: 'mock-user', user_type: 'registered', roles: [] })
    }
    if (url.includes('/api/cb/conversations/')) {
      // One loadable conversation with one (empty) turn, so the harness can
      // exercise turn-gated behavior such as the cache-cost notices.
      if (url.endsWith('/mock-conv-1/fetch')) {
        return json({ conversation_id: 'mock-conv-1', title: 'Mock conversation', turns: [{ turn_id: 'turn-1', artifacts: [] }] })
      }
      if (url.includes('/turns-with-feedbacks')) {
        return json({ items: [] })
      }
      return json({ items: [{ conversation_id: 'mock-conv-1', title: 'Mock conversation', started_at: new Date().toISOString(), last_activity_at: new Date().toISOString() }] })
    }
    if (url.includes('/operations/agent_capabilities')) {
      return json({
        ok: true, agent: 'main', capabilities: MOCK_INVENTORY,
        selection: { schema_version: 1, disabled, model, cache_policy: cachePolicy, pending },
        cache_policy: cachePolicyBlock(),
      })
    }
    if (url.includes('/operations/agent_selection_update')) {
      const body = JSON.parse(String(init?.body ?? '{}')) as {
        data?: {
          disabled?: AgentSelectionPatch
          model?: AgentModelPick | null
          apply?: string
          conversation_id?: string
          cache_policy?: Record<string, string>
        }
      }
      const apply = body.data?.apply || 'now'
      if (body.data?.cache_policy) {
        cachePolicy = { ...cachePolicy, ...body.data.cache_policy }
      }
      if (apply === 'next_conversation' || apply === 'when_cold') {
        pending = {
          ...(body.data?.disabled && Object.keys(body.data.disabled).length ? { disabled: body.data.disabled } : {}),
          ...(body.data && 'model' in body.data ? { model: body.data.model ?? null } : {}),
          apply,
          since_conversation_id: body.data?.conversation_id || '',
        }
      } else {
        disabled = applySelectionPatch(disabled, body.data?.disabled ?? {})
        if (body.data && 'model' in body.data) {
          model = body.data.model ?? null
        }
      }
      console.info('[mock] agent_selection_update ->', JSON.stringify({ apply, disabled, model, cachePolicy, pending }))
      return json({ ok: true, agent: 'main', selection: { schema_version: 1, disabled, model, cache_policy: cachePolicy, pending } })
    }
    return realFetch(input as RequestInfo, init)
  }
  console.info('[mock] capabilities mock installed — composer "+" menu runs without a backend')
}
