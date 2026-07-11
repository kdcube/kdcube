import {
  type NormalizedSceneContextDropTargetConfig,
  type SceneContextDropEffect,
  type SceneContextDropTargetConfig,
  type SceneContextDropTargetIssue,
  type SceneRecord,
} from './types'
import {
  asSceneRecord,
  asSceneString,
  isSceneRecord,
  sceneListValues,
  sceneMatchesAnySelector,
} from './runtime'

/**
 * Kind-aware exception route on a drop target whose blanket effect is NOT
 * `open` (attach / pin): objects whose canonical ref matches `patterns`
 * (minus `exclude`) resolve through the provider `open` pipeline toward
 * `targetSurface` instead of the target's default effect.
 *
 * The motivating mapping: a conversation pin (`conv:*`, excluding the
 * `conv:fi:*` file refs) dropped on chat OPENS that conversation in the chat
 * surface, while every other pin kind keeps attach-as-context. The shape is a
 * routing seam — additional per-kind routes (or an explicit user choice such
 * as "attach this conversation as context instead") layer on top of it later
 * without changing the drop-target contract.
 */
export interface SceneContextDropOpenRoute {
  patterns: string[]
  exclude?: string[]
  targetSurface: string
}

export function normalizeSceneContextDropOpenRoute(value: unknown): SceneContextDropOpenRoute | null {
  if (!isSceneRecord(value)) return null
  const record = value as SceneRecord
  const targetSurface = asSceneString(record.targetSurface) || asSceneString(record.target_surface)
  const patterns = sceneListValues(record.patterns)
  if (!targetSurface || !patterns.length) return null
  const exclude = sceneListValues(record.exclude)
  return {
    patterns,
    ...(exclude.length ? { exclude } : {}),
    targetSurface,
  }
}

/**
 * Resolve the kind-aware open route for a dropped object ref: returns the
 * route when the ref matches its patterns (and none of its exclusions),
 * otherwise null — the caller then applies the drop target's default effect.
 */
export function resolveSceneContextDropOpenRoute(
  ref: unknown,
  route: SceneContextDropOpenRoute | null | undefined,
): SceneContextDropOpenRoute | null {
  if (!route) return null
  const value = asSceneString(ref)
  if (!value) return null
  if (!sceneMatchesAnySelector(value, route.patterns)) return null
  if (route.exclude?.length && sceneMatchesAnySelector(value, route.exclude)) return null
  return route
}

export interface NormalizeSceneContextDropTargetOptions {
  knownDeliveries?: Iterable<string>
  requireDeliveryForEffects?: Iterable<string>
}

export interface NormalizeSceneContextDropTargetResult {
  target: NormalizedSceneContextDropTargetConfig | null
  issue: SceneContextDropTargetIssue | null
}

export interface NormalizeSceneContextDropTargetsResult {
  targets: Record<string, NormalizedSceneContextDropTargetConfig>
  issues: SceneContextDropTargetIssue[]
}

function cloneRecord<T>(value: T): T {
  if (value == null) return value
  return JSON.parse(JSON.stringify(value)) as T
}

function issue(key: string, code: SceneContextDropTargetIssue['code'], message: string, delivery?: string): SceneContextDropTargetIssue {
  return { key, code, message, delivery }
}

function knownDeliverySet(options: NormalizeSceneContextDropTargetOptions): Set<string> | null {
  if (!options.knownDeliveries) return null
  return new Set(Array.from(options.knownDeliveries).map((item) => asSceneString(item)).filter(Boolean))
}

function requiredDeliveryEffects(options: NormalizeSceneContextDropTargetOptions): Set<string> {
  const configured = options.requireDeliveryForEffects
    ? Array.from(options.requireDeliveryForEffects).map((item) => asSceneString(item)).filter(Boolean)
    : ['attach', 'pin']
  return new Set(configured)
}

export function mergeSceneContextDropTargets(base: unknown, override: unknown): SceneRecord {
  const out: SceneRecord = cloneRecord(asSceneRecord(base))
  const updates = asSceneRecord(override)
  Object.keys(updates).forEach((key) => {
    const value = updates[key]
    if (value === false || value === null) {
      out[key] = value
      return
    }
    const existing = out[key]
    if (isSceneRecord(existing) && isSceneRecord(value)) {
      out[key] = { ...cloneRecord(existing), ...cloneRecord(value) }
      return
    }
    out[key] = cloneRecord(value)
  })
  return out
}

export function sceneContextDropTargetsFromConfig(config: unknown): SceneRecord {
  const record = asSceneRecord(config)
  const direct = asSceneRecord(record.contextDropTargets)
  if (Object.keys(direct).length) return direct
  const scene = asSceneRecord(record.scene)
  const sceneTargets = asSceneRecord(scene.contextDropTargets)
  if (Object.keys(sceneTargets).length) return sceneTargets
  const runtime = asSceneRecord(record.runtime)
  return asSceneRecord(runtime.contextDropTargets)
}

export function normalizeSceneContextDropTarget(
  key: string,
  input: unknown,
  options: NormalizeSceneContextDropTargetOptions = {},
): NormalizeSceneContextDropTargetResult {
  const targetKey = asSceneString(key)
  if (!targetKey) {
    return { target: null, issue: issue('', 'target_not_record', 'Drop target key is empty.') }
  }
  if (input === false || (isSceneRecord(input) && input.enabled === false)) {
    return { target: null, issue: issue(targetKey, 'target_disabled', 'Drop target is disabled.') }
  }
  if (!isSceneRecord(input)) {
    return { target: null, issue: issue(targetKey, 'target_not_record', 'Drop target config must be an object.') }
  }

  const config = input as SceneContextDropTargetConfig
  const surfaceRef = asSceneString(config.surfaceRef)
  if (!surfaceRef) return { target: null, issue: issue(targetKey, 'surface_ref_missing', 'Drop target config is missing surfaceRef.') }

  const railId = asSceneString(config.railId)
  if (!railId) return { target: null, issue: issue(targetKey, 'rail_id_missing', 'Drop target config is missing railId.') }

  const dropEffect = asSceneString(config.dropEffect) || 'open'
  const accepts = config.accepts === undefined ? (dropEffect === 'open' ? 'provider-open' : 'context') : cloneRecord(config.accepts)
  const delivery = asSceneString(config.delivery)
  const knownDeliveries = knownDeliverySet(options)
  if (delivery && knownDeliveries && !knownDeliveries.has(delivery)) {
    return { target: null, issue: issue(targetKey, 'delivery_unknown', 'Drop target references an unknown delivery adapter.', delivery) }
  }
  if (requiredDeliveryEffects(options).has(dropEffect) && !delivery) {
    return { target: null, issue: issue(targetKey, 'delivery_missing', 'Drop target effect requires a delivery adapter.') }
  }
  const targetSurface = asSceneString(config.targetSurface)
  if (dropEffect === 'open' && !targetSurface && !delivery) {
    return { target: null, issue: issue(targetKey, 'open_route_missing', 'Open drop target needs targetSurface or delivery adapter.') }
  }

  return {
    target: {
      key: targetKey,
      surfaceRef,
      railId,
      accepts,
      dropEffect: dropEffect as SceneContextDropEffect | string,
      label: asSceneString(config.label) || undefined,
      targetSurface: targetSurface || undefined,
      delivery: delivery || undefined,
      raw: cloneRecord(config),
    },
    issue: null,
  }
}

export function normalizeSceneContextDropTargets(
  config: unknown,
  options: NormalizeSceneContextDropTargetOptions = {},
): NormalizeSceneContextDropTargetsResult {
  const source = sceneContextDropTargetsFromConfig(config)
  const targets: Record<string, NormalizedSceneContextDropTargetConfig> = {}
  const issues: SceneContextDropTargetIssue[] = []
  Object.keys(source).forEach((key) => {
    const result = normalizeSceneContextDropTarget(key, source[key], options)
    if (result.target) targets[key] = result.target
    if (result.issue && result.issue.code !== 'target_disabled') issues.push(result.issue)
  })
  return { targets, issues }
}
