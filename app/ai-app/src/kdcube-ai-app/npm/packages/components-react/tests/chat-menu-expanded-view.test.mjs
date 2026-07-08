import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { preferredMenuPresentation } from '../../components-core/dist/chat/index.js'

// The capability picker is ONE component with two presentations: the compact
// popover (quick toggles) and the wide canvas-modal shell where service-card
// prose wraps. These pin the presentation-switch contract at the rig's layer.

const STYLESHEETS = [
  new URL('../examples/standalone/chat-ui.css', import.meta.url),
  new URL(
    '../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget/src/index.css',
    import.meta.url,
  ),
]

const INVENTORY = {
  named_services: [
    { namespace: 'mail', alias: 'named_services' },
    { namespace: 'slack', alias: 'named_services' },
  ],
}

test('spotlight on a namespace entry prefers the expanded view', () => {
  assert.equal(preferredMenuPresentation(['slack'], INVENTORY), 'modal')
  assert.equal(preferredMenuPresentation(['named_services'], INVENTORY), 'modal')
})

test('a short dedicated-tool spotlight stays in the popover', () => {
  assert.equal(preferredMenuPresentation(['slack_tools.search_slack'], INVENTORY), 'popover')
  assert.equal(preferredMenuPresentation([], INVENTORY), 'popover')
  assert.equal(preferredMenuPresentation(undefined, INVENTORY), 'popover')
})

test('a long target list prefers the expanded view', () => {
  assert.equal(preferredMenuPresentation(['a.b', 'a.c', 'a.d', 'a.e'], INVENTORY), 'modal')
})

const SOURCE = readFileSync(
  new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url),
  'utf8',
)

test('one body renders into every shell (shared interaction state)', () => {
  // The body node comes from useCapabilityPickerBody above the shells:
  // popover, in-chat modal, and the served full-page widget.
  const bodyRenders = SOURCE.match(/\{body\}/g) ?? []
  assert.equal(bodyRenders.length, 3, 'the same {body} mounts in the popover, the modal, and the page')
  // ...and the confirm picker re-anchors when the presentation switches.
  assert.match(SOURCE, /\[confirmState, presentation\]/)
})

test('expand and collapse affordances are present', () => {
  assert.match(SOURCE, /CanvasExpandButton onClick=\{\(\) => setView\('modal'\)\}/)
  assert.match(SOURCE, /aria-label="Collapse to menu"/)
  assert.match(SOURCE, /aria-label="Close \(Esc\)"/)
})

for (const sheet of STYLESHEETS) {
  const label = sheet.pathname.split('/').slice(-3).join('/')
  const css = readFileSync(sheet, 'utf8')

  test(`expanded view wraps prose instead of ellipsizing (${label})`, () => {
    const start = css.indexOf('.k-menu-expanded .k-menu-row-sub')
    assert.ok(start >= 0, 'expanded wrap block exists')
    const block = css.slice(start, css.indexOf('}', start))
    assert.match(block, /white-space:\s*normal/)
    assert.match(block, /overflow:\s*visible/)
    assert.match(block, /text-overflow:\s*clip/)
  })

  test(`popover head carries the expand affordance styling (${label})`, () => {
    assert.ok(css.includes('.k-menu-head {'), '.k-menu-head exists')
    assert.ok(css.includes('.k-menu-head-label'), '.k-menu-head-label exists')
  })

  test(`modal body is a readable scrolling column (${label})`, () => {
    const start = css.indexOf('.k-menu-modal-body')
    assert.ok(start >= 0)
    const block = css.slice(start, css.indexOf('}', start))
    assert.match(block, /overflow:\s*auto/)
  })
}

// Third shell: the served capability widget renders the SAME body full-page.
test('the full-page shell reuses the shared picker body', () => {
  assert.match(SOURCE, /export function useCapabilityPickerBody/)
  assert.match(SOURCE, /export function CapabilityPickerPage/)
  // The page presentation drives the hook with active: true.
  const pageStart = SOURCE.indexOf('export function CapabilityPickerPage')
  const pageBlock = SOURCE.slice(pageStart, SOURCE.indexOf('export function ComposerMenu'))
  assert.match(pageBlock, /useCapabilityPickerBody\(/)
  assert.match(pageBlock, /active: true/)
  assert.match(pageBlock, /k-menu-expanded/)
  assert.match(pageBlock, /k-menu-page/)
})

test('the standalone vm reuses the shared selection logic, not a fork', () => {
  const standalone = readFileSync(
    new URL('../src/chat/ui/features/composer/CapabilityPickerStandalone.tsx', import.meta.url),
    'utf8',
  )
  assert.match(standalone, /applySelectionPatch/)
  assert.match(standalone, /mergeSelectionPatches/)
  assert.match(standalone, /useStandaloneCapabilitiesVm/)
})

for (const sheet of STYLESHEETS) {
  const label = sheet.pathname.split('/').slice(-3).join('/')
  const css = readFileSync(sheet, 'utf8')

  test(`full-page shell styles exist with the readable column (${label})`, () => {
    assert.ok(css.includes('.k-menu-page {'), '.k-menu-page exists')
    const start = css.indexOf('.k-menu-page .k-menu-expanded')
    assert.ok(start >= 0, 'page column bounds the expanded body')
    const block = css.slice(start, css.indexOf('}', start))
    assert.match(block, /max-width:\s*920px/)
  })
}
