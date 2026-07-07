import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

// Surfaced live (compact/embedded chat): the fixed turn-nav column's container
// box swallowed clicks meant for the composer banner's dismiss button — the
// buttons are right-aligned inside a wider flex column, so the transparent
// strip beside them (and the gaps between them) intercepted the pointer.
// Guard the standard overlay pattern in BOTH stylesheets that carry the rule:
// pointer-events none on the passive container, auto on its buttons, and the
// banner row layering above passive overlays.

const STYLESHEETS = [
  new URL('../examples/standalone/chat-ui.css', import.meta.url),
  new URL(
    '../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget/src/index.css',
    import.meta.url,
  ),
]

function ruleBody(css, selector) {
  const start = css.indexOf(`${selector} {`)
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

  test(`turn-nav buttons stay clickable (${label})`, () => {
    assert.match(ruleBody(css, '.k-turn-nav-btn'), /pointer-events:\s*auto/)
  })

  test(`banner row layers above passive overlays (${label})`, () => {
    const body = ruleBody(css, '.k-notice')
    assert.match(body, /position:\s*relative/)
    assert.match(body, /z-index:\s*5/)
  })

  test(`nav band derives from the shared width variable (${label})`, () => {
    // The reserved band is computed from the nav column's own width variable
    // (sized to the Latest pill) — the two stay in sync by construction.
    assert.match(css, /--k-turn-nav-band:\s*calc\(var\(--k-turn-nav-w\)/)
    assert.match(css, /\.k-turn-nav\s*\{\s*width:\s*var\(--k-turn-nav-w\)/)
  })

  test(`full-bleed layouts reserve the nav band while the nav renders (${label})`, () => {
    // Compact and host-embed columns pad the message scroll area and the
    // composer banner strip by the band — message toolbars and banner
    // controls end before the nav column begins. Scoped to
    // .k-has-turn-nav, so single-turn chats keep a clean right edge.
    assert.match(
      css,
      /\.k-has-turn-nav\.k-chat-compact \.k-chat-scroll,\s*\.k-has-turn-nav\.k-embed-bleed \.k-chat-scroll,\s*\.k-has-turn-nav\.k-chat-compact \.k-composer-banners,\s*\.k-has-turn-nav\.k-embed-bleed \.k-composer-banners\s*\{\s*padding-right:\s*var\(--k-turn-nav-band\)/,
    )
  })

  test(`the centered expanded column reserves the band below its clearance width (${label})`, () => {
    // Below 1536px the (viewport − 1320px)/2 centering margin is narrower
    // than the band, so the reservation applies there too.
    assert.match(
      css,
      /@media \(max-width: 1535px\)\s*\{\s*\.k-has-turn-nav \.k-chat-scroll,\s*\.k-has-turn-nav \.k-composer-banners\s*\{\s*padding-right:\s*var\(--k-turn-nav-band\)/,
    )
  })
}
