import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  SCENE_SUBSCRIBE_MESSAGE,
  SCENE_UNSUBSCRIBE_MESSAGE,
  buildSceneSubscriptionMessage,
  buildSceneUnsubscribeMessage,
  bindSceneSubscriptions,
  postSceneSubscriptions,
} from '../dist/scene/index.js'

function target(messages) {
  return {
    postMessage(message, origin) {
      messages.push({ message, origin })
    },
  }
}

test('builds canonical scene subscription messages', () => {
  const message = buildSceneSubscriptionMessage({
    widget: 'usage_card',
    subscriptions: [
      {
        id: 'usage-refresh',
        events: ['accounting.usage'],
        channels: ['chat_service'],
        forward: {
          type: 'kdcube.surface.command',
          target_surface: 'sdk.usage.card',
          action: 'refresh',
        },
        debounceMs: 800,
      },
    ],
  })

  assert.equal(message.type, SCENE_SUBSCRIBE_MESSAGE)
  assert.equal(message.widget, 'usage_card')
  assert.deepEqual(message.subscriptions[0], {
    id: 'usage-refresh',
    source: 'sse',
    events: ['accounting.usage'],
    channels: ['chat_service'],
    forwardType: undefined,
    reason: undefined,
    includeEnvelope: false,
    debounceMs: 800,
    forward: {
      type: 'kdcube.surface.command',
      target_surface: 'sdk.usage.card',
      action: 'refresh',
    },
  })
})

test('drops empty subscription claims', () => {
  const message = buildSceneSubscriptionMessage({
    widget: 'usage_card',
    subscriptions: [
      { id: 'empty' },
      { id: 'valid', events: ['accounting.usage'] },
    ],
  })

  assert.equal(message.subscriptions.length, 1)
  assert.equal(message.subscriptions[0].id, 'valid')
})

test('posts subscribe and unsubscribe messages to a supplied target', () => {
  const messages = []
  const ok = postSceneSubscriptions({
    widget: 'task_tracker_tasks',
    subscriptions: [
      { id: 'task-change', events: ['task_tracker.task.changed'], forwardType: 'kdcube-task-tracker-task-changed' },
    ],
    target: target(messages),
    origin: 'https://scene.example',
  })

  assert.equal(ok, true)
  assert.equal(messages[0].origin, 'https://scene.example')
  assert.equal(messages[0].message.type, SCENE_SUBSCRIBE_MESSAGE)

  const unsubscribe = buildSceneUnsubscribeMessage({ widget: 'task_tracker_tasks' })
  assert.equal(unsubscribe.type, SCENE_UNSUBSCRIBE_MESSAGE)
})

test('bindSceneSubscriptions returns an unsubscribe cleanup', () => {
  const messages = []
  const cleanup = bindSceneSubscriptions({
    widget: 'usage_card',
    subscriptions: [{ id: 'usage-refresh', events: ['accounting.usage'] }],
    target: target(messages),
  })

  assert.equal(messages[0].message.type, SCENE_SUBSCRIBE_MESSAGE)
  const result = cleanup()
  assert.equal(result, true)
  assert.equal(messages[1].message.type, SCENE_UNSUBSCRIBE_MESSAGE)
})
