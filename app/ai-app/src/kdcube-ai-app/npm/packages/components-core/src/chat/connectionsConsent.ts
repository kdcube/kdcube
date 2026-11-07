/**
 * Connected-account consent → Connection-Hub open payload.
 *
 * The backend's consent payload carries a served Connection-Hub deep link
 * (`consent.url`) that encodes the CORRECT view for the deployment — for the
 * delegated-accounts flow that is `?tab=delegated_to_kdcube&provider_id=…&
 * connector_app_id=…&claims=…`, the numbered consent plan. The hub-open
 * payload therefore derives its `tab` and params from that URL verbatim, so
 * the on-scene summon lands on exactly the view the direct link would.
 *
 * URLs that point at the provider-connections cards grant access by claim
 * TIER, so that path also maps the consent's claim ids to the provider's tier
 * ids. Tier vocabulary source of truth: `ConnectionProvider.claim_tiers`
 * (integrations/connections/providers) — Slack: read / write / files,
 * Gmail (provider `google`): read / send.
 */
import type { ConnectionsConsentOpen } from '../shared/index.ts'

const CLAIM_TIERS_BY_PROVIDER: Record<string, Record<string, string>> = {
  slack: {
    'slack:search': 'read',
    'slack:channels': 'read',
    'slack:history': 'read',
    'slack:assistant:search': 'read',
    'slack:post': 'write',
    'slack:files:read': 'files',
    'slack:files:write': 'files',
  },
  google: {
    'gmail:read': 'read',
    'gmail:send': 'send',
  },
}

const PROVIDER_CONNECTIONS_TAB_TOKENS = new Set([
  'provider_connections',
  'provider-connections',
  'providerconnections',
  'providers',
])

/** Provider claim-tier ids covering the given claims (declaration order of
 *  first appearance, deduped). Claims outside the provider's map are omitted. */
export function consentTiersForClaims(provider: string, claims: string[]): string[] {
  const map = CLAIM_TIERS_BY_PROVIDER[String(provider || '').trim().toLowerCase()] || {}
  const tiers: string[] = []
  for (const claim of claims) {
    const tier = map[String(claim || '').trim().toLowerCase()]
    if (tier && !tiers.includes(tier)) tiers.push(tier)
  }
  return tiers
}

/** Parse a served hub deep link into {tab, params}. The link may be
 *  site-relative (`/api/integrations/...?...`); only its query matters here. */
function deepLinkTabAndParams(url: string): { tab: string; params: Record<string, string> } | null {
  const query = url.includes('?') ? url.slice(url.indexOf('?') + 1) : ''
  if (!query) return null
  let search: URLSearchParams
  try {
    search = new URLSearchParams(query)
  } catch {
    return null
  }
  const tab = String(search.get('tab') || '').trim()
  if (!tab) return null
  const params: Record<string, string> = {}
  search.forEach((value, key) => {
    const cleanKey = String(key || '').trim()
    const cleanValue = String(value || '').trim()
    if (cleanKey && cleanKey !== 'tab' && cleanValue) params[cleanKey] = cleanValue
  })
  return { tab, params }
}

/** Hub-open payload for a picker-row consent affordance: seeds the consent
 *  plan with exactly the named claims. `url` stays empty — a host without the
 *  scene contract falls back to the widget's own served-URL path. */
export function consentOpenForClaims(args: {
  providerId: string
  connectorAppId?: string
  claims: string[]
}): ConnectionsConsentOpen {
  const params: Record<string, string> = {}
  const provider = String(args.providerId || '').trim()
  if (provider) params.provider_id = provider
  const connector = String(args.connectorAppId || '').trim()
  if (connector) params.connector_app_id = connector
  const claims = (args.claims || []).map((item) => String(item || '').trim()).filter(Boolean)
  if (claims.length) params.claims = claims.join(',')
  return { tab: 'delegated_to_kdcube', params, url: '' }
}

/** Hub-open payload for a per-agent grant demand: lands on the "Delegated by
 *  KDCube" tab (the automation/delegated-access panel — an agent grant is a kind
 *  of automation access) with the pending agent + resource + claims, so the panel
 *  can open Card consent review. `url` is the served deep-link fallback for hosts
 *  without the scene contract. */
export function agentGrantConsentOpen(args: {
  agentClientId: string
  resource: string
  claims: string[]
  namespace?: string
  operation?: string
  outerOperation?: string
  accessId?: string
  invocationPolicy?: string
  invocationChangeId?: string
  accountId?: string
  accountClaim?: string
  accountProviderId?: string
  url?: string
}): ConnectionsConsentOpen {
  const url = String(args.url || '').trim()
  const reviewed = deepLinkTabAndParams(url)
  if (reviewed?.tab === 'delegated_by_kdcube' && reviewed.params.agent_card_consent === '1') {
    // The Card-review request is complete, including an intentionally empty
    // door-claim selection. Preserve it rather than rebuilding from a richer
    // banner and dropping the account-only context.
    return { ...reviewed, url }
  }
  const params: Record<string, string> = { pending_agent_grant: '1' }
  const client = String(args.agentClientId || '').trim()
  if (client) params.agent_client_id = client
  if (client.startsWith('kdcube-agent:')) params.agent_card_consent = '1'
  const resource = String(args.resource || '').trim()
  if (resource) params.resource = resource
  const claims = (args.claims || []).map((item) => String(item || '').trim()).filter(Boolean)
  if (claims.length) params.claims = claims.join(',')
  // The demand names one inner operation; the panel seeds its picker from these
  // two, so omitting them opens the grant with nothing selected.
  const namespace = String(args.namespace || '').trim()
  if (namespace) params.namespace = namespace
  const operation = String(args.operation || '').trim()
  if (operation) params.operation = operation
  const outerOperation = String(args.outerOperation || '').trim()
  if (outerOperation) params.outer_operation = outerOperation
  const accessId = String(args.accessId || '').trim()
  if (accessId) params.access_id = accessId
  const invocationPolicy = String(args.invocationPolicy || '').trim()
  if (invocationPolicy) params.invocation_policy = invocationPolicy
  const invocationChangeId = String(args.invocationChangeId || '').trim()
  if (invocationChangeId) params.invocation_change_id = invocationChangeId
  const accountId = String(args.accountId || '').trim()
  if (accountId) params.account_id = accountId
  const accountClaim = String(args.accountClaim || '').trim()
  if (accountClaim) params.account_claim = accountClaim
  const accountProvider = String(args.accountProviderId || '').trim()
  if (accountProvider) params.account_provider_id = accountProvider
  return { tab: 'delegated_by_kdcube', params, url }
}

/** Build the structured hub-open payload from a consent card's fields.
 *  The consent deep link is authoritative for tab + params; the claim→tier
 *  map fills `tiers` on the provider-connections path. */
export function connectionsConsentOpen(args: {
  provider: string
  claims: string[]
  accountId?: string
  url?: string
}): ConnectionsConsentOpen {
  const url = String(args.url || '').trim()
  const provider = String(args.provider || '').trim()
  const claims = args.claims || []
  const fromUrl = deepLinkTabAndParams(url)
  if (fromUrl) {
    if (PROVIDER_CONNECTIONS_TAB_TOKENS.has(fromUrl.tab.toLowerCase())) {
      if (!fromUrl.params.provider && provider) fromUrl.params.provider = provider
      if (!fromUrl.params.tiers) {
        const tiers = consentTiersForClaims(fromUrl.params.provider || provider, claims)
        if (tiers.length) fromUrl.params.tiers = tiers.join(',')
      }
    }
    return { tab: fromUrl.tab, params: fromUrl.params, url }
  }
  // Deep link without a tab: land on the provider-connections card built from
  // the consent fields.
  const params: Record<string, string> = {}
  if (provider) params.provider = provider
  const tiers = consentTiersForClaims(provider, claims)
  if (tiers.length) params.tiers = tiers.join(',')
  const accountId = String(args.accountId || '').trim()
  if (accountId) params.account_id = accountId
  return { tab: 'provider_connections', params, url }
}
