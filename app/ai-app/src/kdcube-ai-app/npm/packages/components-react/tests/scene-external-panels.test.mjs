import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  externalPanelCommandFromOpen,
  externalPanelSurfaceRegistrations,
  externalPanelWidgetMessage,
  normalizeExternalPanelConfig,
} from '../dist/scene/index.js'

// The referent runtime shape: one panel widget, a list surface with a static
// command and an editor surface that forwards the provider's open payload.
const panel = normalizeExternalPanelConfig({
  id: 'task_panel',
  label: 'Tasks',
  bundle_id: 'task-tracker@1-0',
  widget_alias: 'task_tracker_tasks',
  widget_message_type: 'kdcube-task-tracker-widget-command',
  open_message_types: ['kdcube-task-tracker-open-issue', 'kdcube-task-tracker-create-issue'],
  surfaces: {
    'task_tracker.issue_list': { expanded: false, command: { action: 'refresh' } },
    'task_tracker.issue_editor': { expanded: true, command_from_open: 'provider_surface_open' },
  },
})

const openRequest = {
  targetSurface: 'task_tracker.issue_editor',
  uiEvent: {
    target_surface: 'task_tracker.issue_editor',
    action: 'open',
    object_ref: 'task:issue:issue_123',
  },
  response: { object_ref: 'task:issue:issue_123', title: 'Broken export' },
  source: {},
}

test('command_from_open: provider_surface_open forwards the open payload', () => {
  const command = externalPanelCommandFromOpen(
    panel, 'task_tracker.issue_editor', panel.surfaces['task_tracker.issue_editor'], openRequest,
  )
  assert.equal(command.action, 'open')
  assert.equal(command.object_ref, 'task:issue:issue_123')
  assert.equal(command.target_surface, 'task_tracker.issue_editor')
  assert.equal(command.title, 'Broken export')
})

test('a static command surface posts its command, not the open payload', () => {
  const command = externalPanelCommandFromOpen(
    panel, 'task_tracker.issue_list', panel.surfaces['task_tracker.issue_list'],
    { ...openRequest, targetSurface: 'task_tracker.issue_list' },
  )
  assert.deepEqual(command, { action: 'refresh', target_surface: 'task_tracker.issue_list' })
})

test('widget_message_type retypes the delivered command and names the widget', () => {
  const message = externalPanelWidgetMessage(panel, {
    type: 'kdcube.surface.command',
    target_surface: 'task_tracker.issue_editor',
    action: 'open',
    object_ref: 'task:issue:issue_123',
  })
  assert.equal(message.type, 'kdcube-task-tracker-widget-command')
  assert.equal(message.widget, 'task_tracker_tasks')
  assert.equal(message.action, 'open')
  assert.equal(message.object_ref, 'task:issue:issue_123')
  // Without widget_message_type the scene-level type is kept.
  const plain = externalPanelWidgetMessage(
    { ...panel, widget_message_type: undefined },
    { type: 'kdcube.surface.command', action: 'open' },
  )
  assert.equal(plain.type, 'kdcube.surface.command')
})

test('registrations summon with the surface expanded state and deliver retyped commands', () => {
  const calls = { ensureOpen: [], posted: [] }
  const registrations = externalPanelSurfaceRegistrations(panel, {
    ensureOpen: (targetSurface, surface) => calls.ensureOpen.push([targetSurface, surface.expanded]),
    postToPanel: (message) => {
      calls.posted.push(message)
      return true
    },
    isReady: () => true,
  })
  assert.deepEqual(
    Object.keys(registrations).sort(),
    ['task_tracker.issue_editor', 'task_tracker.issue_list'],
  )
  const editor = registrations['task_tracker.issue_editor']
  editor.ensureOpen(openRequest)
  assert.deepEqual(calls.ensureOpen, [['task_tracker.issue_editor', true]])
  assert.equal(editor.isReady(openRequest), true)
  const command = editor.commandFromOpen(openRequest)
  assert.equal(editor.postCommand(command, openRequest), true)
  assert.equal(calls.posted.length, 1)
  assert.equal(calls.posted[0].type, 'kdcube-task-tracker-widget-command')
  assert.equal(calls.posted[0].widget, 'task_tracker_tasks')
  assert.equal(calls.posted[0].action, 'open')
  assert.equal(calls.posted[0].object_ref, 'task:issue:issue_123')
  assert.equal(calls.posted[0].target_surface, 'task_tracker.issue_editor')
})
