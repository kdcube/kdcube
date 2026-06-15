export type SceneRecord = Record<string, unknown>

export const SCENE_CONFIG_REQUEST = 'CONFIG_REQUEST'
export const SCENE_CONFIG_RESPONSE = 'CONFIG_RESPONSE'
export const SCENE_CONN_RESPONSE = 'CONN_RESPONSE'
export const SCENE_OBJECT_OPEN = 'kdcube-object-open'
export const SCENE_PINBOARD_OPEN = 'kdcube-pinboard-open'
export const SCENE_WIDGET_VIEW = 'kdcube-widget-view'
export const SCENE_WIDGET_FOCUS = 'kdcube-widget-focus'

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

export interface SceneSurfaceOpenRequest {
  targetSurface: string
  uiEvent: SceneRecord
  response: SceneRecord
  source?: SceneRecord
  message?: SceneRecord
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
  dispatchObjectOpen: (message: unknown) => SceneDispatchResult
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
