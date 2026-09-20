import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const SOURCE = readFileSync(
  new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url),
  'utf8',
)
const STYLES = [
  readFileSync(new URL('../examples/standalone/chat-ui.css', import.meta.url), 'utf8'),
  readFileSync(
    new URL(
      '../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget/src/index.css',
      import.meta.url,
    ),
    'utf8',
  ),
]

test('the shared capability picker renders Card exclusion as an immutable third state', () => {
  assert.match(SOURCE, /const notAllowed = authorityState === 'not_allowed'/)
  assert.match(SOURCE, /aria-disabled=\{notAllowed \|\| undefined\}/)
  assert.match(SOURCE, /disabled=\{notAllowed\}/)
  assert.match(SOURCE, />Not permitted<\/span>/)
  assert.match(SOURCE, /data-authority-state=\{authorityState \|\| undefined\}/)
  for (const stylesheet of STYLES) {
    assert.match(stylesheet, /\.k-menu-row-not-allowed \.k-menu-row-main:disabled/)
    assert.match(stylesheet, /\.k-menu-tag-not-allowed/)
  }
})

test('every user-toggleable capability family carries its live authority state', () => {
  for (const expression of [
    'skill.authority_state',
    'target.authority_state',
    'group.authority_state',
    'tool.authority_state',
    'server.authority_state',
    'entry.authority_state',
  ]) {
    assert.ok(SOURCE.includes(expression), `missing ${expression}`)
  }
  const helperAgentsSection = SOURCE.slice(
    SOURCE.indexOf('function HelperAgentsSection'),
    SOURCE.indexOf('function ConnectorsSection'),
  )
  assert.match(helperAgentsSection, /subagentsTogglePatch/)
  assert.match(helperAgentsSection, /authorityState=\{entry\.authority_state\}/)
})
