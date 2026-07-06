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
import type { AgentSelectionDisabled, AgentSelectionPatch } from '@kdcube/components-core/chat'

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
  const realFetch = window.fetch.bind(window)

  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input instanceof Request ? input.url : input)
    if (url.endsWith('/profile')) {
      return json({ session_id: 'mock-session', user_id: 'mock-user', user_type: 'registered', roles: [] })
    }
    if (url.includes('/api/cb/conversations/')) {
      return json({ items: [] })
    }
    if (url.includes('/operations/agent_capabilities')) {
      return json({ ok: true, agent: 'main', capabilities: MOCK_INVENTORY, selection: { schema_version: 1, disabled } })
    }
    if (url.includes('/operations/agent_selection_update')) {
      const body = JSON.parse(String(init?.body ?? '{}')) as { data?: { disabled?: AgentSelectionPatch } }
      disabled = applySelectionPatch(disabled, body.data?.disabled ?? {})
      console.info('[mock] agent_selection_update ->', JSON.stringify(disabled))
      return json({ ok: true, agent: 'main', selection: { schema_version: 1, disabled } })
    }
    return realFetch(input as RequestInfo, init)
  }
  console.info('[mock] capabilities mock installed — composer "+" menu runs without a backend')
}
