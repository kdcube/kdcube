import {
  CanvasBoard,
  applyCanvasCards,
  cardFromChatArtifact,
  cardFromChatAssistantText,
  cardFromSearchResult,
  cardFromSelectedText,
  canvasContext,
  canvasFromListItem,
  canvasFromPatchEvent,
  canvasFromReadResponse,
  emptyCanvasDefinition,
  normalizeCanvasPatchEvent,
  uploadAndPinFiles,
  upsertCanvasDefinition,
  type CanvasCard,
  type CanvasContextItem,
  type CanvasDefinition,
  type CanvasIngressPayload,
  type CanvasListResponse,
  type CanvasObjectActionName,
  type CanvasObjectActionResponse,
  type CanvasPatchInput,
  type CanvasPatchResponse,
  type CanvasPatchUiEvent,
  type CanvasReadInput,
  type CanvasReadResponse,
  type CanvasUploadResponse,
} from '@kdcube/canvas-component'
import { Archive, Bot, Maximize2, Minimize2, Plus, X } from 'lucide-react'
import React, { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { createRoot } from 'react-dom/client'
import { io, type Socket } from 'socket.io-client'
import './styles.css'

const BUNDLE_ID = 'versatile@2026-03-31-13-36'
const CONFIG_IDENTITY = 'BUNDLE_VERSATILE_MAIN_VIEW'
const CHAT_CONFIG_IDENTITY = 'BUNDLE_VERSATILE_CHAT_VIEW'
const CHAT_WIDGET_ALIAS = 'versatile_chat'
const MEMORY_WIDGET_ALIAS = 'memories'
const CANVAS_STORY_ID = 'versatile:main'
const CANVAS_SUBJECT = 'canvas.patch'
const DEFAULT_CHAT_WIDTH = 460
const DEFAULT_CHAT_HEIGHT = 720
const DEFAULT_CHAT_MIN_WIDTH = 340
const DEFAULT_CHAT_MAX_WIDTH = 860
const FLOATING_PANEL_BASE_Z = 72

interface RouteContext {
  tenant: string
  project: string
  bundleId: string
  publicStatic: boolean
  baseUrl: string
  accessToken?: string | null
  idToken?: string | null
}

interface RuntimeConfig {
  baseUrl?: string
  defaultTenant?: string
  defaultProject?: string
  defaultAppBundleId?: string | null
  tenant?: string
  project?: string
  tenant_id?: string
  project_id?: string
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
    data?: Record<string, unknown>
    code?: string
    message?: string
  }
}

let dataBusSocket: Socket | null = null
let dataBusSocketKey = ''
let dataBusConnectPromise: Promise<void> | null = null

function decodePart(value: string | undefined): string {
  if (value === undefined || value === '') return ''
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

function routeContext(): RouteContext {
  const path = window.location.pathname
  const publicMarker = '/api/integrations/bundles/'
  const staticMarker = '/api/integrations/static/'

  const publicIndex = path.indexOf(publicMarker)
  if (publicIndex >= 0) {
    const parts = path.slice(publicIndex + publicMarker.length).split('/').map(decodePart)
    return {
      tenant: parts[0] ?? '',
      project: parts[1] ?? '',
      bundleId: parts[2] ?? BUNDLE_ID,
      publicStatic: parts[3] === 'public' && parts[4] === 'static',
      baseUrl: window.location.origin,
    }
  }

  const staticIndex = path.indexOf(staticMarker)
  if (staticIndex >= 0) {
    const parts = path.slice(staticIndex + staticMarker.length).split('/').map(decodePart)
    return {
      tenant: parts[0] ?? '',
      project: parts[1] ?? '',
      bundleId: parts[2] ?? BUNDLE_ID,
      publicStatic: false,
      baseUrl: window.location.origin,
    }
  }

  const params = new URLSearchParams(window.location.search)
  return {
    tenant: params.get('tenant') ?? 'demo-tenant',
    project: params.get('project') ?? 'demo-project',
    bundleId: params.get('bundle_id') ?? params.get('bundleId') ?? BUNDLE_ID,
    publicStatic: params.get('public') === '1',
    baseUrl: window.location.origin,
  }
}

function contextFromConfig(config: RuntimeConfig | null, fallback: RouteContext): RouteContext {
  if (!config) return fallback
  return {
    tenant: config.defaultTenant ?? config.tenant ?? config.tenant_id ?? fallback.tenant,
    project: config.defaultProject ?? config.project ?? config.project_id ?? fallback.project,
    bundleId: config.defaultAppBundleId ?? fallback.bundleId,
    publicStatic: fallback.publicStatic,
    baseUrl: config.baseUrl ?? fallback.baseUrl,
    accessToken: config.accessToken ?? fallback.accessToken,
    idToken: config.idToken ?? fallback.idToken,
  }
}

function widgetUrl(ctx: RouteContext, alias: string, params?: Record<string, string>): string {
  const base = `${ctx.baseUrl}/api/integrations/bundles/${encodeURIComponent(ctx.tenant)}/${encodeURIComponent(ctx.project)}/${encodeURIComponent(ctx.bundleId)}`
  const route = ctx.publicStatic ? 'public/widgets' : 'widgets'
  const url = new URL(`${base}/${route}/${alias}`)
  if (params) {
    Object.entries(params).forEach(([key, value]) => url.searchParams.set(key, value))
  }
  return url.toString()
}

function chatWidgetUrl(ctx: RouteContext): string {
  return widgetUrl(ctx, CHAT_WIDGET_ALIAS, {
    chat_embed_mode: 'host',
    chat_widget_id: CHAT_WIDGET_ALIAS,
    chat_config_identity: CHAT_CONFIG_IDENTITY,
    chat_brand_label: 'Versatile',
    chat_event_prefix: 'versatile',
    chat_surface: 'versatile_chat',
    chat_user_event_source_id: 'versatile.main.chat.user',
    chat_attachment_event_source_id: 'versatile.main.chat.attachment',
    chat_context_event_source_id: 'versatile.context.focus',
    chat_canvas_state_event_source_id: 'canvas.state',
    chat_canvas_focus_event_source_id: 'canvas.focus',
    chat_canvas_surface: 'canvas',
    chat_canvas_ingress_message: 'kdcube-canvas-ingress',
    chat_canvas_patch_step: 'canvas.patch',
    chat_context_attach_message: 'kdcube-context-attach',
    chat_context_focus_message: 'kdcube-context-focus',
    chat_context_remove_message: 'kdcube-context-remove',
    chat_context_refresh_source: 'kdcube-context-refresh',
    bundle_id: ctx.bundleId,
  })
}

function memoryWidgetUrl(ctx: RouteContext, expanded: boolean): string {
  return widgetUrl(ctx, MEMORY_WIDGET_ALIAS, {
    view: expanded ? 'expanded' : 'compact',
    compact: expanded ? '0' : '1',
    host_controls: '1',
    limit: expanded ? '12' : '2',
  })
}

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
    const message = operationErrorMessage(payload, `HTTP ${response.status}`)
    throw new Error(message)
  }
  return unwrapOperationResponse<TRes>(alias, payload)
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

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function numberParam(names: string[], fallback: number): number {
  const params = new URLSearchParams(window.location.search || '')
  for (const name of names) {
    const value = Number(params.get(name))
    if (Number.isFinite(value) && value > 0) return value
  }
  return fallback
}

function sceneChatSizing() {
  const viewportMax = Math.max(300, window.innerWidth - 108)
  const minWidth = clamp(numberParam(['chat_min_width', 'chatMinWidth'], DEFAULT_CHAT_MIN_WIDTH), 300, viewportMax)
  const maxWidth = clamp(numberParam(['chat_max_width', 'chatMaxWidth'], DEFAULT_CHAT_MAX_WIDTH), minWidth, viewportMax)
  return {
    minWidth,
    maxWidth,
    width: clamp(numberParam(['chat_width', 'chatWidth'], DEFAULT_CHAT_WIDTH), minWidth, maxWidth),
    height: clamp(numberParam(['chat_height', 'chatHeight'], DEFAULT_CHAT_HEIGHT), 420, Math.max(420, window.innerHeight - 92)),
  }
}

function downloadBase64File(contentBase64: string, filename: string, mime = 'application/octet-stream') {
  const binary = window.atob(contentBase64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  const blob = new Blob([bytes], { type: mime || 'application/octet-stream' })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename || 'download'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

function KubeRobotIcon({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="versatileKubeBody" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#C6F3F1" />
          <stop offset="100%" stopColor="#4372C3" />
        </linearGradient>
      </defs>
      <line x1="32" y1="7" x2="32" y2="17" stroke="#2B4B8A" strokeWidth="3" strokeLinecap="round" />
      <circle cx="32" cy="5" r="5" fill="#6B63FE" stroke="#06101E" strokeWidth="1.5" />
      <rect x="7" y="17" width="50" height="40" fill="url(#versatileKubeBody)" stroke="#06101E" strokeWidth="2.5" rx="4" />
      <circle cx="23" cy="35" r="9" fill="white" stroke="#06101E" strokeWidth="1.5" />
      <circle cx="23" cy="35" r="4" fill="#06101E" />
      <circle cx="41" cy="35" r="9" fill="white" stroke="#06101E" strokeWidth="1.5" />
      <circle cx="41" cy="35" r="4" fill="#06101E" />
      <path d="M 23 48 Q 32 55 41 48" stroke="#06101E" strokeWidth="2.5" fill="none" strokeLinecap="round" />
      <rect x="13" y="57" width="12" height="7" fill="#4372C3" stroke="#06101E" strokeWidth="1.5" rx="1.5" />
      <rect x="39" y="57" width="12" height="7" fill="#4372C3" stroke="#06101E" strokeWidth="1.5" rx="1.5" />
    </svg>
  )
}

function GlowPinnedCanvasIcon({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <defs>
        <radialGradient id="versatileBulbGlow" cx="50%" cy="42%" r="62%">
          <stop offset="0%" stopColor="#FFF6B8" stopOpacity="1" />
          <stop offset="62%" stopColor="#F0BC2E" stopOpacity="0.52" />
          <stop offset="100%" stopColor="#01BEB2" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="23" cy="22" r="22" fill="url(#versatileBulbGlow)" />
      <path
        d="M15.5 27.2c-3.9-3-5.7-7-4.6-11.3C12.2 10.2 17.1 6.2 23 5.9c6.3-.4 11.7 3.8 13 9.7 1.1 4.7-.9 8.8-5.1 11.9-1.5 1.1-2.4 2.7-2.4 4.5v.8H18v-.7c0-1.9-.9-3.7-2.5-4.9Z"
        fill="#FFE884"
        stroke="#9A7206"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path d="M18.5 36h10M20 40h7" stroke="#9A7206" strokeWidth="2.2" strokeLinecap="round" />
      <path
        d="M33.5 30.5 39 36l-4.3 1-1 4.3-5.5-5.5Z"
        fill="#C6F3F1"
        stroke="#4372C3"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <circle cx="34.7" cy="34.3" r="1.5" fill="#4372C3" />
    </svg>
  )
}

function memoryIdFromCanvasActionResponse(response: CanvasObjectActionResponse): string {
  const eventMemoryId = response.ui_event?.memory_id
  if (typeof eventMemoryId === 'string' && eventMemoryId.trim()) return eventMemoryId.trim()
  const memory = response.memory
  if (memory && typeof memory === 'object') {
    const id = (memory as { id?: unknown }).id
    if (typeof id === 'string' && id.trim()) return id.trim()
  }
  const ref = String(response.object_ref || response.ref || '').trim()
  return ref.startsWith('mem:') ? ref.slice(4).split(/[?#]/, 1)[0].replace(/^\/+/, '') : ''
}

function memoryPanelSize(expanded: boolean) {
  return {
    width: expanded ? Math.min(760, window.innerWidth - 64) : Math.min(420, window.innerWidth - 80),
    height: expanded ? Math.min(720, window.innerHeight - 92) : Math.min(520, window.innerHeight - 118),
  }
}

function chatPanelSize(expanded: boolean, width: number, height = DEFAULT_CHAT_HEIGHT) {
  return {
    width: expanded ? window.innerWidth : Math.min(width, window.innerWidth - 72),
    height: expanded ? window.innerHeight : Math.min(height, window.innerHeight - 92),
  }
}

function compactMemoryPaneHeight(contentHeight: number | null): number {
  const max = Math.max(260, Math.min(520, window.innerHeight - 118))
  const measured = contentHeight && Number.isFinite(contentHeight) ? contentHeight + 32 : 0
  return clamp(measured || 360, 220, max)
}

function defaultChatFrame(width: number, expanded: boolean, height = DEFAULT_CHAT_HEIGHT) {
  const panel = chatPanelSize(expanded, width, height)
  return {
    x: clamp(window.innerWidth - panel.width - 70, 8, Math.max(8, window.innerWidth - panel.width - 60)),
    y: 84,
  }
}

function defaultMemoryFrame(chatWidth: number, chatOpen: boolean, expanded: boolean) {
  const panel = memoryPanelSize(expanded)
  const rightEdge = chatOpen ? Math.max(64, window.innerWidth - chatWidth - 18) : window.innerWidth - 62
  return {
    x: clamp(rightEdge - panel.width - 12, 8, Math.max(8, window.innerWidth - panel.width - 8)),
    y: 92,
  }
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
    console.warn('[versatile-scene:data-bus] connect_error', { message: error.message })
  })
  socket.on('disconnect', (reason: string) => {
    console.info('[versatile-scene:data-bus] disconnected', { reason })
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
        finish(() => resolve(data.data ?? {}))
        return
      }
      if (env.type === 'kdcube.data_bus.conflict') {
        finish(() => resolve(data.data ?? { ok: false, error: 'conflict' }))
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

function objectRefForPatch(input: CanvasPatchInput): string {
  const canvasId = String(input.canvas_id ?? input.patch.canvas_id ?? '').trim()
  if (canvasId) return canvasId
  const canvasName = String(input.canvas_name ?? input.patch.canvas_name ?? 'main').trim()
  return `canvas:${canvasName}`
}

function cardFromContext(context: CanvasContextItem, rect: CanvasCard['rect']) {
  const ref = String(context.logical_path ?? context.ref ?? context.id ?? '').trim()
  return cardFromSearchResult(
    {
      ref,
      title: context.label ? context.label : ref,
      mime: context.mime,
      summary: context.summary,
      kind: context.kind === 'memory' ? 'memory' : undefined,
    },
    { placement: 'placed', rect },
  )
}

function cardFromIngress(payload: CanvasIngressPayload, rect: CanvasCard['rect']) {
  if (payload.kind === 'chat.artifact') {
    return cardFromChatArtifact(
      {
        ref: payload.ref,
        filename: payload.filename,
        mime: payload.mime,
        preview: payload.preview,
      },
      { placement: 'placed', rect },
    )
  }
  if (payload.kind === 'chat.assistant.text') {
    return cardFromChatAssistantText(payload.text, { title: payload.title, placement: 'placed', rect })
  }
  throw new Error(`Unsupported canvas ingress kind: ${(payload as { kind?: string }).kind}`)
}

function requestRuntimeConfig(): Promise<RuntimeConfig | null> {
  if (window.parent === window) return Promise.resolve(null)
  window.parent.postMessage({ type: 'CONFIG_REQUEST', identity: CONFIG_IDENTITY }, '*')
  return new Promise<RuntimeConfig | null>((resolve) => {
    const timer = window.setTimeout(() => {
      window.removeEventListener('message', onMessage)
      resolve(null)
    }, 1200)
    function onMessage(event: MessageEvent): void {
      if (event.data?.type !== 'CONFIG_RESPONSE' && event.data?.type !== 'CONN_RESPONSE') return
      if (event.data.identity !== CONFIG_IDENTITY) return
      window.clearTimeout(timer)
      window.removeEventListener('message', onMessage)
      resolve(event.data.config ?? null)
    }
    window.addEventListener('message', onMessage)
  })
}

function App() {
  const fallback = useMemo(() => routeContext(), [])
  const chatSizing = useMemo(() => sceneChatSizing(), [])
  const [ctx, setCtx] = useState<RouteContext>(fallback)
  const [ready, setReady] = useState(false)
  const [chatOpen, setChatOpen] = useState(true)
  const [chatExpanded, setChatExpanded] = useState(false)
  const [chatWidth, setChatWidth] = useState(chatSizing.width)
  const [chatFrame, setChatFrame] = useState(() => defaultChatFrame(chatSizing.width, false, chatSizing.height))
  const [canvasOpen, setCanvasOpen] = useState(true)
  const [memoryOpen, setMemoryOpen] = useState(true)
  const [memoryExpanded, setMemoryExpanded] = useState(false)
  const [memoryCount, setMemoryCount] = useState<number | null>(null)
  const [memoryContentHeight, setMemoryContentHeight] = useState<number | null>(null)
  const [memoryFrame, setMemoryFrame] = useState(() => defaultMemoryFrame(chatSizing.width, true, false))
  const [panelZ, setPanelZ] = useState<Record<'chat' | 'memory', number>>({
    chat: FLOATING_PANEL_BASE_Z,
    memory: FLOATING_PANEL_BASE_Z + 1,
  })
  const [activeCanvasName, setActiveCanvasName] = useState('main')
  const [canvases, setCanvases] = useState<CanvasDefinition[]>([emptyCanvasDefinition('main')])
  const [canvasPatchEvent, setCanvasPatchEvent] = useState<CanvasPatchUiEvent | null>(null)
  const [notice, setNotice] = useState('')
  const chatFrameRef = useRef<HTMLIFrameElement | null>(null)
  const memoryFrameRef = useRef<HTMLIFrameElement | null>(null)
  const pendingMemoryCommandRef = useRef<Record<string, unknown> | null>(null)
  const panelZCursorRef = useRef(FLOATING_PANEL_BASE_Z + 2)

  const activeCanvas = useMemo(
    () => canvases.find((canvas) => canvas.name === activeCanvasName) ?? emptyCanvasDefinition(activeCanvasName),
    [activeCanvasName, canvases],
  )

  const sendToChat = useCallback((message: Record<string, unknown>) => {
    chatFrameRef.current?.contentWindow?.postMessage(message, '*')
  }, [])

  const bringPanelToFront = useCallback((panel: 'chat' | 'memory') => {
    panelZCursorRef.current += 1
    setPanelZ((current) => ({ ...current, [panel]: panelZCursorRef.current }))
  }, [])

  const syncChatWidgetView = useCallback((view: 'compact' | 'expanded') => {
    chatFrameRef.current?.contentWindow?.postMessage({ type: 'kdcube-set-view', view }, '*')
  }, [])

  const syncMemoryWidgetView = useCallback((view: 'compact' | 'expanded') => {
    memoryFrameRef.current?.contentWindow?.postMessage({
      type: 'kdcube-set-view',
      widget: MEMORY_WIDGET_ALIAS,
      view,
    }, '*')
  }, [])

  const sendMemoryWidgetCommand = useCallback((command: Record<string, unknown>) => {
    const target = memoryFrameRef.current?.contentWindow
    if (!target) return false
    target.postMessage({
      type: 'kdcube-memory-widget-command',
      widget: MEMORY_WIDGET_ALIAS,
      ...command,
    }, '*')
    return true
  }, [])

  const flushPendingMemoryCommand = useCallback(() => {
    const command = pendingMemoryCommandRef.current
    if (!command) return
    if (sendMemoryWidgetCommand(command)) pendingMemoryCommandRef.current = null
  }, [sendMemoryWidgetCommand])

  const openMemoryWidget = useCallback((expanded = true) => {
    bringPanelToFront('memory')
    setMemoryOpen(true)
    setMemoryExpanded(expanded)
    const panel = memoryPanelSize(expanded)
    setMemoryFrame((frame) => ({
      x: clamp(frame.x, 8, Math.max(8, window.innerWidth - panel.width - 8)),
      y: clamp(frame.y, 62, Math.max(62, window.innerHeight - panel.height - 8)),
    }))
    window.setTimeout(() => syncMemoryWidgetView(expanded ? 'expanded' : 'compact'), 0)
  }, [bringPanelToFront, syncMemoryWidgetView])

  const createMemoryFromHost = useCallback(() => {
    pendingMemoryCommandRef.current = { action: 'create' }
    openMemoryWidget(true)
    window.setTimeout(flushPendingMemoryCommand, 80)
  }, [flushPendingMemoryCommand, openMemoryWidget])

  const openMemoryFromResolver = useCallback((memoryId: string) => {
    if (!memoryId) return false
    pendingMemoryCommandRef.current = { action: 'open', memory_id: memoryId }
    openMemoryWidget(true)
    window.setTimeout(flushPendingMemoryCommand, 80)
    return true
  }, [flushPendingMemoryCommand, openMemoryWidget])

  const startMemoryDrag = useCallback((event: React.PointerEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest('button')) return
    bringPanelToFront('memory')
    event.preventDefault()
    const dragTarget = event.currentTarget
    try {
      dragTarget.setPointerCapture?.(event.pointerId)
    } catch {
      // Some embedded browsers do not expose pointer capture consistently.
    }
    document.body.classList.add('scene-moving-memory')
    const startX = event.clientX
    const startY = event.clientY
    const startFrame = memoryFrame
    const panel = memoryPanelSize(memoryExpanded)
    const onMove = (move: PointerEvent) => {
      setMemoryFrame({
        x: clamp(startFrame.x + move.clientX - startX, 8, Math.max(8, window.innerWidth - panel.width - 8)),
        y: clamp(startFrame.y + move.clientY - startY, 62, Math.max(62, window.innerHeight - panel.height - 8)),
      })
    }
    const finish = () => {
      try {
        dragTarget.releasePointerCapture?.(event.pointerId)
      } catch {
        // The pointer may already have been released by the browser.
      }
      document.body.classList.remove('scene-moving-memory')
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
      window.removeEventListener('blur', finish)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', finish, { once: true })
    window.addEventListener('pointercancel', finish, { once: true })
    window.addEventListener('blur', finish, { once: true })
  }, [bringPanelToFront, memoryExpanded, memoryFrame])

  const startChatDrag = useCallback((event: React.PointerEvent<HTMLElement>) => {
    if (chatExpanded || (event.target as HTMLElement).closest('button')) return
    bringPanelToFront('chat')
    event.preventDefault()
    const dragTarget = event.currentTarget
    try {
      dragTarget.setPointerCapture?.(event.pointerId)
    } catch {
      // Pointer capture is not guaranteed in every embedded browser.
    }
    document.body.classList.add('scene-moving-chat')
    const startX = event.clientX
    const startY = event.clientY
    const startFrame = chatFrame
    const panel = chatPanelSize(false, chatWidth, chatSizing.height)
    const onMove = (move: PointerEvent) => {
      setChatFrame({
        x: clamp(startFrame.x + move.clientX - startX, 8, Math.max(8, window.innerWidth - panel.width - 60)),
        y: clamp(startFrame.y + move.clientY - startY, 62, Math.max(62, window.innerHeight - panel.height - 8)),
      })
    }
    const finish = () => {
      try {
        dragTarget.releasePointerCapture?.(event.pointerId)
      } catch {
        // The pointer may already have been released by the browser.
      }
      document.body.classList.remove('scene-moving-chat')
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
      window.removeEventListener('blur', finish)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', finish, { once: true })
    window.addEventListener('pointercancel', finish, { once: true })
    window.addEventListener('blur', finish, { once: true })
  }, [bringPanelToFront, chatExpanded, chatFrame, chatSizing.height, chatWidth])

  const startChatResize = useCallback((event: React.PointerEvent<HTMLElement>) => {
    if (chatExpanded) return
    bringPanelToFront('chat')
    event.preventDefault()
    const startX = event.clientX
    const startWidth = chatWidth
    const startFrame = chatFrame
    const rightEdge = startFrame.x + startWidth
    const onMove = (move: PointerEvent) => {
      const nextWidth = clamp(startWidth + startX - move.clientX, chatSizing.minWidth, Math.min(chatSizing.maxWidth, window.innerWidth - 108))
      setChatWidth(nextWidth)
      setChatFrame((frame) => ({
        ...frame,
        x: clamp(rightEdge - nextWidth, 8, Math.max(8, window.innerWidth - nextWidth - 60)),
      }))
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp, { once: true })
  }, [bringPanelToFront, chatExpanded, chatFrame, chatSizing.maxWidth, chatSizing.minWidth, chatWidth])

  const attachContexts = useCallback((messageType: string, contexts: CanvasContextItem[]) => {
    if (!contexts.length) return
    sendToChat({
      type: messageType,
      source: 'versatile.scene',
      contexts,
    })
    setNotice(`Attached ${contexts.length} item${contexts.length === 1 ? '' : 's'} to chat.`)
  }, [sendToChat])

  const applyPatchResponse = useCallback((response: CanvasPatchResponse) => {
    if (!response.ok) return
    const event = normalizeCanvasPatchEvent(response.ui_event ?? response)
    if (!event) return
    setCanvasPatchEvent(event)
    setCanvases((current) => upsertCanvasDefinition(current, canvasFromPatchEvent(event, activeCanvas)))
  }, [activeCanvas])

  const patchCanvas = useCallback(async (input: CanvasPatchInput): Promise<CanvasPatchResponse> => {
    const messageId = timestampId('dbmsg')
    const payload = await publishDataBusAndWait(ctx, {
      message_id: messageId,
      subject: CANVAS_SUBJECT,
      object_ref: objectRefForPatch(input),
      idempotency_key: messageId,
      payload: input as unknown as Record<string, unknown>,
      client: {
        surface: 'versatile.scene',
        operation: 'canvas_patch',
      },
    })
    const response = payload as unknown as CanvasPatchResponse
    applyPatchResponse(response)
    return response
  }, [applyPatchResponse, ctx])

  const readCanvas = useCallback((input: CanvasReadInput): Promise<CanvasReadResponse> => (
    postOperation<CanvasReadInput, CanvasReadResponse>(ctx, 'canvas_read', {
      story_id: CANVAS_STORY_ID,
      ...input,
    })
  ), [ctx])

  const loadCanvas = useCallback(async () => {
    try {
      const list = await postOperation<{ story_id: string }, CanvasListResponse>(ctx, 'canvas_list', {
        story_id: CANVAS_STORY_ID,
      })
      const listed = (list.canvases ?? []).map(canvasFromListItem)
      const main = await readCanvas({ story_id: CANVAS_STORY_ID, canvas_name: activeCanvasName })
      const mainCanvas = main.ok
        ? canvasFromReadResponse(main, emptyCanvasDefinition(activeCanvasName))
        : emptyCanvasDefinition(activeCanvasName)
      setCanvases((current) => upsertCanvasDefinition(listed.length ? listed : current, mainCanvas))
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setNotice(`Canvas load failed: ${message}`)
    }
  }, [activeCanvasName, ctx, readCanvas])

  const uploadCanvasFiles = useCallback((payload: Record<string, unknown>, files: File[]) => (
    uploadCanvasAttachments(ctx, payload, files)
  ), [ctx])

  const canvasIngressClient = useMemo(() => ({
    patchCanvas,
    uploadCanvasAttachments: uploadCanvasFiles,
  }), [patchCanvas, uploadCanvasFiles])

  const canvasTarget = useCallback((rect?: CanvasCard['rect']) => ({
    storyId: CANVAS_STORY_ID,
    canvasId: activeCanvas.id,
    canvasName: activeCanvas.name,
    baseRevision: activeCanvas.revision,
    rect,
  }), [activeCanvas])

  const pinDroppedFilesToCanvas = useCallback((files: File[], rect: CanvasCard['rect']) => {
    void uploadAndPinFiles(files, canvasTarget(rect), canvasIngressClient, { placement: 'placed', rect })
      .then(applyPatchResponse)
      .catch((error) => setNotice(error instanceof Error ? error.message : String(error)))
  }, [applyPatchResponse, canvasIngressClient, canvasTarget])

  const pinDroppedTextToCanvas = useCallback((text: string, rect: CanvasCard['rect']) => {
    void applyCanvasCards(
      [cardFromSelectedText(text, { placement: 'placed', rect })],
      canvasTarget(rect),
      canvasIngressClient,
    ).then(applyPatchResponse).catch((error) => setNotice(error instanceof Error ? error.message : String(error)))
  }, [applyPatchResponse, canvasIngressClient, canvasTarget])

  const pinDroppedContextToCanvas = useCallback((context: CanvasContextItem, rect: CanvasCard['rect']) => {
    void applyCanvasCards(
      [cardFromContext(context, rect)],
      canvasTarget(rect),
      canvasIngressClient,
    ).then(applyPatchResponse).catch((error) => setNotice(error instanceof Error ? error.message : String(error)))
  }, [applyPatchResponse, canvasIngressClient, canvasTarget])

  const pinIngressPayloadToCanvas = useCallback((payload: CanvasIngressPayload, rect: CanvasCard['rect']) => {
    void applyCanvasCards(
      [cardFromIngress(payload, rect)],
      canvasTarget(rect),
      canvasIngressClient,
    ).then(applyPatchResponse).catch((error) => setNotice(error instanceof Error ? error.message : String(error)))
  }, [applyPatchResponse, canvasIngressClient, canvasTarget])

  const handleCanvasObjectAction = useCallback(async (
    card: CanvasCard,
    action: CanvasObjectActionName,
  ): Promise<CanvasObjectActionResponse> => {
    console.info('[versatile:canvas] object action request', {
      action,
      cardId: card.id,
      ref: card.ref,
      kind: card.kind,
    })
    const response = await postOperation<unknown, CanvasObjectActionResponse>(ctx, 'canvas_object_action', {
      action,
      object_ref: card.ref,
      card_id: card.id,
      canvas_id: activeCanvas.id,
      canvas_name: activeCanvas.name,
      story_id: CANVAS_STORY_ID,
      mime: card.mime,
    })
    console.info('[versatile:canvas] object action response', {
      action,
      cardId: card.id,
      ref: card.ref,
      ok: response.ok,
      namespace: response.namespace,
      resolver: response.resolver,
      resolverStatus: response.resolver_status,
      hasContent: Boolean(response.content_base64),
      targetSurface: response.ui_event?.target_surface,
      error: response.error,
    })
    if (!response.ok) {
      setNotice(response.error || response.message || `Canvas object ${action} failed.`)
      return response
    }
    if (action === 'download') {
      if (response.content_base64) {
        downloadBase64File(
          response.content_base64,
          response.filename || card.title || card.id,
          response.mime || card.mime || 'application/octet-stream',
        )
        setNotice(`Downloaded ${response.filename || card.title}.`)
      } else {
        setNotice(response.error || response.message || `No downloadable content returned for ${card.title || card.id}.`)
      }
    }
    if (action === 'open') {
      if (response.ui_event?.target_surface === 'sdk.memory.viewer') {
        const memoryId = memoryIdFromCanvasActionResponse(response)
        const opened = openMemoryFromResolver(memoryId)
        setNotice(opened ? `Opened memory ${memoryId}.` : `Memory resolver did not return a memory id for ${card.title || card.id}.`)
      } else {
        setNotice(response.error || response.message || `No open target returned for ${card.title || card.id}.`)
      }
    }
    if (action === 'preview') {
      setNotice(`Resolved preview for ${response.title || card.title}.`)
    }
    return response
  }, [activeCanvas.id, activeCanvas.name, ctx, openMemoryFromResolver])

  useEffect(() => {
    requestRuntimeConfig()
      .then((config) => {
        setCtx(contextFromConfig(config, fallback))
        setReady(true)
      })
      .catch(() => setReady(true))
  }, [fallback])

  useEffect(() => {
    if (!ready) return
    void loadCanvas()
  }, [loadCanvas, ready])

  useEffect(() => {
    function onMessage(event: MessageEvent): void {
      const data = event.data ?? {}
      const childWindows = [
        chatFrameRef.current?.contentWindow,
        memoryFrameRef.current?.contentWindow,
      ].filter(Boolean)

      if (childWindows.includes(event.source as Window)) {
        if (['CONFIG_REQUEST', 'kdcube-auth-required', 'kdcube-resize'].includes(data.type)) {
          if (event.source === memoryFrameRef.current?.contentWindow && data.type === 'kdcube-resize') {
            const height = Number(data.height)
            setMemoryContentHeight(Number.isFinite(height) && height > 0 ? Math.ceil(height) : null)
          }
          window.parent?.postMessage(data, '*')
          return
        }
        if (data.type === 'kdcube-widget-view') {
          if (data.widget === CHAT_WIDGET_ALIAS) {
            bringPanelToFront('chat')
            setChatOpen(true)
            setChatExpanded(data.view === 'expanded')
            return
          }
          if (data.widget === MEMORY_WIDGET_ALIAS) {
            bringPanelToFront('memory')
            setMemoryOpen(true)
            setMemoryExpanded(data.view === 'expanded')
            return
          }
          return
        }
        if (data.type === 'kdcube-widget-focus') {
          if (data.widget === CHAT_WIDGET_ALIAS) bringPanelToFront('chat')
          if (data.widget === MEMORY_WIDGET_ALIAS) bringPanelToFront('memory')
          return
        }
        if (data.type === 'kdcube-memory-widget-status' && data.widget === MEMORY_WIDGET_ALIAS) {
          const count = Number(data.count)
          setMemoryCount(Number.isFinite(count) ? count : null)
          flushPendingMemoryCommand()
          return
        }
        if (data.type === 'kdcube-set-view') {
          if (data.widget === MEMORY_WIDGET_ALIAS) {
            setMemoryExpanded(data.view === 'expanded')
            return
          }
          setChatExpanded(data.view === 'expanded')
          return
        }
        if (['kdcube-context-attach', 'kdcube-context-focus', 'kdcube-context-remove'].includes(data.type)) {
          sendToChat(data)
          return
        }
        if (data.type === 'kdcube-canvas-ingress') {
          const payload = data.payload as CanvasIngressPayload | undefined
          if (payload) {
            pinIngressPayloadToCanvas(payload, { x: 40, y: 40, w: 246, h: 112 })
          }
          return
        }
      }

      if (['CONFIG_RESPONSE', 'CONN_RESPONSE'].includes(data.type)) {
        childWindows.forEach((target) => target?.postMessage(data, '*'))
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [bringPanelToFront, pinIngressPayloadToCanvas, sendToChat])

  useEffect(() => {
    syncChatWidgetView(chatExpanded ? 'expanded' : 'compact')
  }, [chatExpanded, syncChatWidgetView])

  useEffect(() => {
    syncMemoryWidgetView(memoryExpanded ? 'expanded' : 'compact')
  }, [memoryExpanded, syncMemoryWidgetView])

  if (!ready) {
    return <div className="boot">Loading versatile scene...</div>
  }

  const scope = `${ctx.tenant} / ${ctx.project}`
  return (
    <main className="scene" style={{ '--versatile-chat-width': `${chatWidth}px` } as CSSProperties}>
      <header className="scene-header">
        <div className="brand">
          <Bot size={24} />
          <div>
            <span className="eyebrow">KDCube</span>
            <span className="title">Versatile Scene</span>
          </div>
        </div>
        <div className="status" title={scope}>
          <span className="dot" aria-hidden="true" />
          <span>{scope}</span>
        </div>
      </header>

      <section className={`scene-body ${chatOpen ? '' : 'chat-collapsed'} ${chatExpanded ? 'chat-expanded' : ''}`}>
        <section className={`scene-panel canvas-shell ${canvasOpen ? '' : 'canvas-hidden'}`}>
          {canvasOpen ? (
            <>
              {notice ? (
                <div className="notice">
                  <span>{notice}</span>
                  <button type="button" aria-label="Dismiss notice" onClick={() => setNotice('')}>
                    <X size={14} />
                  </button>
                </div>
              ) : null}
              <CanvasBoard
                activeCanvasName={activeCanvasName}
                canvases={canvases}
                canvasPatchEvent={canvasPatchEvent}
                patchCanvas={patchCanvas}
                readCanvas={readCanvas}
                onCanvasChange={setActiveCanvasName}
                onAttachCanvas={(context) => attachContexts('kdcube-context-attach', [context])}
                onAttachCard={(context) => attachContexts('kdcube-context-focus', Array.isArray(context) ? context : [context])}
                onDragCard={() => undefined}
                onCloseCanvas={() => setCanvasOpen(false)}
                onDropFiles={pinDroppedFilesToCanvas}
                onDropText={pinDroppedTextToCanvas}
                onDropContext={pinDroppedContextToCanvas}
                onDropIngress={pinIngressPayloadToCanvas}
                onObjectAction={handleCanvasObjectAction}
              />
            </>
          ) : (
            <div className="canvas-closed-state">
              <strong>Canvas hidden</strong>
              <span>The workspace column is kept stable so chat and tools stay in place.</span>
              <button type="button" onClick={() => setCanvasOpen(true)}>Open canvas</button>
            </div>
          )}
        </section>

        <div className="scene-rail" aria-label="Scene widgets">
          <button
            type="button"
            className="scene-rail-button chat-shortcut"
            title={chatOpen ? 'Collapse chat' : 'Open chat'}
            aria-label={chatOpen ? 'Collapse chat' : 'Open chat'}
            aria-pressed={chatOpen}
            onClick={() => {
              bringPanelToFront('chat')
              setChatOpen((open) => {
                const next = !open
                if (!next) setChatExpanded(false)
                if (next) setChatFrame(defaultChatFrame(chatWidth, chatExpanded, chatSizing.height))
                return next
              })
            }}
          >
            <KubeRobotIcon size={24} />
          </button>
          <button
            type="button"
            className="scene-rail-button canvas-shortcut"
            title={canvasOpen ? 'Hide canvas' : 'Open canvas'}
            aria-label={canvasOpen ? 'Hide canvas' : 'Open canvas'}
            aria-pressed={canvasOpen}
            onClick={() => setCanvasOpen((value) => !value)}
          >
            <GlowPinnedCanvasIcon size={24} />
          </button>
          <button
            type="button"
            className="scene-rail-button memory-shortcut"
            title={memoryOpen ? 'Hide memories' : 'Open memories'}
            aria-label={memoryOpen ? 'Hide memories' : 'Open memories'}
            aria-pressed={memoryOpen}
            onClick={() => {
              bringPanelToFront('memory')
              setMemoryOpen((open) => {
                const next = !open
                if (!next) {
                  setMemoryExpanded(false)
                } else {
                  setMemoryFrame(defaultMemoryFrame(chatWidth, chatOpen, memoryExpanded))
                }
                return next
              })
            }}
          >
            <Archive size={21} strokeWidth={2.1} />
          </button>
        </div>
      </section>
      <aside
        className={`scene-side ${chatOpen ? '' : 'collapsed'} ${chatExpanded ? 'expanded' : ''}`}
        style={chatExpanded ? ({
          zIndex: panelZ.chat,
        } as CSSProperties) : ({
          left: chatFrame.x,
          top: chatFrame.y,
          width: chatWidth,
          zIndex: panelZ.chat,
          '--versatile-chat-pane-height': `${chatPanelSize(false, chatWidth, chatSizing.height).height}px`,
        } as CSSProperties)}
        aria-hidden={!chatOpen}
        onPointerDownCapture={() => bringPanelToFront('chat')}
      >
        <header className="chat-pane-header" onPointerDown={startChatDrag}>
          <span>Chat</span>
          <div>
            <button
              type="button"
              onClick={() => setChatExpanded((value) => !value)}
              title={chatExpanded ? 'Compact chat' : 'Enlarge chat'}
              aria-label={chatExpanded ? 'Compact chat' : 'Enlarge chat'}
            >
              {chatExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            </button>
            <button
              type="button"
              onClick={() => {
                setChatOpen(false)
                setChatExpanded(false)
              }}
              title="Close chat"
              aria-label="Close chat"
            >
              <X size={14} />
            </button>
          </div>
        </header>
        {chatOpen && !chatExpanded ? (
          <button
            type="button"
            className="chat-width-handle"
            title="Resize chat"
            aria-label="Resize chat"
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture?.(event.pointerId)
              startChatResize(event)
            }}
          />
        ) : null}
        <div className="chat-frame-shell">
          <iframe
            ref={chatFrameRef}
            className="chat-frame"
            title="Versatile chat widget"
            src={chatWidgetUrl(ctx)}
            onLoad={() => syncChatWidgetView(chatExpanded ? 'expanded' : 'compact')}
          />
        </div>
      </aside>
      {memoryOpen ? (
        <section
          className={`memory-pane${memoryExpanded ? ' expanded' : ''}`}
          style={{
            left: memoryFrame.x,
            top: memoryFrame.y,
            zIndex: panelZ.memory,
            '--memory-pane-height': `${compactMemoryPaneHeight(memoryContentHeight)}px`,
          } as CSSProperties}
          aria-label="Memories"
          onPointerDownCapture={() => bringPanelToFront('memory')}
        >
          <header onPointerDown={startMemoryDrag}>
            <span className="memory-pane-title">
              <strong>Memories</strong>
              {memoryCount !== null ? <small>{memoryCount} in scope</small> : null}
            </span>
            <div>
              <button
                type="button"
                className="memory-pane-add"
                onClick={createMemoryFromHost}
                title="Add memory"
                aria-label="Add memory"
              >
                <Plus size={14} />
              </button>
              <button
                type="button"
                onClick={() => {
                  setMemoryExpanded((value) => {
                    const next = !value
                    const panel = memoryPanelSize(next)
                    setMemoryFrame((frame) => ({
                      x: clamp(frame.x, 8, Math.max(8, window.innerWidth - panel.width - 8)),
                      y: clamp(frame.y, 62, Math.max(62, window.innerHeight - panel.height - 8)),
                    }))
                    return next
                  })
                }}
                title={memoryExpanded ? 'Compact memories' : 'Enlarge memories'}
                aria-label={memoryExpanded ? 'Compact memories' : 'Enlarge memories'}
              >
                {memoryExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              </button>
              <button
                type="button"
                onClick={() => {
                  setMemoryOpen(false)
                  setMemoryExpanded(false)
                }}
                title="Close memories"
                aria-label="Close memories"
              >
                <X size={14} />
              </button>
            </div>
          </header>
          <iframe
            ref={memoryFrameRef}
            className="memory-frame"
            title="Versatile memories"
            src={memoryWidgetUrl(ctx, false)}
            onLoad={() => {
              syncMemoryWidgetView(memoryExpanded ? 'expanded' : 'compact')
              flushPendingMemoryCommand()
            }}
          />
        </section>
      ) : null}
      <div className="context-source" hidden>
        {JSON.stringify(canvasContext(activeCanvas))}
      </div>
    </main>
  )
}

const rootNode = document.getElementById('app')
if (!rootNode) {
  throw new Error('Missing #app root')
}

createRoot(rootNode).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
