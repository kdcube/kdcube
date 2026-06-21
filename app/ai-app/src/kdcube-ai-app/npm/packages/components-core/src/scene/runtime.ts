import {
  SCENE_CONFIG_REQUEST,
  SCENE_CONFIG_RESPONSE,
  SCENE_CONTEXT_DRAG_START,
  SCENE_SURFACE_COMMAND,
  type SceneActiveContextDrag,
  type SceneContextDragBroker,
  type SceneContextDragBrokerOptions,
  type SceneDispatchError,
  type SceneDispatchErrorCode,
  type SceneDispatchResult,
  type SceneDispatchSuccess,
  type SceneContextItem,
  type SceneContextDropError,
  type SceneContextDropResult,
  type SceneDropTarget,
  type SceneMessageEvent,
  type SceneMessageRouteOptions,
  type SceneObjectOpenActionRequest,
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

export function canonicalObjectRef(value: unknown): string {
  const record = asSceneRecord(value)
  const data = asSceneRecord(record.data)
  return (
    asSceneString(record.object_ref) ||
    asSceneString(record.ref) ||
    asSceneString(record.logical_path) ||
    asSceneString(record.logicalPath) ||
    asSceneString(record.hosted_uri) ||
    asSceneString(record.hostedUri) ||
    asSceneString(data.object_ref) ||
    asSceneString(data.ref) ||
    asSceneString(data.logical_path)
  )
}

export function rootNamespaceFromRef(ref: unknown): string {
  const match = asSceneString(ref).match(/^([A-Za-z][A-Za-z0-9_.-]*):/)
  return match ? match[1].toLowerCase() : ''
}

export function rootNamespaceFromNamespace(namespace: unknown): string {
  return asSceneString(namespace).toLowerCase().split(':', 1)[0] || ''
}

export function normalizeSceneContext(value: unknown): SceneContextItem | null {
  if (!isSceneRecord(value)) return null
  const ref = canonicalObjectRef(value)
  if (!ref) return null
  const context: SceneContextItem = { ...value }
  context.id = asSceneString(context.id) || ref
  context.ref = ref
  context.object_ref = ref
  context.logical_path = asSceneString(context.logical_path) || ref
  context.namespace = asSceneString(context.namespace) || rootNamespaceFromRef(ref)
  return context
}

export function rootNamespaceCandidates(item: SceneContextItem | null | undefined): string[] {
  if (!item) return []
  const seen = new Set<string>()
  const out: string[] = []
  const add = (value: unknown): void => {
    const root = rootNamespaceFromNamespace(value)
    if (!root || seen.has(root)) return
    seen.add(root)
    out.push(root)
  }
  add(item.namespace)
  add(rootNamespaceFromRef(item.ref))
  add(rootNamespaceFromRef(item.object_ref))
  add(rootNamespaceFromRef(item.logical_path))
  return out
}

export function contextFromSceneMessage(message: unknown): SceneContextItem | null {
  const data = asSceneRecord(message)
  const contexts = Array.isArray(data.contexts)
    ? data.contexts
    : data.context !== undefined
      ? [data.context]
      : []
  for (const item of contexts) {
    const context = normalizeSceneContext(item)
    if (context) return context
  }
  return normalizeSceneContext(data)
}

export function normalizeContextDragMessage(input: unknown): SceneActiveContextDrag | null {
  const data = asSceneRecord(input)
  if (asSceneString(data.type) !== SCENE_CONTEXT_DRAG_START) return null
  const rawContexts = Array.isArray(data.contexts)
    ? data.contexts
    : data.context !== undefined
      ? [data.context]
      : []
  const contexts = rawContexts
    .map((item) => normalizeSceneContext(item))
    .filter((item): item is SceneContextItem => Boolean(item))
  if (!contexts.length) return null
  return {
    sourceSurfaceRef: asSceneString(data.source_surface_ref) || asSceneString(data.sourceSurfaceRef) || asSceneString(data.source),
    contexts,
    message: data,
  }
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
    type: SCENE_SURFACE_COMMAND,
    target_surface: request.targetSurface,
    action: 'open',
    view: request.uiEvent.mode || 'expanded',
  }
  if (objectRef) command.object_ref = objectRef
  if (request.response.object !== undefined) command.object = request.response.object
  if (request.response.title !== undefined && command.title === undefined) command.title = request.response.title
  return objectRef || Object.keys(command).length > 2 ? command : null
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

function contextDropError(
  code: SceneContextDropError['code'],
  message: string,
  targetSurface?: string,
  context?: SceneContextItem,
  error?: unknown,
): SceneContextDropError {
  return { ok: false, code, message, targetSurface, context, error }
}

export function createContextDragBroker(options: SceneContextDragBrokerOptions): SceneContextDragBroker {
  let activeDrag: SceneActiveContextDrag | null = null
  const logger = options.logger

  function getActiveContext(): SceneContextItem | null {
    return activeDrag?.contexts[0] ?? null
  }

  function accepts(target: SceneDropTarget, contextInput?: SceneContextItem | null): boolean {
    const context = contextInput ?? getActiveContext()
    if (!context) return false
    const accepted = new Set((target.acceptsRootNamespaces || []).map((item) => asSceneString(item).toLowerCase()).filter(Boolean))
    if (accepted.has('*')) return true
    return rootNamespaceCandidates(context).some((candidate) => accepted.has(candidate))
  }

  async function dropOnTarget(target: SceneDropTarget, point?: { x: number; y: number }): Promise<SceneContextDropResult> {
    if (!activeDrag) {
      return contextDropError('active_drag_missing', 'No active context drag is available.', target.targetSurface)
    }
    const context = getActiveContext()
    if (!context) {
      return contextDropError('context_missing', 'Active context drag did not include a usable context.', target.targetSurface)
    }
    if (!accepts(target, context)) {
      return contextDropError('target_rejected', 'The drop target does not accept this context namespace.', target.targetSurface, context)
    }

    const targetSurface = asSceneString(target.targetSurface)
    const request = { context, target, point, activeDrag }
    const effect = target.dropEffect || 'open'
    if (effect !== 'open') {
      if (!target.deliverContext) {
        return contextDropError('target_delivery_missing', 'The drop target does not define a local delivery handler.', targetSurface, context)
      }
      try {
        await target.deliverContext(request)
        logger?.info?.('[kdcube.scene] delivered context drop', {
          effect,
          targetSurface,
          surfaceRef: target.surfaceRef,
          ref: context.ref,
        })
        return {
          ok: true,
          code: 'delivered',
          targetSurface,
          context,
          message: `Delivered context to ${target.label || targetSurface || target.surfaceRef}.`,
        }
      } catch (error) {
        return contextDropError('target_delivery_failed', 'The drop target delivery handler failed.', targetSurface, context, error)
      }
    }

    if (!targetSurface) {
      return contextDropError('target_surface_missing', 'Open drop target does not define target_surface.', undefined, context)
    }

    const actionRequest: SceneObjectOpenActionRequest = {
      action: 'open',
      object_ref: asSceneString(context.ref),
      target_surface: targetSurface,
      context,
    }
    try {
      const response = await options.objectAction(actionRequest)
      const dispatch = options.dispatchOpenResponse(response, context)
      logger?.info?.('[kdcube.scene] resolved context drop open', {
        targetSurface,
        surfaceRef: target.surfaceRef,
        ref: context.ref,
        dispatch,
      })
      return {
        ok: true,
        code: 'opened',
        targetSurface,
        context,
        response,
        dispatch,
        message: dispatch.message,
      }
    } catch (error) {
      logger?.warn?.('[kdcube.scene] context drop open failed', { targetSurface, ref: context.ref, error })
      return contextDropError('object_action_failed', 'Object action open failed for dropped context.', targetSurface, context, error)
    }
  }

  return {
    handleDragStart(message: unknown) {
      activeDrag = normalizeContextDragMessage(message)
      return activeDrag
    },
    handleDragEnd() {
      activeDrag = null
    },
    clear() {
      activeDrag = null
    },
    getActiveDrag() {
      return activeDrag
    },
    getActiveContext,
    accepts,
    dropOnTarget,
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
    const surfaceCommand: SceneRecord = {
      ...command,
      type: asSceneString(command.type) || SCENE_SURFACE_COMMAND,
      target_surface: asSceneString(command.target_surface) || targetSurface,
    }

    const request: SceneSurfaceOpenRequest = {
      targetSurface,
      uiEvent: asSceneRecord(requestInput.uiEvent),
      response: asSceneRecord(requestInput.response),
      source: asSceneRecord(requestInput.source),
      message: asSceneRecord(requestInput.message),
    }
    pending.set(targetSurface, { command: surfaceCommand, request: { ...request, targetSurface } })
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
    if (type !== SCENE_SURFACE_COMMAND) return null
    if (!isOriginAllowed(event.origin, routeOptions.allowedOrigins)) {
      return resultError('origin_not_allowed', `Scene message origin is not allowed: ${event.origin || '(missing)'}.`)
    }
    if (routeOptions.isSourceAllowed && !routeOptions.isSourceAllowed(event.source, data)) {
      return resultError('message_source_not_registered', 'Scene message source is not registered for this host.')
    }
    const targetSurface = asSceneString(data.target_surface) || asSceneString(data.targetSurface)
    return queueCommand(targetSurface, data, {
      targetSurface,
      uiEvent: data,
      response: asSceneRecord(data.response),
      source: asSceneRecord(data.source),
      message: data,
    })
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
