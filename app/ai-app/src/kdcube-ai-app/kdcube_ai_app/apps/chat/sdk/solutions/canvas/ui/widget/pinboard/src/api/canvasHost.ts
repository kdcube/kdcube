/**
 * canvasHost — framework-free wiring that lets a host (the multi-widget
 * scene, or this standalone Pin Board widget) talk to the bundle's canvas
 * operations and the Data Bus.
 *
 * It is a faithful lift of the canvas plumbing the versatile scene runs
 * inline: REST `operations/<alias>` calls for read / list / object-action /
 * attachment-upload, and a Socket.IO Data Bus publish-and-wait for the
 * `canvas.patch` write path. Nothing here touches React — callers own state
 * and decide how to apply the responses (via the component package's
 * `applyCanvasCards` / `uploadAndPinFiles` + `normalizeCanvasPatchEvent`).
 */

import { io, type Socket } from 'socket.io-client'
import {
  canvasFromListItem,
  canvasFromReadResponse,
  emptyCanvasDefinition,
  upsertCanvasDefinition,
  type CanvasCard,
  type CanvasDefinition,
  type CanvasListResponse,
  type CanvasObjectActionName,
  type CanvasObjectActionResponse,
  type CanvasPatchInput,
  type CanvasPatchResponse,
  type CanvasReadInput,
  type CanvasReadResponse,
  type CanvasSearchInput,
  type CanvasSearchResponse,
  type CanvasUploadResponse,
} from '@kdcube/components-react/canvas'

export interface RouteContext {
  tenant: string
  project: string
  bundleId: string
  baseUrl: string
  accessToken?: string | null
  idToken?: string | null
}

interface DataBusMessageInput {
  message_id: string
  subject: string
  object_ref: string
  idempotency_key: string
  payload: Record<string, unknown>
  client: Record<string, unknown>
}

interface DataBusServiceEnvelope {
  type?: string
  data?: {
    message_id?: string
    subject?: string
    object_ref?: string
    data?: unknown
    code?: string
    message?: string
  }
}

const CANVAS_SUBJECT = 'canvas.patch'

// ---------------------------------------------------------------------------
// REST operations
// ---------------------------------------------------------------------------

function operationsUrl(ctx: RouteContext, alias: string): string {
  return (
    `${ctx.baseUrl}/api/integrations/bundles/` +
    `${encodeURIComponent(ctx.tenant)}/${encodeURIComponent(ctx.project)}/` +
    `${encodeURIComponent(ctx.bundleId)}/operations/${alias}`
  )
}

function unwrapOperationResponse<T>(alias: string, payload: unknown): T {
  if (payload && typeof payload === 'object' && alias in payload) {
    return (payload as Record<string, unknown>)[alias] as T
  }
  return payload as T
}

function operationErrorMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== 'object') return fallback
  const direct = payload as Record<string, unknown>
  if (direct.error !== undefined) return String(direct.error)
  if (direct.detail !== undefined) return String(direct.detail)
  if (direct.message !== undefined) return String(direct.message)
  for (const value of Object.values(direct)) {
    if (!value || typeof value !== 'object') continue
    const nested = value as Record<string, unknown>
    if (nested.error !== undefined) return String(nested.error)
    if (nested.detail !== undefined) return String(nested.detail)
    if (nested.message !== undefined) return String(nested.message)
  }
  return fallback
}

async function postOperation<TReq, TRes>(
  ctx: RouteContext,
  alias: string,
  body: TReq,
): Promise<TRes> {
  const response = await fetch(operationsUrl(ctx, alias), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ data: body }),
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(operationErrorMessage(payload, `HTTP ${response.status}`))
  }
  return unwrapOperationResponse<TRes>(alias, payload)
}

async function uploadCanvasAttachments(
  ctx: RouteContext,
  request: Record<string, unknown>,
  files: File[],
): Promise<CanvasUploadResponse> {
  const form = new FormData()
  form.append('payload', JSON.stringify(request))
  files.forEach((file) => form.append('files', file, file.name))
  const response = await fetch(operationsUrl(ctx, 'canvas_attachment_upload'), {
    method: 'POST',
    credentials: 'include',
    body: form,
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(operationErrorMessage(payload, `HTTP ${response.status}`))
  }
  return unwrapOperationResponse<CanvasUploadResponse>('canvas_attachment_upload', payload)
}

// ---------------------------------------------------------------------------
// Data Bus (Socket.IO) — single shared socket per host context
// ---------------------------------------------------------------------------

let dataBusSocket: Socket | null = null
let dataBusSocketKey = ''
let dataBusConnectPromise: Promise<void> | null = null

function timestampId(prefix: string): string {
  const now = new Date()
  const pad = (value: number, width = 2) => String(value).padStart(width, '0')
  const stamp = [
    now.getUTCFullYear(),
    pad(now.getUTCMonth() + 1),
    pad(now.getUTCDate()),
    pad(now.getUTCHours()),
    pad(now.getUTCMinutes()),
    pad(now.getUTCSeconds()),
    pad(now.getUTCMilliseconds(), 3),
  ].join('-')
  const random = Math.random().toString(36).slice(2, 6)
  return `${prefix}_${stamp}_${random}`
}

async function fetchProfileSessionId(ctx: RouteContext): Promise<string> {
  const response = await fetch(`${ctx.baseUrl}/profile`, {
    method: 'GET',
    credentials: 'include',
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`Unable to fetch profile for Data Bus (${response.status})`)
  }
  const payload = await response.json() as { session_id?: string | null }
  if (!payload.session_id) {
    throw new Error('Profile did not include a session id for Data Bus.')
  }
  return payload.session_id
}

function resetDataBusSocket(socket: Socket): void {
  if (dataBusSocket === socket) {
    dataBusSocket = null
    dataBusSocketKey = ''
    dataBusConnectPromise = null
  }
}

async function dataBusSocketFor(ctx: RouteContext): Promise<Socket> {
  const sessionId = await fetchProfileSessionId(ctx)
  const key = `${ctx.baseUrl}|/socket.io|/`
  const auth = {
    user_session_id: sessionId,
    tenant: ctx.tenant,
    project: ctx.project,
    bundle_id: ctx.bundleId,
    ...(ctx.accessToken ? { bearer_token: ctx.accessToken } : {}),
    ...(ctx.idToken ? { id_token: ctx.idToken } : {}),
  }
  if (dataBusSocket && dataBusSocketKey === key) {
    dataBusSocket.auth = auth
    return dataBusSocket
  }
  if (dataBusSocket) {
    dataBusSocket.disconnect()
    dataBusSocket = null
    dataBusConnectPromise = null
  }
  const socket = io(ctx.baseUrl, {
    path: '/socket.io',
    transports: ['websocket'],
    upgrade: false,
    autoConnect: false,
    withCredentials: true,
    auth,
    reconnection: false,
  })
  socket.on('connect_error', (error: Error) => {
    console.warn('[pinboard:data-bus] connect_error', { message: error.message })
  })
  socket.on('disconnect', (reason: string) => {
    console.info('[pinboard:data-bus] disconnected', { reason })
  })
  dataBusSocket = socket
  dataBusSocketKey = key
  return socket
}

function ensureSocketConnected(socket: Socket): Promise<void> {
  if (socket.connected) return Promise.resolve()
  if (dataBusConnectPromise) return dataBusConnectPromise
  dataBusConnectPromise = new Promise<void>((resolve, reject) => {
    let timer: number | undefined
    const cleanup = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      socket.off('connect', onConnect)
      socket.off('connect_error', onConnectError)
    }
    function onConnect(): void {
      cleanup()
      resolve()
    }
    function onConnectError(error: Error): void {
      cleanup()
      socket.disconnect()
      resetDataBusSocket(socket)
      reject(error)
    }
    timer = window.setTimeout(() => {
      cleanup()
      socket.disconnect()
      resetDataBusSocket(socket)
      reject(new Error('Timed out connecting to the Data Bus Socket.IO transport.'))
    }, 8000)
    socket.once('connect', onConnect)
    socket.once('connect_error', onConnectError)
    socket.connect()
  }).finally(() => {
    dataBusConnectPromise = null
  })
  return dataBusConnectPromise
}

async function publishDataBusAndWait(
  ctx: RouteContext,
  message: DataBusMessageInput,
): Promise<Record<string, unknown>> {
  const socket = await dataBusSocketFor(ctx)
  await ensureSocketConnected(socket)

  const resultPromise = new Promise<Record<string, unknown>>((resolve, reject) => {
    const timer = window.setTimeout(() => finish(() => reject(new Error(`Timed out waiting for Data Bus result: ${message.message_id}`))), 20000)
    const finish = (fn: () => void): void => {
      window.clearTimeout(timer)
      socket.off('chat_service', onService)
      socket.off('disconnect', onDisconnect)
      socket.off('connect_error', onConnectError)
      fn()
    }
    const onDisconnect = (reason: string): void => {
      finish(() => reject(new Error(`Data Bus socket disconnected before result: ${reason}`)))
    }
    const onConnectError = (error: Error): void => {
      finish(() => reject(error))
    }
    const onService = (payload: unknown): void => {
      const env = (payload ?? {}) as DataBusServiceEnvelope
      const data = env.data ?? {}
      if (data.message_id !== message.message_id) return
      if (env.type === 'kdcube.data_bus.result') {
        finish(() => resolve(isRecord(data.data) ? data.data : {}))
        return
      }
      if (env.type === 'kdcube.data_bus.conflict') {
        finish(() => resolve(isRecord(data.data) ? data.data : { ok: false, error: 'conflict' }))
        return
      }
      if (env.type === 'kdcube.data_bus.error') {
        finish(() => reject(new Error(String(data.message ?? data.code ?? 'Data Bus operation failed'))))
      }
    }
    socket.on('chat_service', onService)
    socket.once('disconnect', onDisconnect)
    socket.once('connect_error', onConnectError)
  })
  void resultPromise.catch(() => undefined)

  const packagePayload = {
    schema: 'kdcube.data_bus.ingress.v1',
    bundle_id: ctx.bundleId,
    messages: [message],
  }
  const ack = await socket.timeout(8000).emitWithAck('data_bus.publish', packagePayload)
  if (ack?.status !== 'accepted' && ack?.status !== 'partial') {
    throw new Error(String(ack?.rejected?.[0]?.error ?? 'Data Bus publish was rejected'))
  }
  const accepted = Array.isArray(ack.accepted) ? ack.accepted : []
  if (!accepted.some((item: Record<string, unknown>) => item.message_id === message.message_id)) {
    throw new Error(`Data Bus message was not accepted: ${message.message_id}`)
  }
  return resultPromise
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function subscribeCanvasPatchEvents(
  ctx: RouteContext,
  onPatch: (response: CanvasPatchResponse) => void,
  onError?: (error: Error) => void,
): () => void {
  let closed = false
  let subscribedSocket: Socket | null = null

  const onService = (payload: unknown): void => {
    const env = (payload ?? {}) as DataBusServiceEnvelope
    const data = env.data ?? {}
    if (env.type !== 'kdcube.data_bus.result') return
    if (data.subject !== CANVAS_SUBJECT) return
    if (!isRecord(data.data)) return
    onPatch(data.data as unknown as CanvasPatchResponse)
  }

  void (async () => {
    try {
      const socket = await dataBusSocketFor(ctx)
      await ensureSocketConnected(socket)
      if (closed) return
      subscribedSocket = socket
      socket.on('chat_service', onService)
    } catch (error) {
      if (closed) return
      onError?.(error instanceof Error ? error : new Error(String(error)))
    }
  })()

  return () => {
    closed = true
    if (subscribedSocket) {
      subscribedSocket.off('chat_service', onService)
    }
  }
}

function normalizeCanvasName(value: unknown): string {
  return String(value || 'main').trim() || 'main'
}

function canonicalCanvasId(value: unknown): string | undefined {
  const canvasId = String(value || '').trim()
  if (!canvasId || canvasId.startsWith('canvas:')) return undefined
  return canvasId
}

function objectRefForPatch(input: CanvasPatchInput): string {
  const canvasId = canonicalCanvasId(input.canvas_id ?? input.patch.canvas_id)
  if (canvasId?.startsWith('cnv:')) return canvasId
  const canvasName = normalizeCanvasName(input.canvas_name ?? input.patch.canvas_name)
  return `cnv:${canvasName}`
}

function normalizedPatchInput(input: CanvasPatchInput): CanvasPatchInput {
  const canvasName = String(input.canvas_name ?? input.patch.canvas_name ?? 'main').trim()
  const topCanvasId = canonicalCanvasId(input.canvas_id)
  const patchCanvasId = canonicalCanvasId(input.patch.canvas_id)
  const normalized: CanvasPatchInput = {
    ...input,
    canvas_name: canvasName || 'main',
    patch: {
      ...input.patch,
      canvas_name: canvasName || 'main',
    },
  }
  if (topCanvasId) normalized.canvas_id = topCanvasId
  else delete normalized.canvas_id
  if (patchCanvasId) normalized.patch.canvas_id = patchCanvasId
  else delete normalized.patch.canvas_id
  return normalized
}

// ---------------------------------------------------------------------------
// Host factory
// ---------------------------------------------------------------------------

export interface CanvasHostConfig {
  ctx: RouteContext
  /** Surface label stamped on Data Bus publishes for provenance. */
  surface?: string
}

export interface CanvasHost {
  patchCanvas(input: CanvasPatchInput): Promise<CanvasPatchResponse>
  subscribeCanvasPatchEvents(
    onPatch: (response: CanvasPatchResponse) => void,
    onError?: (error: Error) => void,
  ): () => void
  readCanvas(input: CanvasReadInput): Promise<CanvasReadResponse>
  /** List + read the active canvas, returning the merged definition set. */
  loadCanvas(activeCanvasName: string): Promise<CanvasDefinition[]>
  /** Bundle-authored board help HTML, surfaced by `canvas_list`; '' until loaded. */
  getBoardInfoHtml(): string
  uploadCanvasAttachments(payload: Record<string, unknown>, files: File[]): Promise<CanvasUploadResponse>
  objectAction(
    card: CanvasCard,
    action: CanvasObjectActionName,
    activeCanvas: CanvasDefinition,
  ): Promise<CanvasObjectActionResponse>
  /** Hybrid search over the user's pins (read-only; economically gated server-side). */
  searchPins(input: CanvasSearchInput): Promise<CanvasSearchResponse>
  /** Mark a board the user's last-active (so omitted-canvas pins land on it). */
  setActiveCanvas(canvasName: string): Promise<Record<string, unknown>>
  archiveCanvas(canvasName: string): Promise<Record<string, unknown>>
  deleteCanvas(canvasName: string): Promise<Record<string, unknown>>
}

export function createCanvasHost(config: CanvasHostConfig): CanvasHost {
  const { ctx } = config
  const surface = config.surface || 'pinboard.widget'
  // Board help HTML is bundle-authored config returned by `canvas_list`. It is
  // board-set-level and stable, so the first non-empty value sticks.
  let boardInfoHtml = ''

  const patchCanvas = async (input: CanvasPatchInput): Promise<CanvasPatchResponse> => {
    const normalizedInput = normalizedPatchInput(input)
    const messageId = timestampId('dbmsg')
    const payload = await publishDataBusAndWait(ctx, {
      message_id: messageId,
      subject: CANVAS_SUBJECT,
      object_ref: objectRefForPatch(normalizedInput),
      idempotency_key: messageId,
      payload: normalizedInput as unknown as Record<string, unknown>,
      client: { surface, operation: 'canvas_patch' },
    })
    return payload as unknown as CanvasPatchResponse
  }

  const readCanvas = (input: CanvasReadInput): Promise<CanvasReadResponse> => (
    postOperation<CanvasReadInput, CanvasReadResponse>(ctx, 'canvas_read', input)
  )

  const loadCanvas = async (activeCanvasName: string): Promise<CanvasDefinition[]> => {
    const list = await postOperation<Record<string, never>, CanvasListResponse>(ctx, 'canvas_list', {})
    const info = String(list.info_html ?? '').trim()
    if (info) boardInfoHtml = info
    const listed = (list.canvases ?? []).map(canvasFromListItem)
    const main = await readCanvas({ canvas_name: activeCanvasName })
    const mainCanvas = main.ok
      ? canvasFromReadResponse(main, emptyCanvasDefinition(activeCanvasName))
      : emptyCanvasDefinition(activeCanvasName)
    return upsertCanvasDefinition(listed.length ? listed : [], mainCanvas)
  }

  const objectAction = (
    card: CanvasCard,
    action: CanvasObjectActionName,
    activeCanvas: CanvasDefinition,
  ): Promise<CanvasObjectActionResponse> => (
    postOperation<unknown, CanvasObjectActionResponse>(ctx, 'canvas_object_action', {
      action,
      object_ref: card.ref,
      card_id: card.id,
      canvas_id: activeCanvas.id,
      canvas_name: activeCanvas.name,
      mime: card.mime,
    })
  )

  return {
    patchCanvas,
    subscribeCanvasPatchEvents: (onPatch, onError) => subscribeCanvasPatchEvents(ctx, onPatch, onError),
    readCanvas,
    loadCanvas,
    getBoardInfoHtml: () => boardInfoHtml,
    uploadCanvasAttachments: (payload, files) => uploadCanvasAttachments(ctx, payload, files),
    objectAction,
    searchPins: (input) =>
      postOperation<Record<string, unknown>, CanvasSearchResponse>(ctx, 'canvas_search', {
        query: input.query,
        all_boards: input.allBoards ?? false,
        canvas_name: input.canvasName,
        canvas_id: input.canvasId,
        kinds: input.kinds,
        namespaces: input.namespaces,
        limit: input.limit ?? 30,
      }),
    setActiveCanvas: (canvasName) =>
      postOperation<Record<string, unknown>, Record<string, unknown>>(ctx, 'canvas_set_active', { canvas_name: canvasName }),
    archiveCanvas: (canvasName) =>
      postOperation<Record<string, unknown>, Record<string, unknown>>(ctx, 'canvas_archive', { canvas_name: canvasName }),
    deleteCanvas: (canvasName) =>
      postOperation<Record<string, unknown>, Record<string, unknown>>(ctx, 'canvas_delete', { canvas_name: canvasName }),
  }
}
