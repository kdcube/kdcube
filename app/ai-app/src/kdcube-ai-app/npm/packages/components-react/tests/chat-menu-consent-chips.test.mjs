import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

// Composer-menu consent decoration contract, pinned in BOTH stylesheets:
// one chip family (status tags + the Consent action button) with the flat
// kdcube corner radius, a fixed compact height, and self-centering inside
// the row flexbox. `.k-menu-row` stretches its children (align-items:
// stretch), which once turned the "connected" tag into a row-height blob
// next to two-line tool rows — `align-self: center` + `height` is the
// regression pin. State is color; the family metrics stay shared.

const STYLESHEETS = [
  new URL('../examples/standalone/chat-ui.css', import.meta.url),
  new URL(
    '../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget/src/index.css',
    import.meta.url,
  ),
]

for (const sheet of STYLESHEETS) {
  const label = sheet.pathname.split('/').slice(-3).join('/')
  const css = readFileSync(sheet, 'utf8')

  const familyStart = css.indexOf('.k-menu-tag,\n.k-menu-consent {')
  const familyBlock = familyStart >= 0 ? css.slice(familyStart, css.indexOf('}', familyStart)) : ''

  test(`tag and consent button share one family block (${label})`, () => {
    assert.ok(familyBlock.length > 0, 'the shared .k-menu-tag, .k-menu-consent block exists')
  })

  test(`chips are flat, not pills (${label})`, () => {
    assert.match(familyBlock, /border-radius:\s*4px/)
    // no 999px pill radius anywhere in the family
    assert.doesNotMatch(familyBlock, /border-radius:\s*999px/)
  })

  test(`chips center in the stretching row flexbox at a fixed height (${label})`, () => {
    // .k-menu-row is align-items: stretch; without these two the aside tag
    // stretches to the full two-line row height (the "grey blob").
    assert.match(css, /\.k-menu-row\s*\{[^}]*align-items:\s*stretch/)
    assert.match(familyBlock, /align-self:\s*center/)
    assert.match(familyBlock, /flex:\s*0 0 auto/)
    assert.match(familyBlock, /height:\s*18px/)
  })

  test(`one type treatment across the family (${label})`, () => {
    assert.match(familyBlock, /font-size:\s*10px/)
    assert.match(familyBlock, /text-transform:\s*uppercase/)
    assert.match(familyBlock, /white-space:\s*nowrap/)
  })

  test(`state is color: quiet tag, accent action (${label})`, () => {
    // status tag stays quiet by default (family block carries muted color)
    assert.match(familyBlock, /color:\s*var\(--muted\)/)
    // the Consent ACTION is a button: accent + pointer, no metric overrides
    const consentStart = css.lastIndexOf('.k-menu-consent {')
    const consentBlock = css.slice(consentStart, css.indexOf('}', consentStart))
    assert.match(consentBlock, /color:\s*var\(--accent\)/)
    assert.match(consentBlock, /cursor:\s*pointer/)
    assert.doesNotMatch(consentBlock, /height:|font-size:|border-radius:/)
  })

  test(`long tool names truncate; chips keep their width (${label})`, () => {
    // the row main flexes and may shrink; the label ellipsizes
    assert.match(css, /\.k-menu-row-main\s*\{[^}]*flex:\s*1 1 auto/)
    assert.match(css, /\.k-menu-row-main\s*\{[^}]*min-width:\s*0/)
    assert.match(css, /\.k-menu-row-label\s*\{[^}]*text-overflow:\s*ellipsis/)
  })
}

// Expanded-namespace internals: informational rows share the toggle row's
// geometry and truncation without its affordance; the chip family applies
// unchanged to claims-bearing entries.
for (const sheet of STYLESHEETS) {
  const label = sheet.pathname.split('/').slice(-3).join('/')
  const css = readFileSync(sheet, 'utf8')

  test(`namespace internals rows keep row geometry without the toggle (${label})`, () => {
    const start = css.indexOf('.k-menu-row-static {')
    assert.ok(start >= 0, '.k-menu-row-static exists')
    const block = css.slice(start, css.indexOf('}', start))
    assert.match(block, /flex:\s*1 1 auto/)
    assert.match(block, /min-width:\s*0/)
    assert.doesNotMatch(block, /cursor:/)
  })
}

// The expanded namespace reads as a SERVICE CARD: human entry titles with the
// grammar token demoted to a small mono hint, and quiet card lines for the
// third-party dependency / object kinds / honest not-yet-described state.
for (const sheet of STYLESHEETS) {
  const label = sheet.pathname.split('/').slice(-3).join('/')
  const css = readFileSync(sheet, 'utf8')

  test(`grammar tokens demote to a small mono hint (${label})`, () => {
    const start = css.indexOf('.k-menu-entry-token {')
    assert.ok(start >= 0, '.k-menu-entry-token exists')
    const block = css.slice(start, css.indexOf('}', start))
    assert.match(block, /font-family:\s*ui-monospace/)
    assert.match(block, /color:\s*var\(--muted\)/)
    // plain hint, no chip dressing
    assert.match(block, /background:\s*none/)
    assert.match(block, /border:\s*0/)
  })

  test(`service card lines are quiet one-liners that ellipsize (${label})`, () => {
    const start = css.indexOf('.k-menu-card-line {')
    assert.ok(start >= 0, '.k-menu-card-line exists')
    const block = css.slice(start, css.indexOf('}', start))
    assert.match(block, /text-overflow:\s*ellipsis/)
    assert.match(block, /white-space:\s*nowrap/)
    assert.match(block, /color:\s*var\(--muted\)/)
  })
}
