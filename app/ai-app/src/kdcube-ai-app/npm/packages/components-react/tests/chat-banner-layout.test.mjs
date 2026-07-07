import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

// Banner layout contract, pinned in BOTH stylesheets: wide containers keep
// the single row; below 600px (the measured width where text + the two
// consent actions + dismiss stop fitting on one row) the banner stacks —
// full-width text, actions on their own wrapping row, dismiss pinned
// top-right. Claims render as compact code chips after the sentence.

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

  test(`banner strip is a size container (${label})`, () => {
    assert.match(css, /\.k-banner-strip\s*\{\s*container-type:\s*inline-size/)
  })

  test(`narrow containers stack the banner (${label})`, () => {
    const block = css.slice(css.indexOf('@container (max-width: 599px)'))
    assert.notEqual(block.length, 0, 'the narrow-container block exists')
    // text goes full-width and clears the pinned dismiss
    assert.match(block, /\.k-banner-strip \.k-notice\s*\{\s*display:\s*block;\s*padding-right:\s*34px/)
    // actions drop to their own row
    assert.match(block, /\.k-banner-strip \.k-banner-actions\s*\{\s*margin-top:\s*8px/)
    // dismiss pins top-right of the (position: relative) banner
    assert.match(block, /\.k-banner-strip \.k-banner-dismiss\s*\{\s*position:\s*absolute;\s*top:\s*5px;\s*right:\s*5px/)
  })

  test(`claim chips are compact code chips (${label})`, () => {
    assert.match(css, /\.k-banner-claim\s*\{[^}]*font-family:\s*ui-monospace/)
    assert.match(css, /\.k-banner-claims\s*\{[^}]*flex-wrap:\s*wrap/)
  })
}
