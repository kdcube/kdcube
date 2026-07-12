import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import {
  applySelectionPatch,
  isSubagentsDisabled,
  subagentsTogglePatch,
} from '../../components-core/dist/chat/index.js'

// The helper-agents row (ReAct subagents v2, the user's decision) and the
// subagent thread block. The row rides the shared picker body — one body,
// three shells (composer popover, expanded modal, served capabilities
// widget) — so pinning the section registration covers all three.

const MENU_SOURCE = readFileSync(
  new URL('../src/chat/ui/features/composer/ComposerMenu.tsx', import.meta.url),
  'utf8',
)

test('the helper-agents row rides the shared picker body via the sections registry', () => {
  assert.match(MENU_SOURCE, /function HelperAgentsSection/)
  assert.match(MENU_SOURCE, /capabilitySection\('subagents', 45, HelperAgentsSection\)/)
})

test('entry absent -> no row; copy comes from the payload, never hardcoded prose', () => {
  const section = MENU_SOURCE.slice(
    MENU_SOURCE.indexOf('function HelperAgentsSection'),
    MENU_SOURCE.indexOf('\nfunction ', MENU_SOURCE.indexOf('function HelperAgentsSection') + 10),
  )
  // gate: the inventory entry must be present AND available
  assert.match(section, /inventory\.subagents/)
  assert.match(section, /if \(!entry\?\.available\) return null/)
  // label + description render from the payload
  assert.match(section, /entry\.label/)
  assert.match(section, /sub=\{entry\.description/)
  // the description (trade-off copy) is server-owned: no cost/quality prose here
  assert.doesNotMatch(section, /billed|model calls|quality/i)
  // the toggle goes through the shared selection flow with the deny-key patch
  assert.match(section, /toggle\(subagentsTogglePatch\(disabled\)\)/)
  assert.match(section, /isSubagentsDisabled\(disabled\)/)
  // a deferred change badges the row like every other category
  assert.match(section, /pending\?\.disabled\?\.subagents/)
})

test('the toggle patch is the subagents deny key in the standard patch shape', () => {
  assert.deepEqual(subagentsTogglePatch({}), { subagents: true })
  const off = applySelectionPatch({}, subagentsTogglePatch({}))
  assert.equal(isSubagentsDisabled(off), true)
  assert.deepEqual(subagentsTogglePatch(off), { subagents: false })
  assert.deepEqual(applySelectionPatch(off, subagentsTogglePatch(off)), {})
})

const THREADS_SOURCE = readFileSync(
  new URL('../src/chat/ui/features/chat/SubagentThreads.tsx', import.meta.url),
  'utf8',
)
const SHELL_SOURCE = readFileSync(
  new URL('../src/chat/ui/ChatShell.tsx', import.meta.url),
  'utf8',
)

test('threads anchor inline under their fork turn with the same rendering pipeline', () => {
  // anchored per turn in the message flow (no reserved columns/widths)
  assert.match(SHELL_SOURCE, /subagentThreadsForTurn\(state\.threads, turn\.id\)/)
  assert.match(SHELL_SOURCE, /<SubagentThreads/)
  // child turns render through the SAME ChatTurnView pipeline, nested
  assert.match(THREADS_SOURCE, /<ChatTurnView/)
  // collapsed header: charter goal + status + contribution milestones
  assert.match(THREADS_SOURCE, /charterGoal/)
  assert.match(THREADS_SOURCE, /ThreadStatusChip/)
  assert.match(THREADS_SOURCE, /contributions/)
  // expanding a reload stub fetches the child conversation
  assert.match(THREADS_SOURCE, /loadThread\(thread\.childConversationId\)/)
  assert.match(SHELL_SOURCE, /engine\.loadSubagentThread/)
})

test('the thread header names the delegating agent\'s chosen persona (fix C)', () => {
  // the persona name comes from the stamp/reload agent_title, not a hardcoded string
  assert.match(THREADS_SOURCE, /thread\.agentTitle \|\| 'Helper agent'/)
  assert.match(THREADS_SOURCE, /k-subthread-kicker">\{personaName\}/)
  // the old hardcoded "Helper agent" kicker literal is gone
  assert.doesNotMatch(THREADS_SOURCE, /k-subthread-kicker">Helper agent</)
})

const TURNVIEW_SOURCE = readFileSync(
  new URL('../src/chat/ui/features/chat/TurnView.tsx', import.meta.url),
  'utf8',
)

test('an agent-authored continuation turn renders as the helper persona, not "You" (fix B)', () => {
  // the turn's triggering-input persona is read from the state model
  assert.match(TURNVIEW_SOURCE, /turn\.authoredBy/)
  // the persona name replaces "You"; the handoff renders as "<name> said: …"
  assert.match(TURNVIEW_SOURCE, /agentTitle \|\| 'Helper agent'/)
  assert.match(TURNVIEW_SOURCE, /\{personaName\} said:/)
  // no contribution -> persona with a neutral line, never the raw event or "You"
  assert.match(TURNVIEW_SOURCE, /agentPersona\.handoff \?/)
})

const STYLESHEETS = [
  new URL('../examples/standalone/chat-ui.css', import.meta.url),
  new URL(
    '../../../../kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget/src/index.css',
    import.meta.url,
  ),
]

test('both stylesheet twins carry the subagent thread styles', () => {
  for (const href of STYLESHEETS) {
    const css = readFileSync(href, 'utf8')
    assert.match(css, /\.k-subthread\b/, `${href} missing .k-subthread`)
    assert.match(css, /\.k-subthread-milestone\b/, `${href} missing .k-subthread-milestone`)
    assert.match(css, /\.k-subthread-body\b/, `${href} missing .k-subthread-body`)
  }
})
