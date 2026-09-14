import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ConversationSearchControls as focusedControls,
  useConversationSearch as focusedHook,
} from '../dist/chat/search.js'
import {
  ConversationSearchControls as chatControls,
  useConversationSearch as chatHook,
} from '../dist/chat/index.js'

test('conversation search is available from the chat and focused search entries', () => {
  assert.equal(typeof focusedControls, 'function')
  assert.equal(typeof focusedHook, 'function')
  assert.equal(chatControls, focusedControls)
  assert.equal(chatHook, focusedHook)
})
