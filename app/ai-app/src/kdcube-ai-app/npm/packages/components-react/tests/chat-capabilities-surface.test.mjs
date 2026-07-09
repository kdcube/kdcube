import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import {
  CAPABILITIES_SURFACE,
  ackCapabilitiesOpen,
  openCapabilitiesOnHost,
  parseCapabilitiesOpen,
} from '../../components-core/dist/chat/index.js'

// The `capabilities.open` scene contract (the connections.hub.open twin):
// emit shape + command_id ack semantics + the honest fallback, pinned at the
// core layer where every shell (composer popover/modal, served widget) reads it.

function fakeWindow({ embedded = true } = {}) {
  const listeners = new Set()
  const posted = []
  const win = {
    addEventListener: (_type, fn) => listeners.add(fn),
    removeEventListener: (_type, fn) => listeners.delete(fn),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (id) => clearTimeout(id),
    receive(data) {
      listeners.forEach((fn) => fn({ data }))
    },
    posted,
  }
  win.parent = embedded
    ? { postMessage: (message) => posted.push(message) }
    : win
  return win
}

test('emit carries the contract shape and resolves on a positive ack', async () => {
  const win = fakeWindow()
  const pending = openCapabilitiesOnHost(
    { agent_id: 'main', spotlight_tools: ['slack', ''], section: 'services' },
    { source: 'composer-expand', widget: 'workspace_chat', win },
  )
  assert.equal(win.posted.length, 1)
  const command = win.posted[0]
  assert.equal(command.type, 'kdcube.surface.command')
  assert.equal(command.target_surface, CAPABILITIES_SURFACE)
  assert.equal(command.action, 'open')
  assert.equal(command.source, 'composer-expand')
  assert.equal(command.widget, 'workspace_chat')
  assert.ok(String(command.command_id).startsWith('caps_'))
  assert.deepEqual(command.ui_event, {
    agent_id: 'main',
    spotlight_tools: ['slack'],
    section: 'services',
  })
  win.receive({ type: 'kdcube.surface.command.ack', command_id: command.command_id, ok: true })
  assert.equal(await pending, true)
})

test('a negative ack keeps the in-chat presentation', async () => {
  const win = fakeWindow()
  const pending = openCapabilitiesOnHost({}, { win })
  const command = win.posted[0]
  win.receive({ type: 'kdcube.surface.command.ack', command_id: command.command_id, ok: false })
  assert.equal(await pending, false)
})

test('no ack within the window falls back (timeout)', async () => {
  const win = fakeWindow()
  const result = await openCapabilitiesOnHost({}, { win, timeoutMs: 20 })
  assert.equal(result, false)
})

test('a standalone (non-embedded) context falls back immediately', async () => {
  const win = fakeWindow({ embedded: false })
  assert.equal(await openCapabilitiesOnHost({}, { win }), false)
  assert.equal(win.posted.length, 0)
})

test('foreign acks are ignored (command_id semantics)', async () => {
  const win = fakeWindow()
  const pending = openCapabilitiesOnHost({}, { win, timeoutMs: 30 })
  win.receive({ type: 'kdcube.surface.command.ack', command_id: 'someone_else', ok: true })
  assert.equal(await pending, false)
})

test('the widget parses only its own routed command', () => {
  assert.equal(parseCapabilitiesOpen(null), null)
  assert.equal(parseCapabilitiesOpen({ type: 'kdcube.surface.command', target_surface: 'other.surface' }), null)
  assert.equal(
    parseCapabilitiesOpen({ type: 'kdcube.surface.command', target_surface: CAPABILITIES_SURFACE, action: 'close' }),
    null,
  )
  const parsed = parseCapabilitiesOpen({
    type: 'kdcube.surface.command',
    target_surface: 'SDK.Agent.Capabilities',
    action: 'open',
    command_id: 'caps_1',
    ui_event: { agent_id: 'main', spotlight_tools: ['mail', 42, ''], section: 'services', noise: 'x' },
  })
  assert.ok(parsed)
  assert.equal(parsed.commandId, 'caps_1')
  assert.deepEqual(parsed.payload, {
    agent_id: 'main',
    spotlight_tools: ['mail', '42'],
    section: 'services',
  })
})

test('the widget ack echoes the command_id with ok for host diagnostics', () => {
  const win = fakeWindow()
  ackCapabilitiesOpen(
    { targetSurface: CAPABILITIES_SURFACE, commandId: 'caps_9', payload: {} },
    'applied',
    win,
  )
  assert.equal(win.posted.length, 1)
  const ack = win.posted[0]
  assert.equal(ack.type, 'kdcube.surface.command.ack')
  assert.equal(ack.command_id, 'caps_9')
  assert.equal(ack.ok, true)
  assert.equal(ack.reason, 'applied')
})

// A served widget's bundle identity comes from its ROUTE (the bundle URL it
// is served from), never from a host's defaultAppBundleId — embedded scenes
// relay CONFIG_REQUEST to the outer host, whose answer names the OUTER app.
// Letting the handshake win re-pointed every hub operation at a foreign
// bundle (the empty-hub regression). Pinned at source in both widgets that
// carry the settings pattern.
test('widget bundle identity: route wins over the host handshake', () => {
  const settingsFiles = [
    '../../../../kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/ui/widgets/connections/src/api/settings.ts',
    '../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget-capabilities/src/settings.ts',
  ]
  for (const file of settingsFiles) {
    const source = readFileSync(new URL(file, import.meta.url), 'utf8')
    const start = source.indexOf('getBundleId()')
    assert.ok(start >= 0, `${file} has getBundleId`)
    const block = source.slice(start, source.indexOf('}', source.indexOf('return isPlaceholder', start)))
    assert.match(block, /if \(context\.bundleId\) return context\.bundleId/)
  }
})
