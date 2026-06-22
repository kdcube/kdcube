import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createSceneEventBus } from '../dist/scene/index.js'

test('dispatches matching scene events to registered widget claims', () => {
  const delivered = []
  const bus = createSceneEventBus({
    getAliases: () => ['usage_card'],
    isReady: () => true,
    post: (alias, message) => delivered.push({ alias, message }),
    now: () => '2026-06-19T00:00:00Z',
  })

  bus.register('usage_card', [
    {
      id: 'usage',
      source: 'sse',
      events: ['accounting.usage'],
      channels: ['chat_service'],
      forward: {
        type: 'kdcube.surface.command',
        target_surface: 'sdk.usage.card',
        action: 'refresh',
      },
      reason: 'accounting.usage',
    },
  ])

  const count = bus.publish(bus.normalizeEvent('sse', { type: 'chat_service' }, { type: 'accounting.usage' }))

  assert.equal(count, 1)
  assert.equal(delivered[0].alias, 'usage_card')
  assert.equal(delivered[0].message.type, 'kdcube.surface.command')
  assert.equal(delivered[0].message.target_surface, 'sdk.usage.card')
  assert.equal(delivered[0].message.action, 'refresh')
  assert.deepEqual(delivered[0].message.scene_event, {
    source: 'sse',
    channel: 'chat_service',
    type: 'accounting.usage',
    ts: '2026-06-19T00:00:00Z',
  })
})

test('queues events when the target alias is not ready', () => {
  const queued = []
  const posted = []
  const bus = createSceneEventBus({
    getAliases: () => ['task_list'],
    isReady: () => false,
    post: (alias, message) => posted.push({ alias, message }),
    queue: (alias, message) => queued.push({ alias, message }),
  })

  bus.register('task_list', [
    { id: 'task-change', events: ['task_tracker.task.changed'], channels: ['chat_service'] },
  ])

  const count = bus.publish(bus.normalizeEvent('sse', { type: 'chat_service' }, { type: 'task_tracker.task.changed' }))

  assert.equal(count, 1)
  assert.equal(posted.length, 0)
  assert.equal(queued.length, 1)
})

test('does not dispatch until a widget registers explicit claims', () => {
  const delivered = []
  const bus = createSceneEventBus({
    getAliases: () => ['usage_card'],
    post: (alias, message) => delivered.push({ alias, message }),
  })

  bus.publish(bus.normalizeEvent('sse', { type: 'message' }, { type: 'accounting.usage' }))
  assert.equal(delivered.length, 0)

  bus.register('usage_card', [
    { id: 'explicit-usage', events: ['accounting.usage'], channels: ['message'] },
  ])
  bus.publish(bus.normalizeEvent('sse', { type: 'message' }, { type: 'accounting.usage' }))
  assert.equal(delivered.length, 1)
})

test('debounces high-frequency matching events', () => {
  const delivered = []
  const scheduled = []
  const bus = createSceneEventBus({
    getAliases: () => ['usage_card'],
    post: (alias, message) => delivered.push({ alias, message }),
    setTimeout: (handler, timeout) => {
      const timer = { handler, timeout, active: true }
      scheduled.push(timer)
      return timer
    },
    clearTimeout: (timer) => {
      timer.active = false
    },
  })

  bus.register('usage_card', [
    { id: 'usage', events: ['accounting.usage'], channels: ['chat_service'], debounceMs: 800 },
  ])

  bus.publish(bus.normalizeEvent('sse', { type: 'chat_service' }, { type: 'accounting.usage' }))
  bus.publish(bus.normalizeEvent('sse', { type: 'chat_service' }, { type: 'accounting.usage' }))

  assert.equal(delivered.length, 0)
  assert.equal(scheduled.length, 2)
  assert.equal(scheduled[0].active, false)
  scheduled[1].handler()
  assert.equal(delivered.length, 1)
})
