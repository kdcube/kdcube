/**
 * SSE transport for the chat stream.
 *
 * `openChatStream` opens an `EventSource` against `/sse/stream`,
 * registers JSON listeners for chat_start / chat_step / chat_delta /
 * chat_complete / chat_error / conv_status / chat_service, and resolves
 * once the stream is open (or rejects on timeout / error).
 *
 * Moved verbatim from src/service.ts (Wave 4).
 */

import { createLocalId, settings } from '../settings.ts'
import { fetchProfileSessionId } from './transport.ts'
import type {
  OpenChatStreamOptions,
  OpenChatStreamResult,
} from './types.ts'

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

export async function openChatStream(options: OpenChatStreamOptions): Promise<OpenChatStreamResult> {
  const sessionId = await fetchProfileSessionId(options.sessionId)
  const streamId = createLocalId('stream')
  const timeoutMs = options.timeoutMs ?? 8000

  let eventSource: EventSource | null = null

  await new Promise<void>((resolve, reject) => {
    const url = new URL(`${settings.getBaseUrl()}/sse/stream`)
    url.searchParams.set('user_session_id', sessionId)
    url.searchParams.set('stream_id', streamId)
    if (settings.getTenant()) url.searchParams.set('tenant', settings.getTenant())
    if (settings.getProject()) url.searchParams.set('project', settings.getProject())
    if (settings.getAccessToken()) url.searchParams.set('bearer_token', settings.getAccessToken()!)
    if (settings.getIdToken()) url.searchParams.set('id_token', settings.getIdToken()!)

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
      options.onDisconnect?.()
    })
  })

  return {
    eventSource: eventSource!,
    sessionId,
    streamId,
  }
}
