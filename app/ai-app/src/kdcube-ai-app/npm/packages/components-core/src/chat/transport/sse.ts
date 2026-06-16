/**
 * SSE transport for the chat stream. Opens an `EventSource` against
 * `/sse/stream`, registers JSON listeners, resolves once open (or rejects on
 * timeout/error).
 *
 * Ported from the widget's `api/sseTransport.ts`; `settings` → `EngineRuntime`,
 * tokens resolved via `runtime.getTokens()` for query-param auth.
 */
import type { EngineRuntime } from '../runtime.ts'
import type { OpenChatStreamOptions, OpenChatStreamResult } from '../protocol.ts'
import { fetchProfileSessionId } from './http.ts'

function addJsonListener<T>(
  eventSource: EventSource,
  eventName: string,
  handler?: (payload: T) => void,
): void {
  if (!handler) return

  eventSource.addEventListener(eventName, (event: MessageEvent) => {
    try {
      handler(JSON.parse(event.data) as T)
    } catch (error) {
      console.error('Malformed SSE event', eventName, error)
    }
  })
}

export async function openChatStream(runtime: EngineRuntime, options: OpenChatStreamOptions): Promise<OpenChatStreamResult> {
  const sessionId = await fetchProfileSessionId(runtime, options.sessionId)
  const streamId = runtime.createLocalId('stream')
  const timeoutMs = options.timeoutMs ?? 8000
  const { accessToken, idToken } = await runtime.getTokens()

  let eventSource: EventSource | null = null

  await new Promise<void>((resolve, reject) => {
    const url = new URL(`${runtime.baseUrl}/sse/stream`)
    url.searchParams.set('user_session_id', sessionId)
    url.searchParams.set('stream_id', streamId)
    if (runtime.tenant) url.searchParams.set('tenant', runtime.tenant)
    if (runtime.project) url.searchParams.set('project', runtime.project)
    if (accessToken) url.searchParams.set('bearer_token', accessToken)
    if (idToken) url.searchParams.set('id_token', idToken)

    eventSource = new EventSource(url.toString(), { withCredentials: true })

    addJsonListener(eventSource, 'chat_start', options.onChatStart)
    addJsonListener(eventSource, 'chat_step', options.onChatStep)
    addJsonListener(eventSource, 'chat_delta', options.onChatDelta)
    addJsonListener(eventSource, 'chat_complete', options.onChatComplete)
    addJsonListener(eventSource, 'chat_error', options.onChatError)
    addJsonListener(eventSource, 'conv_status', options.onConversationStatus)
    addJsonListener(eventSource, 'chat_service', options.onChatService)

    let opened = false
    const timeout = window.setTimeout(() => {
      if (!opened) {
        eventSource?.close()
        reject(new Error('Timed out connecting to the event stream.'))
      }
    }, timeoutMs)

    eventSource.addEventListener('open', () => {
      opened = true
      window.clearTimeout(timeout)
      resolve()
    })

    eventSource.addEventListener('error', () => {
      if (!opened) {
        window.clearTimeout(timeout)
        eventSource?.close()
        reject(new Error('Unable to open the event stream.'))
        return
      }
      eventSource?.close()
      options.onDisconnect?.('event_stream_error')
    })
  })

  return {
    eventSource: eventSource!,
    sessionId,
    streamId,
  }
}
