import assert from 'node:assert/strict'
import { test } from 'node:test'
import { submitAgentSelectionUpdate, AUTHORITY_UPDATING } from '../dist/chat/index.js'

const runtime = { baseUrl: 'https://kdcube.test', tenant: 't', project: 'p', bundleId: 'app@1-0', credentials: 'include' }

function answers(...bodies) {
  const calls = []
  globalThis.fetch = async (url, init) => {
    calls.push(JSON.parse(init.body))
    const body = bodies.shift()
    return { ok: body.ok !== false, status: body.ok === false ? 409 : 200, statusText: '', json: async () => body }
  }
  return calls
}

test('a write refused while the permissions are being updated is retried, the same write', async () => {
  const calls = answers({ ok: false, error: AUTHORITY_UPDATING }, { ok: true, agent: 'main', selection: {} })
  const waits = []
  const answer = await submitAgentSelectionUpdate(runtime, 'main', { tools: { web: true } }, {
    conversationId: 'c1', onAuthorityUpdating: (ms) => waits.push(ms), authorityUpdateBudgetMs: 10000,
  })
  assert.equal(answer.ok, true)
  assert.equal(calls.length, 2)
  assert.deepEqual(calls[0], calls[1])
  assert.deepEqual(waits, [2000])
})

test('the retry budget ends in an error carrying the code', async () => {
  answers({ ok: false, error: AUTHORITY_UPDATING })
  await assert.rejects(
    submitAgentSelectionUpdate(runtime, 'main', { tools: { web: true } }, { conversationId: 'c1', authorityUpdateBudgetMs: 0 }),
    (error) => error.code === AUTHORITY_UPDATING,
  )
})
