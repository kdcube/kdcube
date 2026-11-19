// kdcube#270 F11: a conversation may turn a delegated resource family (the user's own MCP
// connectors) off and back on, like a skill; the draft merge keeps the latest toggle.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { applySelectionPatch, mergeSelectionPatches } from '../dist/chat/index.js'

test('a family toggle turns it off and back on in the deny map', () => {
  const off = applySelectionPatch({}, { delegated_resource_families: { user_external_mcp: true } })
  assert.deepEqual(off.delegated_resource_families, ['user_external_mcp'])
  const kept = applySelectionPatch(off, { skills: { 'pdf-press': true } })
  assert.deepEqual(kept.delegated_resource_families, ['user_external_mcp'])
  const on = applySelectionPatch(kept, { delegated_resource_families: { user_external_mcp: false } })
  assert.equal('delegated_resource_families' in on, false)
})

test('merged drafts keep the latest family toggle', () => {
  const merged = mergeSelectionPatches(
    { delegated_resource_families: { user_external_mcp: true } },
    { delegated_resource_families: { user_external_mcp: false } },
  )
  assert.deepEqual(merged.delegated_resource_families, { user_external_mcp: false })
})
