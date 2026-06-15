import {
  SCENE_CONFIG_REQUEST,
  SCENE_CONFIG_RESPONSE,
  SCENE_OBJECT_OPEN,
  SCENE_PINBOARD_OPEN,
  type SceneDispatchError,
  type SceneDispatchErrorCode,
  type SceneDispatchResult,
  type SceneDispatchSuccess,
  type SceneMessageEvent,
  type SceneMessageRouteOptions,
  type SceneRecord,
  type SceneRuntime,
  type SceneRuntimeConfigResponseOptions,
  type SceneRuntimeOptions,
  type SceneSurfaceOpenRequest,
  type SceneSurfaceRegistration,
} from './types'

interface PendingSurfaceCommand {
  command: SceneRecord
  request: SceneSurfaceOpenRequest
}

type FlushStatus = 'flushed' | 'not_ready' | 'rejected'

const DEFAULT_FLUSH_DELAY_MS = 80

export function asSceneRecord(value: unknown): SceneRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as SceneRecord : {}
}

export function asSceneString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function isSceneRecord(value: unknown): value is SceneRecord {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

export function targetSurfaceFromOpenResponse(response: unknown): string {
  const responseRecord = asSceneRecord(response)
  const uiEvent = asSceneRecord(responseRecord.ui_event)
  return asSceneString(uiEvent.target_surface) || asSceneString(responseRecord.target_surface)
}

export function objectRefFromOpenRequest(request: SceneSurfaceOpenRequest): string {
  return (
    asSceneString(request.uiEvent.object_ref) ||
    asSceneString(request.response.object_ref) ||
    asSceneString(request.response.ref) ||
    asSceneString(request.source?.object_ref) ||
    asSceneString(request.source?.ref) ||
    asSceneString(request.source?.logical_path)
  )
}

export function providerSurfaceCommandFromOpen(request: SceneSurfaceOpenRequest): SceneRecord | null {
  const objectRef = objectRefFromOpenRequest(request)
  const command: SceneRecord = {
    ...request.uiEvent,
    action: 'open',
    view: request.uiEvent.mode || 'expanded',
  }
  if (objectRef) command.object_ref = objectRef
  if (request.response.object !== undefined) command.object = request.response.object
  if (request.response.title !== undefined && command.title === undefined) command.title = request.response.title
  return objectRef || Object.keys(command).length > 2 ? command : null
}

export function normalizeObjectOpenMessage(message: unknown): SceneSurfaceOpenRequest | null {
  const data = asSceneRecord(message)
  const type = asSceneString(data.type)
  if (type !== SCENE_OBJECT_OPEN && type !== SCENE_PINBOARD_OPEN) return null

  if (type === SCENE_OBJECT_OPEN) {
    const response = asSceneRecord(data.response)
    const uiEvent = asSceneRecord(response.ui_event)
    const targetSurface = targetSurfaceFromOpenResponse(response)
    return {
      targetSurface,
      uiEvent,
      response,
      source: asSceneRecord(data.source),
      message: data,
    }
  }

  const uiEventInput = asSceneRecord(data.ui_event)
  const targetSurface = asSceneString(data.target_surface) || asSceneString(uiEventInput.target_surface)
  const uiEvent = { ...uiEventInput }
  if (targetSurface && !uiEvent.target_surface) uiEvent.target_surface = targetSurface
  const responseInput = asSceneRecord(data.response)
  const response: SceneRecord = Object.keys(responseInput).length
    ? { ...responseInput, ui_event: asSceneRecord(responseInput.ui_event), target_surface: targetSurface || responseInput.target_surface }
    : { ui_event: uiEvent, target_surface: targetSurface }
  if (!Object.keys(asSceneRecord(response.ui_event)).length) response.ui_event = uiEvent

  return {
    targetSurface: targetSurfaceFromOpenResponse(response) || targetSurface,
    uiEvent: asSceneRecord(response.ui_event),
    response,
    source: asSceneRecord(data.source),
    message: data,
  }
}

export function normalizeSurfaceOpenResponse(response: unknown, source?: unknown): SceneSurfaceOpenRequest | null {
  const responseRecord = asSceneRecord(response)
  const uiEvent = asSceneRecord(responseRecord.ui_event)
  const targetSurface = targetSurfaceFromOpenResponse(responseRecord)
  if (!Object.keys(responseRecord).length && !Object.keys(uiEvent).length) return null
  return {
    targetSurface,
    uiEvent,
    response: responseRecord,
    source: asSceneRecord(source),
  }
}

export function buildConfigResponse(
  request: unknown,
  config: SceneRecord,
  options: SceneRuntimeConfigResponseOptions = {},
): SceneRecord | null {
  const data = asSceneRecord(request)
  if (data.type !== SCENE_CONFIG_REQUEST) return null
  return {
    type: options.responseType || SCENE_CONFIG_RESPONSE,
    identity: data.identity,
    config,
  }
}

export function createSceneRuntime(options: SceneRuntimeOptions = {}): SceneRuntime {
  const surfaces = new Map<string, SceneSurfaceRegistration>()
  const pending = new Map<string, PendingSurfaceCommand>()
  const timers = new Map<string, unknown>()
  const logger = options.logger
  const flushDelayMs = options.flushDelayMs ?? DEFAULT_FLUSH_DELAY_MS
  const schedule = options.setTimeout ?? ((handler: () => void, timeout: number) => setTimeout(handler, timeout))
  const unschedule = options.clearTimeout ?? ((timer: unknown) => clearTimeout(timer as ReturnType<typeof setTimeout>))

  function resultError(code: SceneDispatchErrorCode, message: string, targetSurface?: string): SceneDispatchError {
    const result: SceneDispatchError = { ok: false, code, message, targetSurface }
    options.onDispatchResult?.(result)
    logger?.warn?.('[kdcube.scene] dispatch failed', result)
    return result
  }

  function resultOk(code: SceneDispatchSuccess['code'], targetSurface: string, message: string): SceneDispatchSuccess {
    const result: SceneDispatchSuccess = { ok: true, code, targetSurface, message }
    options.onDispatchResult?.(result)
    logger?.info?.('[kdcube.scene] dispatch result', result)
    return result
  }

  function scheduleFlush(targetSurface: string): void {
    if (flushDelayMs < 0) return
    const existing = timers.get(targetSurface)
    if (existing !== undefined) unschedule(existing)
    const timer = schedule(() => {
      timers.delete(targetSurface)
      const status = tryFlushSurface(targetSurface)
      if (status === 'rejected') {
        const registration = surfaces.get(targetSurface)
        const label = registration?.label || targetSurface
        resultError('surface_command_rejected', `Surface ${label} rejected the queued command.`, targetSurface)
      }
    }, flushDelayMs)
    timers.set(targetSurface, timer)
  }

  function tryFlushSurface(targetSurface: string): FlushStatus {
    const pendingCommand = pending.get(targetSurface)
    if (!pendingCommand) return 'not_ready'
    const registration = surfaces.get(targetSurface)
    if (!registration) return 'not_ready'
    if (registration.isReady && !registration.isReady(pendingCommand.request)) return 'not_ready'
    const posted = registration.postCommand(pendingCommand.command, pendingCommand.request)
    if (posted) {
      pending.delete(targetSurface)
      logger?.info?.('[kdcube.scene] flushed surface command', {
        targetSurface,
        label: registration.label,
      })
      return 'flushed'
    }
    return registration.isReady?.(pendingCommand.request) ? 'rejected' : 'not_ready'
  }

  function queueCommand(
    targetSurfaceInput: string,
    command: SceneRecord,
    requestInput: Partial<SceneSurfaceOpenRequest> = {},
  ): SceneDispatchResult {
    const targetSurface = asSceneString(targetSurfaceInput || requestInput.targetSurface)
    if (!targetSurface) {
      return resultError('target_surface_missing', 'Open request did not include ui_event.target_surface.')
    }
    const registration = surfaces.get(targetSurface)
    if (!registration) {
      return resultError('target_surface_unavailable', `No widget surface is registered for ${targetSurface}.`, targetSurface)
    }
    if (!isSceneRecord(command) || !Object.keys(command).length) {
      return resultError(
        'surface_command_unavailable',
        `Resolver did not return enough data to open ${registration.label || targetSurface}.`,
        targetSurface,
      )
    }

    const request: SceneSurfaceOpenRequest = {
      targetSurface,
      uiEvent: asSceneRecord(requestInput.uiEvent),
      response: asSceneRecord(requestInput.response),
      source: asSceneRecord(requestInput.source),
      message: asSceneRecord(requestInput.message),
    }
    pending.set(targetSurface, { command, request: { ...request, targetSurface } })
    logger?.info?.('[kdcube.scene] queued surface command', {
      targetSurface,
      label: registration.label,
      objectRef: objectRefFromOpenRequest(request),
    })
    registration.ensureOpen?.(request)
    const status = tryFlushSurface(targetSurface)
    if (status === 'flushed') {
      return resultOk('dispatched', targetSurface, `Opened ${registration.label || targetSurface}.`)
    }
    if (status === 'rejected') {
      pending.delete(targetSurface)
      return resultError('surface_command_rejected', `Surface ${registration.label || targetSurface} rejected the command.`, targetSurface)
    }
    scheduleFlush(targetSurface)
    return resultOk('queued', targetSurface, `Opening ${registration.label || targetSurface}.`)
  }

  function dispatchRequest(request: SceneSurfaceOpenRequest | null): SceneDispatchResult {
    if (!request) {
      return resultError('message_invalid', 'Scene open request did not include a usable resolver response.')
    }
    const targetSurface = asSceneString(request.targetSurface)
    if (!targetSurface) {
      return resultError('target_surface_missing', 'Open request did not include ui_event.target_surface.')
    }
    const registration = surfaces.get(targetSurface)
    if (!registration) {
      return resultError('target_surface_unavailable', `No widget surface is registered for ${targetSurface}.`, targetSurface)
    }
    const command = registration.commandFromOpen(request)
    if (!isSceneRecord(command) || !Object.keys(command).length) {
      return resultError(
        'surface_command_unavailable',
        `Resolver did not return enough data to open ${registration.label || targetSurface}.`,
        targetSurface,
      )
    }
    return queueCommand(targetSurface, command, request)
  }

  function isOriginAllowed(origin: string | undefined, allowed: SceneMessageRouteOptions['allowedOrigins']): boolean {
    if (!allowed) return true
    if (!origin) return false
    if (typeof allowed === 'function') return allowed(origin)
    for (const item of allowed) {
      if (item === origin) return true
    }
    return false
  }

  function routeMessage(event: SceneMessageEvent, routeOptions: SceneMessageRouteOptions = {}): SceneDispatchResult | null {
    const data = asSceneRecord(event.data)
    const type = asSceneString(data.type)
    if (type !== SCENE_OBJECT_OPEN && type !== SCENE_PINBOARD_OPEN) return null
    if (!isOriginAllowed(event.origin, routeOptions.allowedOrigins)) {
      return resultError('origin_not_allowed', `Scene message origin is not allowed: ${event.origin || '(missing)'}.`)
    }
    if (routeOptions.isSourceAllowed && !routeOptions.isSourceAllowed(event.source, data)) {
      return resultError('message_source_not_registered', 'Scene message source is not registered for this host.')
    }
    return dispatchRequest(normalizeObjectOpenMessage(data))
  }

  return {
    registerSurface(targetSurface: string, registration: SceneSurfaceRegistration) {
      const key = asSceneString(targetSurface)
      if (!key) throw new Error('Scene surface registration requires a target surface.')
      surfaces.set(key, registration)
      logger?.info?.('[kdcube.scene] registered surface', { targetSurface: key, label: registration.label })
      return () => {
        surfaces.delete(key)
        pending.delete(key)
        const timer = timers.get(key)
        if (timer !== undefined) unschedule(timer)
        timers.delete(key)
      }
    },
    unregisterSurface(targetSurface: string) {
      const key = asSceneString(targetSurface)
      pending.delete(key)
      const timer = timers.get(key)
      if (timer !== undefined) unschedule(timer)
      timers.delete(key)
      return surfaces.delete(key)
    },
    getSurface(targetSurface: string) {
      return surfaces.get(asSceneString(targetSurface))
    },
    listSurfaces() {
      return Array.from(surfaces.keys())
    },
    dispatchSurfaceOpen(response: unknown, source?: unknown) {
      return dispatchRequest(normalizeSurfaceOpenResponse(response, source))
    },
    dispatchObjectOpen(message: unknown) {
      return dispatchRequest(normalizeObjectOpenMessage(message))
    },
    queueSurfaceCommand(targetSurface: string, command: SceneRecord, request?: Partial<SceneSurfaceOpenRequest>) {
      return queueCommand(targetSurface, command, request)
    },
    routeMessage,
    flushSurface(targetSurface: string) {
      return tryFlushSurface(asSceneString(targetSurface)) === 'flushed'
    },
    flushAll() {
      let count = 0
      Array.from(pending.keys()).forEach((targetSurface) => {
        if (tryFlushSurface(targetSurface) === 'flushed') count += 1
      })
      return count
    },
    getPendingCommand(targetSurface: string) {
      return pending.get(asSceneString(targetSurface))?.command
    },
    clearPending(targetSurface?: string) {
      if (targetSurface === undefined) {
        pending.clear()
        timers.forEach((timer) => unschedule(timer))
        timers.clear()
        return
      }
      const key = asSceneString(targetSurface)
      pending.delete(key)
      const timer = timers.get(key)
      if (timer !== undefined) unschedule(timer)
      timers.delete(key)
    },
  }
}
