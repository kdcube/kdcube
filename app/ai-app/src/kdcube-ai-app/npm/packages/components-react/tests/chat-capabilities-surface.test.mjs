import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import {
  CAPABILITIES_SURFACE,
  ackCapabilitiesOpen,
  openCapabilitiesOnHost,
  openConnectionsOnHost,
  parseCapabilitiesOpen,
} from '../../components-core/dist/chat/index.js'

// The `capabilities.open` Agent Card scene contract (the
// connections.hub.open twin): emit shape + command_id ack semantics + the
// honest fallback, pinned at the shared core layer.

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
    {
      agent_id: 'main',
      conversation_id: 'conv-42',
      spotlight_tools: ['slack', ''],
      section: 'services',
    },
    { source: 'agent-card-open', widget: 'workspace_chat', win },
  )
  assert.equal(win.posted.length, 1)
  const command = win.posted[0]
  assert.equal(command.type, 'kdcube.surface.command')
  assert.equal(command.target_surface, CAPABILITIES_SURFACE)
  assert.equal(command.action, 'open')
  assert.equal(command.source, 'agent-card-open')
  assert.equal(command.widget, 'workspace_chat')
  assert.ok(String(command.command_id).startsWith('caps_'))
  assert.deepEqual(command.ui_event, {
    agent_id: 'main',
    conversation_id: 'conv-42',
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
    ui_event: {
      agent_id: 'main',
      conversation_id: 'conv-42',
      spotlight_tools: ['mail', 42, ''],
      section: 'services',
      noise: 'x',
    },
  })
  assert.ok(parsed)
  assert.equal(parsed.commandId, 'caps_1')
  assert.deepEqual(parsed.payload, {
    agent_id: 'main',
    conversation_id: 'conv-42',
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
// Letting the handshake win re-points operations at the outer app. The
// capabilities widget therefore keeps its route-owned app identity.
test('capabilities widget bundle identity: route wins over the host handshake', () => {
  const file = '../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget-capabilities/src/settings.ts'
  const source = readFileSync(new URL(file, import.meta.url), 'utf8')
  const start = source.indexOf('getBundleId()')
  assert.ok(start >= 0, `${file} has getBundleId`)
  const block = source.slice(start, source.indexOf('}', source.indexOf('return isPlaceholder', start)))
  assert.match(block, /if \(context\.bundleId\) return context\.bundleId/)
})

// ---------------------------------------------------------------------------
// `connections.hub.open` from the served capability widget (the same emitter
// family): host-first with ack-wait; the deep link is the caller's fallback.

test('connections open emit targets the hub settings surface without consent', async () => {
  const win = fakeWindow()
  const pending = openConnectionsOnHost(null, { source: 'capabilities-widget', widget: 'capabilities', win })
  assert.equal(win.posted.length, 1)
  const command = win.posted[0]
  assert.equal(command.type, 'kdcube.surface.command')
  assert.equal(command.target_surface, 'connection_hub.settings')
  assert.equal(command.action, 'open')
  assert.equal(command.source, 'capabilities-widget')
  assert.equal(command.widget, 'capabilities')
  assert.ok(String(command.command_id).startsWith('connhub_'))
  assert.equal(command.ui_event, undefined)
  win.receive({ type: 'kdcube.surface.command.ack', command_id: command.command_id, ok: true })
  assert.equal(await pending, true)
})

test('connections open emit carries the consent payload to the connections surface', async () => {
  const win = fakeWindow()
  const pending = openConnectionsOnHost(
    { tab: 'delegated_to_kdcube', params: { provider: 'google', tiers: 'gmail:read' } },
    { win },
  )
  const command = win.posted[0]
  assert.equal(command.target_surface, 'connection_hub.connections')
  assert.deepEqual(command.ui_event, {
    tab: 'delegated_to_kdcube',
    provider: 'google',
    tiers: 'gmail:read',
  })
  win.receive({ type: 'kdcube.surface.command.ack', command_id: command.command_id, ok: false })
  assert.equal(await pending, false)
})

test('connections open falls back on timeout and in standalone contexts', async () => {
  const embedded = fakeWindow()
  assert.equal(await openConnectionsOnHost(null, { win: embedded, timeoutMs: 20 }), false)
  const standalone = fakeWindow({ embedded: false })
  assert.equal(await openConnectionsOnHost(null, { win: standalone }), false)
})

test('the standalone picker fires consent-LESS connection opens (dead-row regression)', () => {
  const source = readFileSync(
    new URL('../src/chat/ui/features/composer/CapabilityPickerStandalone.tsx', import.meta.url),
    'utf8',
  )
  const open = source.slice(source.indexOf('connections: {'))
  assert.match(open, /runtime\.openConnections\?\.\(consent\)/)
  assert.doesNotMatch(open, /if \(consent\) runtime\.openConnections/)
})

test('the capabilities menu opens the exact resident Agent Card when one is known', () => {
  const source = readFileSync(
    new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /const agentCardId = vm\.capabilities\.agentCardId/)
  assert.match(source, /tab: 'delegated_by_kdcube'/)
  assert.match(source, /params: \{ access_id: agentCardId \}/)
})

test('the served widget opens the hub host-first with the deep-link fallback', () => {
  const source = readFileSync(
    new URL('../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget-capabilities/src/App.tsx', import.meta.url),
    'utf8',
  )
  assert.match(source, /openConnectionsOnHost\(/)
  assert.match(source, /window\.open\(connectionsDeepLink\(consent\), '_blank', 'noopener'\)/)
})

test('composer expansion keeps conversation scope while direct widget opens keep Agent Card defaults', () => {
  const composerSource = readFileSync(
    new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url),
    'utf8',
  )
  const widgetSource = readFileSync(
    new URL(
      '../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget-capabilities/src/App.tsx',
      import.meta.url,
    ),
    'utf8',
  )
  assert.match(composerSource, /openCapabilitiesOnHost/)
  assert.match(composerSource, /conversation_id: vm\.state\.conversationId \|\| undefined/)
  assert.match(composerSource, /Saved for this conversation\. Changes apply from your next message\./)
  assert.match(widgetSource, /const conversationRef = useRef\(conversationId\)/)
  assert.match(widgetSource, /conversation_id: conversationRef\.current/)
  assert.match(widgetSource, /caller_surface: 'capabilities_widget'/)
  assert.match(widgetSource, /Choose what the \$\{agentId\} agent may use in this conversation\./)
  assert.match(widgetSource, /Choose the defaults new conversations with the \$\{agentId\} agent start from/)
})

test('each composer open enters a fresh conversation-scoped load before rendering', () => {
  const menu = readFileSync(
    new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url),
    'utf8',
  )
  const opener = menu.slice(
    menu.indexOf('const openForCurrentContext'),
    menu.indexOf('/* A consent banner'),
  )
  assert.ok(opener.indexOf('capabilities.load(') < opener.indexOf('setOpen(true)'))
  assert.match(menu, /openForCurrentContext\('modal'\)/)
  assert.match(menu, /openForCurrentContext\(preferred\)/)
  assert.match(menu, /else openForCurrentContext\('popover'\)/)
  assert.match(menu, /loadOnActivate: false/)
})

test('saved defaults removed from the Control Card stay visible as missing', () => {
  const menu = readFileSync(
    new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url),
    'utf8',
  )
  assert.match(menu, /function MissingCapabilitiesSection/)
  assert.match(menu, /Missing from Control Card/)
  assert.match(menu, /lockLabelOverride="Missing"/)
})

test('picker names conversation provenance and Agent Card defaults separately', () => {
  const menu = readFileSync(
    new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url),
    'utf8',
  )
  assert.match(menu, /inherited \? 'Current default' : 'New default'/)
  assert.match(menu, /inherited \? 'Starting value' : 'This conversation'/)
  assert.match(menu, /Saved for this conversation\. Changes apply from your next message\./)
  assert.match(menu, /Saved as the defaults for new conversations\./)
  assert.match(menu, /changesConversationCapabilities\(patch\)/)
  const capabilityBranch = menu.slice(
    menu.indexOf('if (changesConversationCapabilities(patch))'),
    menu.indexOf("const klass: 'model_switch'", menu.indexOf('if (changesConversationCapabilities(patch))')),
  )
  assert.match(capabilityBranch, /capabilities\.toggle\(patch\)/)
  assert.doesNotMatch(capabilityBranch, /next_conversation|when_cold/)
})

test('agent-base selection is editable inside the Control Card and model choice stays editable', () => {
  const menu = readFileSync(
    new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url),
    'utf8',
  )
  const models = menu.slice(
    menu.indexOf('function ModelsSection'),
    menu.indexOf('function InstructionsSection'),
  )
  assert.match(models, /scopeLocked=\{scope\?\.capabilities_editable === false\}/)
  assert.match(menu, /const editingAgentBase = selectionScope === 'agent_base'/)
  assert.match(menu, /const locked = notAllowed \|\| scopeLocked/)
  assert.doesNotMatch(menu, /outsideAgentBase|outsideConversationBase/)
  assert.match(menu, /These are the Agent Card defaults new conversations start from/)
  assert.match(menu, /started from Agent Card revision \{inheritedCardRevision\}/)
  assert.match(menu, /The Control Card remains its live capability range/)
})

// ---------------------------------------------------------------------------
// The full-page shell owns its scrolling: host embeddings (scene windows,
// the side-panel widget wrapper) size or clip the frame, so document-level
// scrolling cannot be relied on in the widget context.

test('the page shell scrolls itself in both stylesheet twins', () => {
  const sheets = [
    '../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget/src/index.css',
    '../examples/standalone/chat-ui.css',
  ]
  for (const sheet of sheets) {
    const css = readFileSync(new URL(sheet, import.meta.url), 'utf8')
    const start = css.indexOf('.k-menu-page {')
    assert.ok(start >= 0, `${sheet} has .k-menu-page`)
    const block = css.slice(start, css.indexOf('}', start))
    assert.match(block, /height: 100vh/, `${sheet} page shell owns the viewport`)
    assert.match(block, /overflow-y: auto/, `${sheet} page shell scrolls its content`)
    assert.doesNotMatch(block, /min-height: 100vh/, `${sheet} page shell no longer grows past the frame`)
  }
})

test('the surface titles say Capabilities (holds more than tools or skills)', () => {
  const menu = readFileSync(new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url), 'utf8')
  assert.match(menu, /title = 'Capabilities',/)
  assert.doesNotMatch(menu, /Tools &(amp;)? [sS]kills/)
  const app = readFileSync(new URL('../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget-capabilities/src/App.tsx', import.meta.url), 'utf8')
  assert.match(app, /title="Capabilities"/)
  const scene = readFileSync(new URL('../../../../kdcube_ai_app/apps/chat/sdk/examples/bundles/workspace@2026-03-31-13-36/ui/scene/src/sceneConfig.ts', import.meta.url), 'utf8')
  assert.match(scene, /title: 'Capabilities',/)
})

test('the picker keeps a draft behind an explicit Save changes command', () => {
  const menu = readFileSync(new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url), 'utf8')
  assert.match(menu, /capabilities\.saving \? 'Saving…' : 'Save changes'/)
  assert.match(menu, /disabled=\{!capabilities\.dirty/)
  assert.match(menu, /capabilities\.save\(\)/)

  const standalone = readFileSync(new URL('../src/chat/ui/features/composer/CapabilityPickerStandalone.tsx', import.meta.url), 'utf8')
  assert.match(standalone, /setDirty\(true\)/)
  assert.doesNotMatch(standalone, /SAVE_DEBOUNCE_MS/)
})

test('capabilities has NO scene rail chip (per-agent surface, summon-only)', () => {
  const scene = readFileSync(new URL('../../../../kdcube_ai_app/apps/chat/sdk/examples/bundles/workspace@2026-03-31-13-36/ui/scene/src/sceneConfig.ts', import.meta.url), 'utf8')
  const start = scene.indexOf("alias: 'capabilities',")
  const block = scene.slice(start, scene.indexOf('order:', start))
  assert.match(block, /rail: false,/)
})

test('the service card renders declared access requirements with honest affordances', () => {
  const menu = readFileSync(new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url), 'utf8')
  assert.match(menu, /function RequirementLine/)
  assert.match(menu, /realm\?\.requirements \?\? \[\]/)
  // Only a resolved status renders a chip; a url or on-scene surface renders
  // the affordance (summon-first, url new-tab fallback).
  assert.match(menu, /requirement\.status === 'granted'/)
  assert.match(menu, /surface\?\.kind === 'url'/)
  assert.match(menu, /openSurfaceOnHost\(targetSurface/)
})

test('advertised-but-excluded realm entries render greyed with NO toggle and NO consent chip', () => {
  const menu = readFileSync(new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url), 'utf8')
  // The excluded row is a static presentation: no MenuRow/onToggle, no ConsentAside.
  const start = menu.indexOf('function ExcludedEntryRow')
  assert.ok(start >= 0, 'ExcludedEntryRow exists')
  const block = menu.slice(start, menu.indexOf('\nfunction ', start + 10))
  assert.doesNotMatch(block, /MenuRow|onToggle|ConsentAside/)
  assert.match(block, /k-menu-row-excluded/)
  // No admin-speak on the rows: the fix path lives ONCE, in the summary
  // line's tooltip (with the exact descriptor key), never repeated per row.
  assert.doesNotMatch(block, /app admin/)
  const summaryStart = menu.indexOf('function ExcludedSummary')
  const summaryBlock = menu.slice(summaryStart, menu.indexOf('\nfunction ', summaryStart + 10))
  assert.match(summaryBlock, /An app admin can enable these/)
  assert.match(summaryBlock, /namespaces\.\$\{namespace\}\.allowed/)
  // The whole excluded wall now collapses behind ONE quiet line per service.
  assert.match(menu, /function ExcludedSummary/)
  // Excluded entries never contribute toggle keys / namespace state (only the
  // enabled group keys do).
  assert.match(menu, /const entryKeys = groups\.flatMap\(\(group\) => group\.keys\)/)
})

test('the greyed styling exists in both stylesheet twins', () => {
  const sheets = [
    '../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget/src/index.css',
    '../examples/standalone/chat-ui.css',
  ]
  for (const sheet of sheets) {
    const css = readFileSync(new URL(sheet, import.meta.url), 'utf8')
    assert.match(css, /\.k-menu-row-excluded \{ opacity: 0\.75; \}/, `${sheet} greys excluded rows`)
  }
})

test('scene hosts never clamp the hub frame to a reported content height', () => {
  // The surfaced case: in the workspace scene the platform-injected resize
  // reporter (in the scene page) wrote the hub widget's first kdcube-resize
  // height — measured off its brief "Loading…" page — onto the iframe, and
  // the 100vh-bound app then re-measured exactly that clamp forever: admin
  // tabs, tab guide, and the whole panel rendered but stayed clipped.
  // Scene-host windows own their frame size. The hosted app independently
  // tests that its viewport-bound widget opts out of the resize reporter.
  // The stylesheet height
  // outranks any inline style.height a resize listener writes on the iframe.
  const sceneCss = readFileSync(new URL('../src/scene/sceneHost.css', import.meta.url), 'utf8')
  const frameBlock = sceneCss.slice(sceneCss.indexOf('.kdc-frame {'), sceneCss.indexOf('}', sceneCss.indexOf('.kdc-frame {')))
  assert.match(frameBlock, /height: 100% !important/)
})
