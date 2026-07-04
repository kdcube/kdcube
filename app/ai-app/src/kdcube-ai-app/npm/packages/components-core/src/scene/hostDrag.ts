import {
  SCENE_CONTEXT_DRAG_START,
  type SceneActiveContextDrag,
  type SceneContextItem,
} from './types'
import {
  asSceneRecord,
  asSceneString,
  normalizeSceneContext,
} from './runtime'
import { namespacePresentationCandidates } from '../shared/namespacePresentation'

export interface ScenePoint {
  x: number
  y: number
}

export interface SceneRect {
  left: number
  top: number
  right?: number
  bottom?: number
  width?: number
  height?: number
}

export interface SceneDragCoordinateCalibration {
  x: number
  y: number
}

export interface SceneDropTargetGeometry {
  rect: SceneRect
  z?: number
}

export interface SelectSceneDropTargetOptions<TTarget> {
  reject?: (target: TTarget) => boolean
}

export function sceneNumber(value: unknown): number | null {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function contextItemsFromDragMessage(input: unknown): unknown[] {
  const data = asSceneRecord(input)
  if (Array.isArray(data.contexts)) return data.contexts
  if (data.context !== undefined) return [data.context]
  return []
}

export function normalizeHostContextDragStartMessage(
  input: unknown,
): SceneActiveContextDrag | null {
  const data = asSceneRecord(input)
  if (asSceneString(data.type) !== SCENE_CONTEXT_DRAG_START) return null
  const contexts = contextItemsFromDragMessage(data)
    .map((item) => normalizeSceneContext(item))
    .filter((item): item is SceneContextItem => Boolean(item))
  if (!contexts.length) return null
  return {
    sourceSurfaceRef: asSceneString(data.source_surface_ref) || asSceneString(data.sourceSurfaceRef) || asSceneString(data.source),
    contexts,
    message: data,
  }
}

export function presentationStyleCandidates(input: unknown): string[] {
  return namespacePresentationCandidates(input)
}

export function sceneRectBounds(rect: SceneRect): Required<SceneRect> {
  const width = sceneNumber(rect.width) ?? Math.max(0, (sceneNumber(rect.right) ?? rect.left) - rect.left)
  const height = sceneNumber(rect.height) ?? Math.max(0, (sceneNumber(rect.bottom) ?? rect.top) - rect.top)
  const right = sceneNumber(rect.right) ?? rect.left + width
  const bottom = sceneNumber(rect.bottom) ?? rect.top + height
  return {
    left: rect.left,
    top: rect.top,
    right,
    bottom,
    width,
    height,
  }
}

export function scenePointInRect(point: ScenePoint | null | undefined, rect: SceneRect): boolean {
  if (!point) return false
  const bounds = sceneRectBounds(rect)
  return point.x >= bounds.left && point.x <= bounds.right && point.y >= bounds.top && point.y <= bounds.bottom
}

export function computeSceneDragScreenCalibration(input: unknown, frameRect: SceneRect): SceneDragCoordinateCalibration | null {
  const data = asSceneRecord(input)
  const childX = sceneNumber(data.client_x ?? data.clientX)
  const childY = sceneNumber(data.client_y ?? data.clientY)
  const screenX = sceneNumber(data.screen_x ?? data.screenX)
  const screenY = sceneNumber(data.screen_y ?? data.screenY)
  if (childX === null || childY === null || screenX === null || screenY === null) return null
  return {
    x: frameRect.left + childX - screenX,
    y: frameRect.top + childY - screenY,
  }
}

export function scenePointFromChildDragMessage(
  input: unknown,
  frameRect: SceneRect,
  calibration?: SceneDragCoordinateCalibration | null,
): ScenePoint | null {
  const data = asSceneRecord(input)
  const parentX = sceneNumber(data.parent_client_x ?? data.parentClientX)
  const parentY = sceneNumber(data.parent_client_y ?? data.parentClientY)
  if (parentX !== null && parentY !== null) return { x: parentX, y: parentY }

  const screenX = sceneNumber(data.screen_x ?? data.screenX)
  const screenY = sceneNumber(data.screen_y ?? data.screenY)
  if (calibration && screenX !== null && screenY !== null) {
    return { x: screenX + calibration.x, y: screenY + calibration.y }
  }

  const childX = sceneNumber(data.client_x ?? data.clientX)
  const childY = sceneNumber(data.client_y ?? data.clientY)
  if (childX === null || childY === null) return null
  return {
    x: frameRect.left + childX,
    y: frameRect.top + childY,
  }
}

export function selectSceneDropTargetAtPoint<TTarget extends SceneDropTargetGeometry>(
  targets: readonly TTarget[],
  point: ScenePoint | null | undefined,
  options: SelectSceneDropTargetOptions<TTarget> = {},
): TTarget | null {
  if (!point) return null
  const matches = targets.filter((target) => {
    if (options.reject?.(target)) return false
    return scenePointInRect(point, target.rect)
  })
  if (!matches.length) return null
  matches.sort((a, b) => {
    const zDelta = (b.z || 0) - (a.z || 0)
    if (zDelta !== 0) return zDelta
    const aRect = sceneRectBounds(a.rect)
    const bRect = sceneRectBounds(b.rect)
    return (aRect.width * aRect.height) - (bRect.width * bRect.height)
  })
  return matches[0]
}
