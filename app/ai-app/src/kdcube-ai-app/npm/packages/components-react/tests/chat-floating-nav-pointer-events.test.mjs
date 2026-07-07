import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

// The floating turn-nav is a TRANSIENT overlay: the conversation owns the
// full column, so the nav never reserves space. Two rules make its overlap
// harmless, pinned here in BOTH stylesheets that carry them:
//   1. pointer-events pattern — the passive container is click-transparent;
//      only its buttons take clicks (surfaced: the container box swallowed
//      the composer banner's dismiss in compact view);
//   2. idle-fade presence — after idle the nav is opacity 0 AND its buttons
//      drop pointer-events, so faded buttons never intercept the message
//      copy controls beneath; keyboard focus always shows it back.
// Space reservation (padding-right bands) is rejected by design: the fade,
// never the layout, resolves the overlap.

const STYLESHEETS = [
  new URL('../examples/standalone/chat-ui.css', import.meta.url),
  new URL(
    '../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget/src/index.css',
    import.meta.url,
  ),
]

function ruleBody(css, selector) {
  // Anchor at a line start so `.k-turn-nav-btn` finds its own rule rather
  // than the `.k-turn-nav-idle .k-turn-nav-btn` descendant rule.
  const needle = `\n${selector} {`
  const start = css.startsWith(`${selector} {`) ? 0 : css.indexOf(needle)
  assert.notEqual(start, -1, `rule ${selector} is declared`)
  const open = css.indexOf('{', start)
  const close = css.indexOf('}', open)
  return css.slice(open + 1, close)
}

for (const sheet of STYLESHEETS) {
  const label = sheet.pathname.split('/').slice(-3).join('/')
  const css = readFileSync(sheet, 'utf8')

  test(`floating turn-nav container is click-transparent (${label})`, () => {
    assert.match(ruleBody(css, '.k-turn-nav'), /pointer-events:\s*none/)
  })

  test(`turn-nav buttons stay clickable while visible (${label})`, () => {
    assert.match(ruleBody(css, '.k-turn-nav-btn'), /pointer-events:\s*auto/)
  })

  test(`turn-nav fades via a pure opacity transition (${label})`, () => {
    assert.match(ruleBody(css, '.k-turn-nav'), /transition:\s*opacity\s*200ms/)
  })

  test(`idle turn-nav is fully hidden AND click-transparent (${label})`, () => {
    // The visible/hidden pair: hidden = opacity 0 coupled with the BUTTONS
    // dropping pointer-events, so a faded nav never intercepts the copy
    // controls beneath it.
    assert.match(ruleBody(css, '.k-turn-nav-idle'), /opacity:\s*0/)
    assert.match(ruleBody(css, '.k-turn-nav-idle .k-turn-nav-btn'), /pointer-events:\s*none/)
  })

  test(`keyboard focus always shows the turn-nav (${label})`, () => {
    assert.match(ruleBody(css, '.k-turn-nav-idle:focus-within'), /opacity:\s*1/)
    assert.match(ruleBody(css, '.k-turn-nav-idle:focus-within .k-turn-nav-btn'), /pointer-events:\s*auto/)
  })

  test(`no space reservation for the nav remains (${label})`, () => {
    assert.doesNotMatch(css, /--k-turn-nav-band/)
    assert.doesNotMatch(css, /k-has-turn-nav/)
    assert.doesNotMatch(css, /k-embed-bleed/)
  })

  test(`banner row layers above passive overlays (${label})`, () => {
    const body = ruleBody(css, '.k-notice')
    assert.match(body, /position:\s*relative/)
    assert.match(body, /z-index:\s*5/)
  })
}
