export type SceneRecord = Record<string, unknown>

export const SCENE_CONFIG_REQUEST = 'CONFIG_REQUEST'
export const SCENE_CONFIG_RESPONSE = 'CONFIG_RESPONSE'
export const SCENE_CONN_RESPONSE = 'CONN_RESPONSE'
export const SCENE_SURFACE_COMMAND = 'kdcube.surface.command'
export const SCENE_WIDGET_VIEW = 'kdcube-widget-view'
export const SCENE_WIDGET_FOCUS = 'kdcube-widget-focus'
export const SCENE_CONTEXT_DRAG_START = 'kdcube-context-drag-start'
export const SCENE_CONTEXT_DRAG_END = 'kdcube-context-drag-end'

export type SceneDispatchSuccessCode = 'dispatched' | 'queued'

export type SceneDispatchErrorCode =
  | 'message_invalid'
  | 'origin_not_allowed'
  | 'message_source_not_registered'
  | 'target_surface_missing'
  | 'target_surface_unavailable'
  | 'surface_command_unavailable'
  | 'surface_command_rejected'

export interface SceneDispatchSuccess {
  ok: true
  code: SceneDispatchSuccessCode
  targetSurface: string
  message: string
}

export interface SceneDispatchError {
  ok: false
  code: SceneDispatchErrorCode
  message: string
  targetSurface?: string
}

export type SceneDispatchResult = SceneDispatchSuccess | SceneDispatchError

export type SceneContextDropEffect = 'open' | 'attach' | 'pin'

export interface SceneContextDropSuccess {
  ok: true
  code: 'opened' | 'delivered'
  targetSurface: string
  context: SceneContextItem
  response?: SceneRecord
  dispatch?: SceneDispatchResult
  message: string
}

export interface SceneContextDropError {
  ok: false
  code:
    | 'active_drag_missing'
    | 'context_missing'
    | 'target_rejected'
    | 'target_surface_missing'
    | 'object_action_failed'
    | 'target_delivery_missing'
    | 'target_delivery_failed'
  targetSurface?: string
  context?: SceneContextItem
  error?: unknown
  message: string
}

export type SceneContextDropResult = SceneContextDropSuccess | SceneContextDropError

export interface SceneSurfaceOpenRequest {
  targetSurface: string
  uiEvent: SceneRecord
  response: SceneRecord
  source?: SceneRecord
  message?: SceneRecord
}

export interface SceneContextItem extends SceneRecord {
  id?: string
  ref?: string
  object_ref?: string
  logical_path?: string
  namespace?: string
  kind?: string
  label?: string
  title?: string
  summary?: string
  mime?: string
  data?: SceneRecord
}

export interface SceneActiveContextDrag {
  sourceSurfaceRef?: string
  contexts: SceneContextItem[]
  message?: SceneRecord
}

export interface SceneContextDropRequest {
  context: SceneContextItem
  target: SceneDropTarget
  point?: { x: number; y: number }
  activeDrag?: SceneActiveContextDrag
}

export interface SceneDropTarget {
  surfaceRef: string
  targetSurface?: string
  acceptsRootNamespaces: string[]
  dropEffect?: SceneContextDropEffect
  label?: string
  deliverContext?: (request: SceneContextDropRequest) => void | Promise<void>
}

export interface SceneContextDropTargetConfig extends SceneRecord {
  surfaceRef?: string
  railId?: string
  targetSurface?: string
  acceptsRootNamespaces?: string[] | string
  accepts?: string[] | string
  dropEffect?: SceneContextDropEffect | string
  label?: string
  delivery?: string
  enabled?: boolean
}

export interface NormalizedSceneContextDropTargetConfig {
  key: string
  surfaceRef: string
  railId: string
  acceptsRootNamespaces: string[]
  dropEffect: SceneContextDropEffect | string
  label?: string
  targetSurface?: string
  delivery?: string
  raw: SceneContextDropTargetConfig
}

export type SceneContextDropTargetIssueCode =
  | 'target_disabled'
  | 'target_not_record'
  | 'surface_ref_missing'
  | 'rail_id_missing'
  | 'accepted_namespaces_missing'
  | 'delivery_unknown'
  | 'delivery_missing'
  | 'open_route_missing'

export interface SceneContextDropTargetIssue {
  key: string
  code: SceneContextDropTargetIssueCode
  message: string
  delivery?: string
}

export interface SceneObjectOpenActionRequest {
  action: 'open'
  object_ref: string
  target_surface?: string
  context?: SceneContextItem
}

export interface SceneContextDragBrokerOptions {
  objectAction: (request: SceneObjectOpenActionRequest) => Promise<SceneRecord>
  dispatchOpenResponse: (response: SceneRecord, source?: SceneContextItem) => SceneDispatchResult
  logger?: Pick<Console, 'info' | 'warn' | 'error'>
}

export interface SceneContextDragBroker {
  handleDragStart: (message: unknown) => SceneActiveContextDrag | null
  handleDragEnd: () => void
  clear: () => void
  getActiveDrag: () => SceneActiveContextDrag | null
  getActiveContext: () => SceneContextItem | null
  accepts: (target: SceneDropTarget, context?: SceneContextItem | null) => boolean
  dropOnTarget: (target: SceneDropTarget, point?: { x: number; y: number }) => Promise<SceneContextDropResult>
}

export interface SceneSurfaceRegistration {
  label?: string
  ensureOpen?: (request: SceneSurfaceOpenRequest) => void
  isReady?: (request: SceneSurfaceOpenRequest) => boolean
  postCommand: (command: SceneRecord, request: SceneSurfaceOpenRequest) => boolean
  commandFromOpen: (request: SceneSurfaceOpenRequest) => SceneRecord | null | undefined
}

export interface SceneRuntimeOptions {
  logger?: Pick<Console, 'info' | 'warn' | 'error'>
  flushDelayMs?: number
  setTimeout?: (handler: () => void, timeout: number) => unknown
  clearTimeout?: (timer: unknown) => void
  onDispatchResult?: (result: SceneDispatchResult) => void
}

export interface SceneRuntime {
  registerSurface: (targetSurface: string, registration: SceneSurfaceRegistration) => () => void
  unregisterSurface: (targetSurface: string) => boolean
  getSurface: (targetSurface: string) => SceneSurfaceRegistration | undefined
  listSurfaces: () => string[]
  dispatchSurfaceOpen: (response: unknown, source?: unknown) => SceneDispatchResult
  queueSurfaceCommand: (targetSurface: string, command: SceneRecord, request?: Partial<SceneSurfaceOpenRequest>) => SceneDispatchResult
  routeMessage: (event: SceneMessageEvent, options?: SceneMessageRouteOptions) => SceneDispatchResult | null
  flushSurface: (targetSurface: string) => boolean
  flushAll: () => number
  getPendingCommand: (targetSurface: string) => SceneRecord | undefined
  clearPending: (targetSurface?: string) => void
}

export interface SceneMessageEvent {
  data?: unknown
  origin?: string
  source?: unknown
}

export interface SceneMessageRouteOptions {
  allowedOrigins?: Iterable<string> | ((origin: string) => boolean)
  isSourceAllowed?: (source: unknown, data: SceneRecord) => boolean
}

export interface SceneRuntimeConfigResponseOptions {
  responseType?: typeof SCENE_CONFIG_RESPONSE | typeof SCENE_CONN_RESPONSE | string
}
