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
    {
      agent_id: 'main',
      conversation_id: 'conv-42',
      spotlight_tools: ['slack', ''],
      section: 'services',
    },
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

test('the served widget opens the hub host-first with the deep-link fallback', () => {
  const source = readFileSync(
    new URL('../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget-capabilities/src/App.tsx', import.meta.url),
    'utf8',
  )
  assert.match(source, /openConnectionsOnHost\(/)
  assert.match(source, /window\.open\(connectionsDeepLink\(consent\), '_blank', 'noopener'\)/)
})

test('chat-originated capability windows keep the active conversation scope', () => {
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
  assert.match(composerSource, /conversation_id:\s*vm\.state\.conversationId/)
  assert.match(widgetSource, /conversation_id:\s*conversationRef\.current/)
  assert.match(widgetSource, /Choose what the \$\{agentId\} agent may use in this conversation/)
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

test('the hub access-map admin view is read-only and admin-gated', () => {
  const base = '../../../../kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/ui/widgets/connections/src'
  const slice = readFileSync(new URL(`${base}/features/accessMap/accessMapSlice.ts`, import.meta.url), 'utf8')
  // Read side only: one GET operation, no write op anywhere in the feature.
  assert.match(slice, /getOp<DelegatedAccessMapResult>\('delegated_access_map'\)/)
  assert.doesNotMatch(slice, /postOp/)
  assert.match(slice, /platform_admin_required/)
  const panel = readFileSync(new URL(`${base}/features/accessMap/AccessMapPanel.tsx`, import.meta.url), 'utf8')
  assert.doesNotMatch(panel, /postOp|onSubmit|<form/)
  assert.match(panel, /platform administrators only/)
  // The tab renders only for admins (same gate as the authenticators tab).
  const app = readFileSync(new URL(`${base}/App.tsx`, import.meta.url), 'utf8')
  assert.match(app, /activeTab === 'accessMap' && authenticatorsAllowed/)
})

test('the hub tab strip is a single-row carousel at every width', () => {
  const base = '../../../../kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/ui/widgets/connections/src'
  const css = readFileSync(new URL(`${base}/styles.css`, import.meta.url), 'utf8')
  // Product ruling: the six tabs NEVER wrap into a second row. One line at
  // every width, horizontal scroll for overflow.
  const tabsBlock = css.slice(css.indexOf('.tabs {'), css.indexOf('}', css.indexOf('.tabs {')))
  assert.match(tabsBlock, /flex-wrap: nowrap/)
  assert.match(tabsBlock, /overflow-x: auto/)
  // The overflow affordance is unconditional — no width media-query gate:
  // edge fades render on exactly the overflowing side(s).
  assert.doesNotMatch(css, /@media \(max-width: 479px\)/)
  assert.match(css, /\.tabs-wrap\[data-fade-left\]::before \{ opacity: 1; \}/)
  assert.match(css, /\.tabs-wrap\[data-fade-right\]::after \{ opacity: 1; \}/)
  // The collapse trap: an overflow strip has no automatic minimum height, so
  // inside the viewport-bound page column the wrapper must never shrink.
  assert.match(css, /\.tabs-wrap \{ position: relative; margin: 0 0 14px; flex: 0 0 auto; \}/)
  // The shell keeps the ACTIVE tab in view and tracks overflow edges from a
  // scroll listener + ResizeObserver. The edge predicates guarantee a fully
  // fitting strip shows NO fades: scrollLeft stays 0 (left false) and
  // scrollLeft + clientWidth === scrollWidth (right false).
  const shell = readFileSync(new URL(`${base}/components/AppShell.tsx`, import.meta.url), 'utf8')
  assert.match(shell, /querySelector\('\.tab\.active'\)/)
  assert.match(shell, /scrollIntoView\(\{ block: 'nearest', inline: 'nearest' \}\)/)
  assert.match(shell, /addEventListener\('scroll', updateFade/)
  assert.match(shell, /new ResizeObserver\(updateFade\)/)
  assert.match(shell, /el\.scrollLeft > 1/)
  assert.match(shell, /el\.scrollLeft \+ el\.clientWidth < el\.scrollWidth - 1/)
  assert.match(shell, /data-fade-left=\{fade\.left \|\| undefined\}/)
  assert.match(shell, /data-fade-right=\{fade\.right \|\| undefined\}/)
})

test('scene hosts never clamp the hub frame to a reported content height', () => {
  // The surfaced case: in the workspace scene the platform-injected resize
  // reporter (in the scene page) wrote the hub widget's first kdcube-resize
  // height — measured off its brief "Loading…" page — onto the iframe, and
  // the 100vh-bound app then re-measured exactly that clamp forever: admin
  // tabs, tab guide, and the whole panel rendered but stayed clipped.
  // Guard 1: the viewport-bound widget opts out of the injected reporter
  // (its index.html carries the marker the injector checks for).
  const widgetRoot = '../../../../kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/ui/widgets/connections'
  const indexHtml = readFileSync(new URL(`${widgetRoot}/index.html`, import.meta.url), 'utf8')
  assert.match(indexHtml, /data-kdcube-resize-reporter/)
  // Guard 2: scene-host windows own their frame size — the stylesheet height
  // outranks any inline style.height a resize listener writes on the iframe.
  const sceneCss = readFileSync(new URL('../src/scene/sceneHost.css', import.meta.url), 'utf8')
  const frameBlock = sceneCss.slice(sceneCss.indexOf('.kdc-frame {'), sceneCss.indexOf('}', sceneCss.indexOf('.kdc-frame {')))
  assert.match(frameBlock, /height: 100% !important/)
})

test('the access-map panel body owns its scrolling (viewport-bound page contract)', () => {
  const base = '../../../../kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/ui/widgets/connections/src'
  const css = readFileSync(new URL(`${base}/styles.css`, import.meta.url), 'utf8')
  const block = css.slice(css.indexOf('.access-map-body {'), css.indexOf('}', css.indexOf('.access-map-body {')))
  assert.match(block, /overflow-y: auto/)
  assert.match(block, /min-height: 0/)
  const panel = readFileSync(new URL(`${base}/features/accessMap/AccessMapPanel.tsx`, import.meta.url), 'utf8')
  assert.match(panel, /className="access-map-body"/)
})
