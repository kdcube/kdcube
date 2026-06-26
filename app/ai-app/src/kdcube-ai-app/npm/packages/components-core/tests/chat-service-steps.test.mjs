import assert from 'node:assert/strict'
import { test } from 'node:test'
import { projectServiceEventToChatStep } from '../dist/chat/index.js'

function serviceEnvelope(type, step = type) {
  return {
    type,
    timestamp: '2026-06-25T00:00:00.000Z',
    service: { request_id: 'req-1' },
    conversation: {
      session_id: 'session-1',
      conversation_id: 'conv-1',
      turn_id: 'turn-1',
    },
    event: {
      step,
      status: 'completed',
      title: 'Service Event',
      agent: 'react.tool',
    },
    data: {
      tool_id: 'react.rg',
      tool_call_id: 'tc_rg',
    },
  }
}

test('projects react tool service events into chat steps', () => {
  const projected = projectServiceEventToChatStep(serviceEnvelope('react.tool.result', 'react.tool.result.tc_rg'))

  assert.ok(projected)
  assert.equal(projected.type, 'chat.step')
  assert.equal(projected.event.step, 'react.tool.result.tc_rg')
  assert.equal(projected.data.service_event_type, 'react.tool.result')
  assert.equal(projected.data.tool_call_id, 'tc_rg')
})

test('projects react tool call service events into chat steps', () => {
  const projected = projectServiceEventToChatStep(serviceEnvelope('react.tool.call', 'react.tool.call.tc_rg'))

  assert.ok(projected)
  assert.equal(projected.type, 'chat.step')
  assert.equal(projected.event.step, 'react.tool.call.tc_rg')
  assert.equal(projected.data.service_event_type, 'react.tool.call')
})

test('projects rejected react tool service events into chat steps', () => {
  const projected = projectServiceEventToChatStep(serviceEnvelope('react.tool.rejected', 'react.tool.rejected.tc_bad'))

  assert.ok(projected)
  assert.equal(projected.type, 'chat.step')
  assert.equal(projected.event.step, 'react.tool.rejected.tc_bad')
  assert.equal(projected.data.service_event_type, 'react.tool.rejected')
})

test('does not project unrelated service events into chat steps', () => {
  assert.equal(projectServiceEventToChatStep(serviceEnvelope('accounting.usage', 'accounting')), null)
})
