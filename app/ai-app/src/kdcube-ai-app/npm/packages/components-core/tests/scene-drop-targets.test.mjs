import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  mergeSceneContextDropTargets,
  normalizeSceneContextDropOpenRoute,
  normalizeSceneContextDropTarget,
  normalizeSceneContextDropTargets,
  resolveSceneContextDropOpenRoute,
  sceneContextDropTargetsFromConfig,
} from '../dist/scene/index.js'

test('extracts context drop targets from active profile config', () => {
  const config = {
    contextDropTargets: {
      chat: {
        surfaceRef: 'website.chat',
        railId: 'chat',
        accepts: 'context',
        dropEffect: 'attach',
        delivery: 'chat.attach',
      },
    },
  }

  assert.deepEqual(Object.keys(sceneContextDropTargetsFromConfig(config)), ['chat'])
})

test('merges scene-level and profile-level target overrides', () => {
  const merged = mergeSceneContextDropTargets(
    {
      task_list: {
        surfaceRef: 'website.task_list',
        railId: 'task_list',
        accepts: 'provider-open',
        dropEffect: 'open',
        targetSurface: 'task_tracker.issue_list',
        delivery: 'task.open',
      },
      memories: {
        surfaceRef: 'website.memories',
        railId: 'memories',
        accepts: 'provider-open',
        dropEffect: 'open',
        targetSurface: 'sdk.memory.viewer',
      },
    },
    {
      task_list: {
        label: 'Open issue',
      },
      memories: false,
    },
  )

  assert.equal(merged.task_list.surfaceRef, 'website.task_list')
  assert.equal(merged.task_list.label, 'Open issue')
  assert.equal(merged.memories, false)
})

test('normalizes target config and accepts aliases', () => {
  const result = normalizeSceneContextDropTarget('pinboard', {
    surfaceRef: 'website.pinboard',
    railId: 'pinboard',
    accepts: '*',
    dropEffect: 'pin',
    delivery: 'pinboard.pin',
  }, {
    knownDeliveries: ['pinboard.pin'],
  })

  assert.equal(result.issue, null)
  assert.equal(result.target?.key, 'pinboard')
  assert.equal(result.target?.accepts, '*')
  assert.equal(result.target?.delivery, 'pinboard.pin')
})

test('reports invalid delivery and missing open route', () => {
  assert.equal(
    normalizeSceneContextDropTarget('chat', {
      surfaceRef: 'website.chat',
      railId: 'chat',
      accepts: 'context',
      dropEffect: 'attach',
      delivery: 'unknown.attach',
    }, {
      knownDeliveries: ['chat.attach'],
    }).issue?.code,
    'delivery_unknown',
  )

  assert.equal(
    normalizeSceneContextDropTarget('viewer', {
      surfaceRef: 'website.viewer',
      railId: 'viewer',
      accepts: 'provider-open',
      dropEffect: 'open',
    }).issue?.code,
    'open_route_missing',
  )
})

test('normalizes a target map and omits disabled targets from issues', () => {
  const result = normalizeSceneContextDropTargets({
    contextDropTargets: {
      chat: {
        surfaceRef: 'website.chat',
        railId: 'chat',
        accepts: 'context',
        dropEffect: 'attach',
        delivery: 'chat.attach',
      },
      disabled: false,
    },
  }, {
    knownDeliveries: ['chat.attach'],
  })

  assert.deepEqual(Object.keys(result.targets), ['chat'])
  assert.deepEqual(result.issues, [])
})

test('normalizes a kind-aware drop open route', () => {
  assert.deepEqual(
    normalizeSceneContextDropOpenRoute({
      patterns: ['conv:*'],
      exclude: ['conv:fi:*'],
      targetSurface: 'sdk.chat.viewer',
    }),
    { patterns: ['conv:*'], exclude: ['conv:fi:*'], targetSurface: 'sdk.chat.viewer' },
  )
  // snake_case surface alias and single-string pattern both normalize.
  assert.deepEqual(
    normalizeSceneContextDropOpenRoute({ patterns: 'conv:*', target_surface: 'sdk.chat.viewer' }),
    { patterns: ['conv:*'], targetSurface: 'sdk.chat.viewer' },
  )
  // Missing surface or patterns -> no route.
  assert.equal(normalizeSceneContextDropOpenRoute({ patterns: ['conv:*'] }), null)
  assert.equal(normalizeSceneContextDropOpenRoute({ targetSurface: 'sdk.chat.viewer' }), null)
  assert.equal(normalizeSceneContextDropOpenRoute(undefined), null)
})

test('kind-aware drop mapping: conversation pins open, other kinds keep the blanket effect', () => {
  const route = normalizeSceneContextDropOpenRoute({
    patterns: ['conv:*'],
    exclude: ['conv:fi:*'],
    targetSurface: 'sdk.chat.viewer',
  })

  // A conversation pin ref (full positional form) routes to open-in-chat.
  assert.deepEqual(
    resolveSceneContextDropOpenRoute('conv:demo/demo/42d5a4e0abc', route),
    route,
  )
  // A conversation FILE ref is excluded -> falls back to attach-as-context.
  assert.equal(resolveSceneContextDropOpenRoute('conv:fi:conv_a.turn_b.attachment/report.pdf', route), null)
  // Any other pin kind falls back to the target's default effect.
  assert.equal(resolveSceneContextDropOpenRoute('mem:record/1', route), null)
  assert.equal(resolveSceneContextDropOpenRoute('', route), null)
  assert.equal(resolveSceneContextDropOpenRoute('conv:demo/demo/42d5a4e0abc', null), null)
})
