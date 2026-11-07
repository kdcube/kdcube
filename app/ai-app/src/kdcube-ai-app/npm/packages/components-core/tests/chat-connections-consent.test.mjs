import assert from 'node:assert/strict'
import { test } from 'node:test'
import { agentGrantConsentOpen, connectionsConsentOpen, consentOpenForClaims, consentTiersForClaims } from '../dist/chat/index.js'

// Surfaced live case: the backend consent deep link points at the
// Delegated-to-KDCube consent plan; the hub-open payload must carry that tab
// and the link's params verbatim (the summon lands where the direct link would).
test('delegated consent deep link drives tab and params verbatim', () => {
  const open = connectionsConsentOpen({
    provider: 'slack',
    claims: ['slack:search', 'slack:post'],
    accountId: 'acc1',
    url: '/api/integrations/bundles/t/p/connection-hub%401-0/widgets/connections_settings'
      + '?tab=delegated_to_kdcube&provider_id=slack&connector_app_id=demo'
      + '&claims=slack%3Asearch%2Cslack%3Apost&tool_name=search_slack&account_id=acc1',
  })
  assert.equal(open.tab, 'delegated_to_kdcube')
  assert.deepEqual(open.params, {
    provider_id: 'slack',
    connector_app_id: 'demo',
    claims: 'slack:search,slack:post',
    tool_name: 'search_slack',
    account_id: 'acc1',
  })
})

test('provider-connections deep link keeps its params and fills tiers from claims', () => {
  const open = connectionsConsentOpen({
    provider: 'slack',
    claims: ['slack:search', 'slack:post', 'slack:unknown'],
    url: '/widgets/connections_settings?tab=provider_connections&provider=slack&account_id=acc2',
  })
  assert.equal(open.tab, 'provider_connections')
  assert.equal(open.params.provider, 'slack')
  assert.equal(open.params.account_id, 'acc2')
  assert.equal(open.params.tiers, 'read,write')
})

test('deep link tiers stay authoritative when the link names them', () => {
  const open = connectionsConsentOpen({
    provider: 'slack',
    claims: ['slack:post'],
    url: '/widgets/connections_settings?tab=provider_connections&provider=slack&tiers=read',
  })
  assert.equal(open.params.tiers, 'read')
})

test('a link without a tab lands on the provider-connections card from consent fields', () => {
  const open = connectionsConsentOpen({
    provider: 'google',
    claims: ['gmail:read', 'gmail:send'],
    accountId: 'g1',
    url: '/widgets/connections_settings',
  })
  assert.equal(open.tab, 'provider_connections')
  assert.deepEqual(open.params, { provider: 'google', tiers: 'read,send', account_id: 'g1' })
})

test('claim to tier map covers the shipped providers', () => {
  assert.deepEqual(consentTiersForClaims('slack', ['slack:files:read', 'slack:history']), ['files', 'read'])
  assert.deepEqual(consentTiersForClaims('google', ['gmail:send']), ['send'])
  assert.deepEqual(consentTiersForClaims('slack', ['other:claim']), [])
})

test('a picker consent affordance seeds the plan with exactly the named claims', () => {
  const open = consentOpenForClaims({
    providerId: 'slack',
    connectorAppId: 'demo',
    claims: ['slack:post', ' ', 'slack:files:write'],
  })
  assert.equal(open.tab, 'delegated_to_kdcube')
  assert.deepEqual(open.params, {
    provider_id: 'slack',
    connector_app_id: 'demo',
    claims: 'slack:post,slack:files:write',
  })
  // No served URL of its own: a host without the scene contract falls back
  // to the widget's served-URL path.
  assert.equal(open.url, '')
})

// The structured scene payload and the served deep link must agree: the URL
// fallback already carries these two, so a host with the scene contract must
// not receive less.
test('an agent grant open carries the inner operation when the demand names one', () => {
  const open = agentGrantConsentOpen({
    agentClientId: 'kdcube-agent:app:main',
    resource: 'https://h/api/mcp/named_services',
    claims: ['named_services:use'],
    namespace: 'task',
    operation: 'object.search',
  })
  assert.equal(open.tab, 'delegated_by_kdcube')
  assert.equal(open.params.namespace, 'task')
  assert.equal(open.params.operation, 'object.search')
})

test('an agent grant open omits the inner operation when the demand names none', () => {
  const open = agentGrantConsentOpen({
    agentClientId: 'kdcube-agent:app:main',
    resource: 'https://h/api/mcp/mem',
    claims: ['memories:read'],
  })
  assert.equal('namespace' in open.params, false)
  assert.equal('operation' in open.params, false)
})

test('Agent Card consent preserves the account-only link across the chat scene hop', () => {
  const open = agentGrantConsentOpen({
    agentClientId: 'kdcube-agent:app:main', resource: 'urn:named-services',
    claims: ['slides:read'], namespace: 'stale', operation: 'stale.operation',
    url: '/widgets/connections_settings?tab=delegated_by_kdcube&pending_agent_grant=1'
      + '&agent_card_consent=1&agent_client_id=kdcube-agent%3Aapp%3Amain&resource=urn%3Anamed-services'
      + '&claims=&account_provider_id=google&account_claim=slides%3Aread&account_id=work',
  })
  assert.equal(open.params.agent_card_consent, '1')
  assert.equal(open.params.account_provider_id, 'google')
  assert.equal(open.params.account_claim, 'slides:read')
  assert.equal(open.params.account_id, 'work')
  assert.equal('claims' in open.params, false)
  assert.equal('namespace' in open.params, false)
  assert.equal('operation' in open.params, false)
})

test('an account-only structured request keeps provider claims out of door grants without a URL', () => {
  const open = agentGrantConsentOpen({agentClientId: 'kdcube-agent:app:main',
    resource: 'urn:named-services', claims: ['slides:read'],
    accountProviderId: 'google', accountClaim: 'slides:read', accountId: 'work'})
  assert.equal(open.params.agent_card_consent, '1')
  assert.equal(open.params.account_claim, 'slides:read')
  assert.equal('claims' in open.params, false)
})
