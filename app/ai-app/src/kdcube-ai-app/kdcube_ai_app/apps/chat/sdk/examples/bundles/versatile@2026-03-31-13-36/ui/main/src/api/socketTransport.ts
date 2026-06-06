import { Manager, Socket } from 'socket.io-client'
import { createLocalId, settings } from '../settings.ts'
import { buildEventSubmission } from './client.ts'
import { fetchProfileSessionId } from './transport.ts'
import type {
  ChatCompleteEnvelope,
  ChatDeltaEnvelope,
  ChatErrorEnvelope,
  ChatServiceEnvelope,
  ChatStartEnvelope,
  ChatStepEnvelope,
  ConvStatusEnvelope,
  OpenChatStreamOptions,
  SubmitChatMessageParams,
  SubmitChatMessageResponse,
} from './types.ts'

type EngineKey = string
const managers = new Map<EngineKey, Manager>()

export interface DataBusMessageInput {
  message_id?: string
  subject: string
  object_ref?: string
  idempotency_key?: string
  payload: Record<string, unknown>
  client?: Record<string, unknown>
  trace?: Record<string, unknown>
  created_at?: string
}

export interface DataBusPublishParams {
  bundleId: string
  messages: DataBusMessageInput[]
}

export interface DataBusPublishAck {
  schema?: string
  status: 'accepted' | 'partial' | 'rejected' | string
  accepted?: Array<Record<string, unknown>>
  rejected?: Array<Record<string, unknown>>
}

export interface OpenSocketTransportOptions extends OpenChatStreamOptions {
  path?: string
  namespace?: string
  bundleId?: string
}

export interface OpenSocketTransportResult {
  socket: Socket
  sessionId: string
  streamId: string
  close: () => void
  sendChatMessage: (
    params: SubmitChatMessageParams,
  ) => Promise<SubmitChatMessageResponse>
  publishDataBus: (params: DataBusPublishParams) => Promise<DataBusPublishAck>
}

function managerFor(baseUrl: string, path: string): Manager {
  const key: EngineKey = `${baseUrl}|${path}`
  let manager = managers.get(key)
  if (!manager) {
    manager = new Manager(baseUrl, {
      path,
      transports: ['websocket', 'polling'],
      upgrade: false,
      autoConnect: false,
      withCredentials: true,
      reconnectionAttempts: 3,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      randomizationFactor: 0.25,
    })
    managers.set(key, manager)
  }
  return manager
}

function bindJson<T>(socket: Socket, eventName: string, handler?: (payload: T) => void): void {
  if (!handler) return
  socket.on(eventName, (payload: unknown) => handler(payload as T))
}

function authPayload(sessionId: string, streamId: string, bundleId: string): Record<string, unknown> {
  const auth: Record<string, unknown> = {
    user_session_id: sessionId,
    stream_id: streamId,
    tenant: settings.getTenant(),
    project: settings.getProject(),
    bundle_id: bundleId,
  }
  if (settings.getAccessToken()) auth.bearer_token = settings.getAccessToken()
  if (settings.getIdToken()) auth.id_token = settings.getIdToken()
  return auth
}

export async function openSocketTransport(
  options: OpenSocketTransportOptions,
): Promise<OpenSocketTransportResult> {
  const sessionId = await fetchProfileSessionId(options.sessionId)
  const streamId = createLocalId('stream')
  const bundleId = options.bundleId || settings.getBundleId()
  const socket = managerFor(settings.getBaseUrl(), options.path || '/socket.io')
    .socket(options.namespace || '/', { auth: authPayload(sessionId, streamId, bundleId) })

  bindJson<ChatStartEnvelope>(socket, 'chat_start', options.onChatStart)
  bindJson<ChatStepEnvelope>(socket, 'chat_step', options.onChatStep)
  bindJson<ChatDeltaEnvelope>(socket, 'chat_delta', options.onChatDelta)
  bindJson<ChatCompleteEnvelope>(socket, 'chat_complete', options.onChatComplete)
  bindJson<ChatErrorEnvelope>(socket, 'chat_error', options.onChatError)
  bindJson<ConvStatusEnvelope>(socket, 'conv_status', options.onConversationStatus)
  bindJson<ChatServiceEnvelope>(socket, 'chat_service', options.onChatService)
  socket.on('disconnect', (reason: string) => options.onDisconnect?.(reason))

  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      socket.disconnect()
      reject(new Error('Timed out connecting to the Socket.IO transport.'))
    }, options.timeoutMs ?? 8000)
    socket.once('connect', () => {
      window.clearTimeout(timeout)
      resolve()
    })
    socket.once('connect_error', (error: unknown) => {
      window.clearTimeout(timeout)
      reject(error instanceof Error ? error : new Error(String(error)))
    })
    socket.connect()
  })

  return {
    socket,
    sessionId,
    streamId,
    close: () => socket.disconnect(),
    sendChatMessage: async (params) => {
      const submission = buildEventSubmission(params, settings.getTenant(), settings.getProject())
      const buffers = await Promise.all(params.files.map((file) => file.arrayBuffer()))
      return socket.emitWithAck('chat_message', submission, ...buffers)
    },
    publishDataBus: async (params) => socket.emitWithAck('data_bus.publish', {
      schema: 'kdcube.data_bus.ingress.v1',
      bundle_id: params.bundleId,
      messages: params.messages,
    }),
  }
}
