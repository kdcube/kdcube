import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  conversationIdFromContextItem,
  conversationIdFromConversationRef,
  conversationIdFromSurfaceCommand,
} from './conversationCommands'

const SURFACE_COMMAND = 'kdcube.surface.command'

test('short conv ref parses to its conversation id', () => {
  assert.equal(conversationIdFromConversationRef('conv:42d5a4e0abc'), '42d5a4e0abc')
})

test('full positional conv ref parses to its LAST segment', () => {
  // conv:<tenant>/<project>/<user>/<bundle>/<agent>/<conversation_id>
  assert.equal(
    conversationIdFromConversationRef('conv:demo/demo/user-1/workspace@2026-03-31-13-36/main/42d5a4e0abc'),
    '42d5a4e0abc',
  )
  // The surfaced regression: the 3-segment ref shown on the pin card.
  assert.equal(conversationIdFromConversationRef('conv:demo/demo/42d5a4e0abc'), '42d5a4e0abc')
})

test('conv file refs and non-conv refs never parse as conversations', () => {
  assert.equal(conversationIdFromConversationRef('conv:fi:conv_x.turn_y.attachment/report.pdf'), '')
  assert.equal(conversationIdFromConversationRef('mem:record/1'), '')
  assert.equal(conversationIdFromConversationRef('conv:'), '')
  assert.equal(conversationIdFromConversationRef('conv:demo/demo/id with spaces'), '')
})

test('pin-board routed open (ui_event carries conversation_id) loads the conversation', () => {
  // The exact shape the pin board forwards: resolver ui_event verbatim,
  // top-level object_ref is the full conv ref.
  const id = conversationIdFromSurfaceCommand({
    type: SURFACE_COMMAND,
    target_surface: 'sdk.chat.viewer',
    action: 'open',
    object_ref: 'conv:demo/demo/42d5a4e0abc',
    ui_event: {
      type: 'kdcube.ui.object.open.requested',
      target_surface: 'sdk.chat.viewer',
      conversation_id: '42d5a4e0abc',
      object_ref: 'conv:demo/demo/42d5a4e0abc',
    },
  })
  assert.equal(id, '42d5a4e0abc')
})

test('open command with only a full conv object_ref still resolves', () => {
  const id = conversationIdFromSurfaceCommand({
    type: SURFACE_COMMAND,
    target_surface: 'sdk.chat.conversation',
    action: 'open',
    object_ref: 'conv:demo/demo/user-1/bundle/main/42d5a4e0abc',
  })
  assert.equal(id, '42d5a4e0abc')
})

test('provider-open dispatch (conversation_id spread top-level) resolves', () => {
  const id = conversationIdFromSurfaceCommand({
    type: SURFACE_COMMAND,
    target_surface: 'sdk.chat.viewer',
    action: 'open',
    conversation_id: '42d5a4e0abc',
  })
  assert.equal(id, '42d5a4e0abc')
})

test('attach-as-context commands are NOT conversation opens', () => {
  // sdk.chat.context attach must keep attaching, never switch conversations.
  const id = conversationIdFromSurfaceCommand({
    type: SURFACE_COMMAND,
    target_surface: 'sdk.chat.context',
    action: 'attach',
    object_ref: 'conv:demo/demo/42d5a4e0abc',
    context: { kind: 'conversation', ref: 'conv:demo/demo/42d5a4e0abc' },
  })
  assert.equal(id, '')
})

test('conversation-surface command with a conv:fi file ref is ignored', () => {
  const id = conversationIdFromSurfaceCommand({
    type: SURFACE_COMMAND,
    target_surface: 'sdk.chat.viewer',
    action: 'open',
    object_ref: 'conv:fi:conv_a.turn_b.attachment/report.pdf',
  })
  assert.equal(id, '')
})

test('dropped conversation pin context item resolves via kind + ref', () => {
  assert.equal(
    conversationIdFromContextItem({
      kind: 'conversation',
      ref: 'conv:demo/demo/42d5a4e0abc',
      label: 'Some chat',
    }),
    '42d5a4e0abc',
  )
  // Explicit data.conversation_id wins over the ref parse.
  assert.equal(
    conversationIdFromContextItem({
      kind: 'chat.conversation',
      ref: 'conv:demo/demo/42d5a4e0abc',
      data: { conversation_id: 'explicit-id' },
    }),
    'explicit-id',
  )
  // Non-conversation kinds without a parsable conv ref stay inert.
  assert.equal(conversationIdFromContextItem({ kind: 'file', ref: 'mem:record/1' }), '')
})
