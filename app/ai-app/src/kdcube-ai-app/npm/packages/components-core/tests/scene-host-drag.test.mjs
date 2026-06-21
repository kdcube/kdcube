import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  computeSceneDragScreenCalibration,
  namespaceStyleCandidates,
  normalizeHostContextDragStartMessage,
  scenePointFromChildDragMessage,
  selectSceneDropTargetAtPoint,
} from '../dist/scene/index.js'

test('normalizes canonical host drag messages', () => {
  const active = normalizeHostContextDragStartMessage(
    {
      type: 'kdcube-context-drag-start',
      source: 'task_list',
      contexts: [{ object_ref: 'task:issue:T-1', label: 'Task one' }],
    },
  )

  assert.equal(active?.sourceSurfaceRef, 'task_list')
  assert.equal(active?.contexts[0].ref, 'task:issue:T-1')
})

test('rejects non-canonical drag-start messages', () => {
  assert.equal(
    normalizeHostContextDragStartMessage({
      type: 'not-canonical-drag-start',
      items: [{ ref: 'task:issue:T-1' }],
    }),
    null,
  )
})

test('builds namespace style candidates from explicit namespace and ref', () => {
  assert.deepEqual(
    namespaceStyleCandidates({
      namespace: 'task:attachment',
      ref: 'task:issue:attachment:T-1/file.docx',
      kind: 'file',
    }),
    ['task:attachment', 'task', 'file'],
  )
})

test('maps child drag coordinates to parent scene coordinates', () => {
  const frameRect = { left: 300, top: 120, width: 200, height: 100 }

  assert.deepEqual(
    scenePointFromChildDragMessage({ parent_client_x: 900, parent_client_y: 640 }, frameRect),
    { x: 900, y: 640 },
  )

  const calibration = computeSceneDragScreenCalibration(
    { client_x: 20, client_y: 10, screen_x: 500, screen_y: 400 },
    frameRect,
  )
  assert.deepEqual(calibration, { x: -180, y: -270 })
  assert.deepEqual(
    scenePointFromChildDragMessage({ screen_x: 520, screen_y: 430 }, frameRect, calibration),
    { x: 340, y: 160 },
  )

  assert.deepEqual(
    scenePointFromChildDragMessage({ client_x: 15, client_y: 25 }, frameRect),
    { x: 315, y: 145 },
  )
})

test('selects drop target by point, z-order, then smaller area', () => {
  const targets = [
    { id: 'large', z: 5, rect: { left: 0, top: 0, width: 400, height: 400 } },
    { id: 'small', z: 5, rect: { left: 50, top: 50, width: 100, height: 100 } },
    { id: 'front', z: 10, rect: { left: 60, top: 60, width: 300, height: 300 } },
  ]

  assert.equal(selectSceneDropTargetAtPoint(targets, { x: 75, y: 75 })?.id, 'front')
  assert.equal(
    selectSceneDropTargetAtPoint(targets, { x: 75, y: 75 }, { reject: (target) => target.id === 'front' })?.id,
    'small',
  )
})
