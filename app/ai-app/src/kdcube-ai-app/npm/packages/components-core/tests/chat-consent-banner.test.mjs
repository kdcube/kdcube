import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  applyChatStep,
  chatActions,
  chatReducer,
  createEmptyTurn,
  initialState,
} from '../dist/chat/index.js'

// Surfaced live: two stacked consent banners (a stale connect_required from
// before the account existed + the current claim_upgrade_required), re-raised
// every turn, with a close button that looked dead because the next envelope
// re-added the banner. One banner per provider, stable while the signature
// repeats, dismissal remembered per conversation.

function consentEnvelope(turnId, { provider = 'slack', claims, message, tools = [] } = {}) {
  return {
    type: 'chat.step',
    timestamp: '2026-07-07T12:00:00.000Z',
    service: { request_id: `req:${turnId}` },
    conversation: { session_id: 'session-1', conversation_id: 'conv-1', turn_id: turnId },
    event: { step: 'tool.preflight', status: 'completed', title: 'Preflight', agent: null },
    data: {
      error: { code: 'needs_connected_account_consent', message },
      consent: {
        kind: 'delegated_to_kdcube.connected_account',
        provider_id: provider,
        claims,
        url: `/widgets/connections_settings?tab=delegated_to_kdcube&provider_id=${provider}&claims=${claims.join(',')}`,
        action_label: 'Approve access',
        tools,
      },
    },
  }
}

function baseState() {
  return {
    ...initialState,
    conversationId: 'conv-1',
    turns: [createEmptyTurn('turn-1', 1_000, 'hello')],
  }
}

const SEVEN = ['slack:search', 'slack:channels', 'slack:history', 'slack:files:read', 'slack:files:write', 'slack:assistant:search', 'slack:post']

test('a repeated consent state keeps ONE banner with a stable id', () => {
  const first = applyChatStep(baseState(), consentEnvelope('turn-1', { claims: SEVEN, message: 'Connect your Slack account.' }))
  assert.equal(first.banners.length, 1)
  const id = first.banners[0].id
  const second = applyChatStep(first, consentEnvelope('turn-1', { claims: SEVEN, message: 'Connect your Slack account.' }))
  assert.equal(second.banners.length, 1)
  assert.equal(second.banners[0].id, id)
})

test('a NEW consent state for the same provider supersedes the older banner', () => {
  const stale = applyChatStep(baseState(), consentEnvelope('turn-1', { claims: SEVEN, message: 'Connect your Slack account.' }))
  const upgraded = applyChatStep(
    stale,
    consentEnvelope('turn-2', {
      claims: ['slack:files:write', 'slack:post'],
      message: 'Your Slack account is connected but has not approved the required access.',
      tools: ['slack.upload_slack_file', 'slack.post_slack_message'],
    }),
  )
  assert.equal(upgraded.banners.length, 1)
  assert.match(upgraded.banners[0].text, /has not approved/)
  assert.deepEqual(upgraded.banners[0].consentTools, ['slack.upload_slack_file', 'slack.post_slack_message'])
})

test('dismiss removes the banner AND keeps the identical state quiet; a changed claims set shows again', () => {
  const shown = applyChatStep(baseState(), consentEnvelope('turn-1', { claims: ['slack:post'], message: 'Approve slack:post.' }))
  assert.equal(shown.banners.length, 1)
  const dismissed = chatReducer(shown, chatActions.dismissBanner(shown.banners[0].id))
  assert.equal(dismissed.banners.length, 0)

  const reRaised = applyChatStep(dismissed, consentEnvelope('turn-2', { claims: ['slack:post'], message: 'Approve slack:post.' }))
  assert.equal(reRaised.banners.length, 0)

  const changed = applyChatStep(dismissed, consentEnvelope('turn-3', { claims: ['slack:post', 'slack:files:write'], message: 'Approve slack:post and slack:files:write.' }))
  assert.equal(changed.banners.length, 1)
})

test('a new conversation clears the dismissal memory', () => {
  const shown = applyChatStep(baseState(), consentEnvelope('turn-1', { claims: ['slack:post'], message: 'Approve slack:post.' }))
  const dismissed = chatReducer(shown, chatActions.dismissBanner(shown.banners[0].id))
  assert.equal(dismissed.dismissedConsentSignatures.length, 1)
  const fresh = chatReducer(dismissed, chatActions.startNewConversation())
  assert.equal(fresh.dismissedConsentSignatures.length, 0)
})

test('spotlightTools sets and clearToolSpotlight clears the menu request', () => {
  const lit = chatReducer({ ...initialState }, chatActions.spotlightTools(['slack.post_slack_message', ' ', 'slack.upload_slack_file']))
  assert.deepEqual(lit.toolSpotlight.tools, ['slack.post_slack_message', 'slack.upload_slack_file'])
  assert.ok(lit.toolSpotlight.nonce > 0)
  const cleared = chatReducer(lit, chatActions.clearToolSpotlight())
  assert.equal(cleared.toolSpotlight, null)
})
