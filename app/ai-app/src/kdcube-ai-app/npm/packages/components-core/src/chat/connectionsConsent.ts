/**
 * Connected-account consent → Connection-Hub open payload.
 *
 * A consent card names the provider and the claims a tool needs (e.g.
 * `slack:search`, `slack:post`, `gmail:send`). The Connection Hub's connect
 * cards grant access by claim TIER (the `connections.hub.open` contract), so
 * this module maps claim ids to the provider's tier ids. Claims outside the
 * map simply contribute no tier — the hub then falls back to its own default
 * tier preselection.
 *
 * Tier vocabulary source of truth: `ConnectionProvider.claim_tiers`
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

/** Build the structured hub-open payload from a consent card's fields. */
export function connectionsConsentOpen(args: {
  provider: string
  claims: string[]
  accountId?: string
  url?: string
}): ConnectionsConsentOpen {
  const provider = String(args.provider || '').trim()
  return {
    tab: 'provider_connections',
    provider,
    tiers: consentTiersForClaims(provider, args.claims || []),
    accountId: String(args.accountId || '').trim(),
    url: String(args.url || '').trim(),
  }
}
