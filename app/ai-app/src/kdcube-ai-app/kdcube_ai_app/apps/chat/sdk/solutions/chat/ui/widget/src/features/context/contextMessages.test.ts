import assert from 'node:assert/strict'
import { test } from 'node:test'
import { recognizeContextMessageWithTypes } from './contextMessages'

const TYPES = {
  attach: 'kdcube-context-attach',
  focus: 'kdcube-context-focus',
  remove: 'kdcube-context-remove',
}

test('surface-command attach on sdk.chat.context is recognized as context', () => {
  const recognized = recognizeContextMessageWithTypes({
    type: 'kdcube.surface.command',
    target_surface: 'sdk.chat.context',
    action: 'attach',
    context: { kind: 'object.ref', ref: 'mem:record/1', label: 'A memory' },
  }, TYPES)
  assert.equal(recognized.length, 1)
  assert.equal(recognized[0].ref, 'mem:record/1')
})

test('surface-command OPEN never attaches as context (regression: conversation pin)', () => {
  // A routed conversation open must not fall through to attach-as-context.
  const recognized = recognizeContextMessageWithTypes({
    type: 'kdcube.surface.command',
    target_surface: 'sdk.chat.viewer',
    action: 'open',
    object_ref: 'conv:demo/demo/42d5a4e0abc',
    ui_event: { conversation_id: '42d5a4e0abc' },
  }, TYPES)
  assert.equal(recognized.length, 0)
})

test('attach targeted at a non-context surface is not attached here', () => {
  const recognized = recognizeContextMessageWithTypes({
    type: 'kdcube.surface.command',
    target_surface: 'sdk.canvas.pinboard',
    action: 'attach',
    context: { kind: 'object.ref', ref: 'mem:record/1' },
  }, TYPES)
  assert.equal(recognized.length, 0)
})
