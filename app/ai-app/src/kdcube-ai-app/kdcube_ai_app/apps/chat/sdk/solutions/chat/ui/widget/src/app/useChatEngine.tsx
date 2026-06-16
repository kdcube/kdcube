/**
 * useChatEngine — the reusable chat "engine": transport wiring, the send
 * pipeline (+ serialization queue), conversation lifecycle, host-message
 * handling, SSE/auth boot, context attachment, feedback, downloads, and the
 * host view-form state.
 *
 * All of this was previously entangled in the widget's App.tsx. It now lives
 * here so a custom UI can drive the chat without re-implementing orchestration:
 *
 *   <ChatStoreProvider config={...}>
 *     <MyOwnChatUI />              // calls useChatEngine() and renders anything
 *   </ChatStoreProvider>
 *
 * The Redux slice + reducers + api transport were already reusable; this hook
 * is the missing "headless" layer between them and the view. The default
 * App.tsx is now just one consumer of `useChatEngine()`.
 *
 * The logic here is moved verbatim from App.tsx — the subtle bits are
 * preserved exactly: the send-queue serialization, the deliberate
 * `store.getState()` reads (NOT stateRef) inside the queued send to dodge the
 * post-commit microtask lag, the reconnect backoff, and the anonymous gates.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  deleteConversationById,
  downloadObjectRef,
  fetchConversationById,
  fetchTurnFeedbacks,
  listBundleConversations,
  openChatStream,
  previewReactContext,
  requestConversationStatus,
  submitChatMessage,
  submitTurnFeedback,
} from '../service.ts'
import type {
  BannerTone,
  ChatServiceEnvelope,
  ChatStepEnvelope,
  ConversationSummary,
  RateLimitPayload,
  ReactContextPreviewResponse,
  TurnReaction,
} from '../service.ts'
import {
  BUILT_BUNDLE_ID,
  CHAT_ATTACHMENT_EVENT_SOURCE_ID,
  CHAT_CANVAS_FOCUS_EVENT_SOURCE_ID,
  CHAT_CANVAS_PATCH_MESSAGE,
  CHAT_CANVAS_PATCH_SOURCE,
  CHAT_CANVAS_PATCH_STEP,
  CHAT_CANVAS_STATE_EVENT_SOURCE_ID,
  CHAT_CANVAS_SURFACE,
  CHAT_CONTEXT_EVENT_SOURCE_ID,
  CHAT_CONTEXT_REFRESH_SOURCE,
  CHAT_CONTEXT_REMOVE_MESSAGE,
  CHAT_SNAPSHOT_EVENT_SOURCE_ID,
  CHAT_SNAPSHOT_SURFACE,
  CHAT_SURFACE,
  CHAT_USER_EVENT_SOURCE_ID,
  createLocalId,
  settings,
} from '../settings.ts'
import type { AttachedContext, ChatState } from '../features/chat/chatTypes.ts'
import {
  buildChatHistory,
  fallbackRateLimitMessage,
  findActiveTurn,
  normalizeTurnAttachment,
} from '../features/chat/chatReducers.ts'
import { messageWithContextChips } from '../features/chat/contextChips.ts'
import { activateContextPin, contextPinActionNotice } from '../features/chat/contextPinActions.ts'
import type { ActionableContext } from '../features/chat/contextPinActions.ts'
import { messageForError } from '../components/utils.ts'
import { useAppDispatch, useAppSelector } from './hooks.ts'
import { store } from './store.ts'
import { chatActions } from '../features/chat/chatSlice.ts'
import { buildExternalEventBatch } from '../features/context/eventBatch.ts'
import { fetchProfile } from '../api/transport.ts'
import {
  isKdcubePreviewContext,
  recognizeContextMessage,
  recognizeContextRemoval,
  requestAuthRequired,
  requestHostView,
} from '../host.ts'

const AUTH_PROMPT_TEXT = 'Sign in to start chatting.'
const STREAM_RECONNECT_DELAYS_MS = [1000, 2500, 5000]
const STREAM_RECONNECT_STABLE_MS = 30000
const STREAM_RECONNECT_EXHAUSTED_TEXT = 'Connection lost. Send again or use Reconnect to open a fresh stream.'

const CHAT_EVENT_DEFAULTS = {
  userEventSourceId: CHAT_USER_EVENT_SOURCE_ID,
  attachmentEventSourceId: CHAT_ATTACHMENT_EVENT_SOURCE_ID,
  contextEventSourceId: CHAT_CONTEXT_EVENT_SOURCE_ID,
  chatSurface: CHAT_SURFACE,
  canvasStateEventSourceId: CHAT_CANVAS_STATE_EVENT_SOURCE_ID,
  canvasFocusEventSourceId: CHAT_CANVAS_FOCUS_EVENT_SOURCE_ID,
  canvasSurface: CHAT_CANVAS_SURFACE,
  snapshotEventSourceId: CHAT_SNAPSHOT_EVENT_SOURCE_ID,
  snapshotSurface: CHAT_SNAPSHOT_SURFACE,
}

function forwardCanvasPatchEvent(env: ChatStepEnvelope) {
  if (env.event?.step !== CHAT_CANVAS_PATCH_STEP) return
  if (!env.data || typeof env.data !== 'object') return
  window.parent?.postMessage(
    {
      type: CHAT_CANVAS_PATCH_MESSAGE,
      source: CHAT_CANVAS_PATCH_SOURCE,
      event: env.data,
    },
    '*',
  )
}

/** Map a raw send failure (HTTP POST rejection text) to a concise, friendly
 *  one-liner + tone. */
function describeSendError(raw: string): { text: string; tone: BannerTone } {
  if (/\b413\b/.test(raw) || /entity too large/i.test(raw)) {
    return { text: 'That attachment is too large to send.', tone: 'warning' }
  }
  if (/\b400\b/.test(raw) && /missing\b[^a-z]*message/i.test(raw)) {
    return { text: 'Add a short message to send with your attachment.', tone: 'warning' }
  }
  if (/no sse stream/i.test(raw)) {
    return { text: 'Connection lost — please try again.', tone: 'warning' }
  }
  if (/\b404\b/.test(raw)) {
    return { text: 'This conversation is no longer available.', tone: 'error' }
  }
  return { text: 'Couldn’t send your message. Please try again.', tone: 'error' }
}

function storyIdFromContexts(contexts: AttachedContext[]): string | undefined {
  for (const context of contexts) {
    const story = context.data?.story_id
    if (typeof story === 'string' && story.trim()) return story.trim()
  }
  return undefined
}

function chatTarget(storyId?: string): Record<string, unknown> {
  const target: Record<string, unknown> = {
    agent_id: 'main',
    surface: CHAT_SURFACE,
    story_kind: 'general_chat',
    conversation_role: 'main',
    event_source_id: CHAT_USER_EVENT_SOURCE_ID,
  }
  if (storyId) target.story_id = storyId
  return target
}

export type HostView = 'compact' | 'expanded'

export interface ChatEngine {
  state: ChatState
  ready: boolean
  bootError: string | null
  setBootError: (value: string | null) => void
  authed: boolean
  hostView: HostView
  /** Set the view form and notify the host (used by the expand/collapse control). */
  setHostView: (next: HostView) => void
  /** Flip the view form locally only (dev preview inside a same-origin frame). */
  setHostViewLocal: () => void
  kdcubePreview: boolean
  bundleId: string
  /** Send the composer draft (or `textOverride`); optional reactive event type. */
  send: (textOverride?: string, requestedReactiveEventType?: string) => void
  /** Steer the active turn (interrupt-and-redirect). */
  steer: () => void
  loadConversation: (conversationId: string) => void
  newChat: () => void
  deleteConversation: (conversation: ConversationSummary) => void
  refreshConversationList: () => void
  /** Attach one or more host-provided context chips to the composer. */
  attachContext: (contexts: AttachedContext | AttachedContext[]) => void
  /** Remove attached context chip(s) and sync the host. */
  removeContext: (ids: string | string[]) => void
  /** Open/activate a context chip via its resolver-declared default effect. */
  openContextChip: (context: ActionableContext) => void
  /** Download an object ref through the bundle's download transport. */
  downloadFile: (ref: string, filename?: string, mime?: string) => void
  submitFeedback: (turnId: string, reaction: TurnReaction | null, text?: string) => void
  handleReconnect: () => void
  pinConversationToCanvas: () => void
  promptLogin: () => void
  dryRun: {
    enabled: boolean
    loading: boolean
    preview: ReactContextPreviewResponse | null
    error: string | null
    setEnabled: (value: boolean) => void
    clearPreview: () => void
  }
}

// Exported so the package engine root (packageEngine.tsx) can provide the very same
// context instance that App.tsx consumes via useChatEngine() — both engine roots
// feed one ChatEngineContext.
export const ChatEngineContext = createContext<ChatEngine | null>(null)

/** Build the engine. Called exactly once (by ChatEngineHost) inside <Provider>. */
function useChatEngineImpl(): ChatEngine {
  const state = useAppSelector((s) => s.chat)
  const dispatch = useAppDispatch()
  const [ready, setReady] = useState(false)
  const [bootError, setBootError] = useState<string | null>(null)
  const [hostView, setHostViewState] = useState<HostView>(() =>
    typeof window !== 'undefined' && window.parent !== window ? 'compact' : 'expanded',
  )
  const [authed, setAuthed] = useState<boolean>(() =>
    Boolean(settings.getAccessToken() || settings.getIdToken()),
  )
  const authedRef = useRef<boolean>(authed)
  const applyAuthed = useCallback((next: boolean) => {
    authedRef.current = next
    setAuthed(next)
  }, [])
  const promptLogin = useCallback(() => {
    requestAuthRequired()
    const exists = store.getState().chat.banners.some((b) => b.text === AUTH_PROMPT_TEXT)
    if (!exists) dispatch(chatActions.pushBanner({ tone: 'info', text: AUTH_PROMPT_TEXT, placement: 'composer' }))
  }, [dispatch])

  const stateRef = useRef<ChatState>(state)
  const loadConversationRef = useRef<((conversationId: string) => void) | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  const connectPromiseRef = useRef<Promise<void> | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)
  const reconnectAttemptRef = useRef(0)
  const sessionIdRef = useRef<string | null>(null)
  const streamIdRef = useRef<string | null>(null)
  const sendQueueRef = useRef<Promise<void>>(Promise.resolve())
  const [dryRunEnabled, setDryRunEnabled] = useState(false)
  const [dryRunLoading, setDryRunLoading] = useState(false)
  const [dryRunPreview, setDryRunPreview] = useState<ReactContextPreviewResponse | null>(null)
  const [dryRunError, setDryRunError] = useState<string | null>(null)

  /* Host -> widget view sync, conversation pin opens, and context attach/remove. */
  useEffect(() => {
    function onHostMessage(event: MessageEvent) {
      const data = event.data
      if (!data || typeof data !== 'object') return
      if (data.type === 'kdcube-set-view') {
        if (data.view === 'compact' || data.view === 'expanded') setHostViewState(data.view)
        return
      }
      if (data.type === 'kdcube-chat-widget-command' && data.action === 'load-conversation') {
        const conversationId = typeof data.conversation_id === 'string' ? data.conversation_id.trim() : ''
        if (conversationId) {
          loadConversationRef.current?.(conversationId)
        }
        return
      }
      const removedContextIds = recognizeContextRemoval(data)
      if (removedContextIds.length > 0) {
        removedContextIds.forEach((id) => dispatch(chatActions.removeComposerContext(id)))
        return
      }
      const recognized = recognizeContextMessage(data)
      if (recognized.length > 0) {
        recognized.forEach((ctx) => dispatch(chatActions.addComposerContext(ctx)))
        const source = typeof data.source === 'string' ? data.source : ''
        const silent = data.silent === true || source === CHAT_CONTEXT_REFRESH_SOURCE
        if (!silent) {
          window.requestAnimationFrame(() => {
            const textarea = document.querySelector('.k-composer textarea') as HTMLTextAreaElement | null
            textarea?.focus()
          })
        }
      }
    }
    window.addEventListener('message', onHostMessage)
    return () => window.removeEventListener('message', onHostMessage)
  }, [dispatch])

  /* Dropped conversation pin (a `conv:` ref) loads that conversation. */
  useEffect(() => {
    const conversationIdFromTransfer = (dt: DataTransfer | null): string => {
      if (!dt) return ''
      const idFromConvRef = (ref: string): string => {
        const value = String(ref || '').trim()
        if (!value.startsWith('conv:')) return ''
        const parts = value.slice('conv:'.length).split('/')
        return (parts[parts.length - 1] || '').trim()
      }
      const fromJson = (raw: string): string => {
        if (!raw) return ''
        try {
          const parsed = JSON.parse(raw)
          const items = Array.isArray(parsed?.contexts) ? parsed.contexts : [parsed]
          for (const item of items) {
            if (!item || typeof item !== 'object') continue
            const kind = String(item.kind || '')
            const ref = String(item.ref || item.logical_path || item.id || '')
            if (kind === 'conversation' || ref.startsWith('conv:')) {
              const fromData = item.data && typeof item.data === 'object'
                ? String((item.data as Record<string, unknown>).conversation_id || '')
                : ''
              return fromData || idFromConvRef(ref)
            }
          }
        } catch {
          /* not JSON */
        }
        return ''
      }
      return fromJson(dt.getData('application/json')) || idFromConvRef(dt.getData('text/uri-list'))
    }
    const looksLikeContextDrag = (dt: DataTransfer | null): boolean =>
      !!dt && Array.from(dt.types || []).some((type) => type === 'application/json' || type === 'text/uri-list')
    const onDragOver = (event: DragEvent) => {
      if (!looksLikeContextDrag(event.dataTransfer)) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
    }
    const onDrop = (event: DragEvent) => {
      const conversationId = conversationIdFromTransfer(event.dataTransfer)
      if (!conversationId) return
      event.preventDefault()
      loadConversationRef.current?.(conversationId)
    }
    window.addEventListener('dragover', onDragOver)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('dragover', onDragOver)
      window.removeEventListener('drop', onDrop)
    }
  }, [])

  const setHostView = useCallback((next: HostView) => {
    setHostViewState(next)
    requestHostView(next)
  }, [])
  const kdcubePreview = useMemo(() => isKdcubePreviewContext(), [])
  const setHostViewLocal = useCallback(() => {
    setHostViewState((prev) => (prev === 'compact' ? 'expanded' : 'compact'))
  }, [])

  useEffect(() => {
    stateRef.current = state
  }, [state])

  const bundleId = settings.getBundleId() || BUILT_BUNDLE_ID

  const refreshConversationList = async () => {
    if (!bundleId) return
    if (!authedRef.current) {
      dispatch(chatActions.setConversations([]))
      return
    }
    dispatch(chatActions.setConversationsLoading(true))
    dispatch(chatActions.setConversationsError(null))
    try {
      const conversations = await listBundleConversations(bundleId)
      dispatch(chatActions.setConversations(conversations))
      dispatch(chatActions.setConversationsLoading(false))
    } catch (error) {
      const message = messageForError(error)
      dispatch(chatActions.setConversationsLoading(false))
      dispatch(chatActions.setConversationsError(message))
    }
  }

  const requestConversationStatusForCurrentStream = async (conversationId: string) => {
    const streamId = streamIdRef.current
    if (!streamId) return
    try {
      await requestConversationStatus(conversationId, streamId)
    } catch (error) {
      console.warn('Unable to request conversation status', error)
    }
  }

  const loadConversation = async (conversationId: string) => {
    dispatch(chatActions.setConversationLoadingId(conversationId))
    dispatch(chatActions.unlockInput())
    try {
      const conversation = await fetchConversationById(conversationId)
      dispatch(chatActions.hydrateConversation({ conversation }))
      dispatch(chatActions.clearComposer())
      dispatch(chatActions.setConversationLoadingId(null))
      void fetchTurnFeedbacks(conversation.conversation_id)
        .then((map) => dispatch(chatActions.setFeedbackMap(map)))
        .catch(() => {})
      if (stateRef.current.connection === 'connected') {
        void requestConversationStatusForCurrentStream(conversation.conversation_id)
      }
    } catch (error) {
      const message = messageForError(error)
      dispatch(chatActions.setConversationLoadingId(null))
      setBootError(message)
    }
  }
  loadConversationRef.current = (conversationId: string) => { void loadConversation(conversationId) }

  const startNewChat = () => {
    dispatch(chatActions.startNewConversation())
    dispatch(chatActions.clearComposer())
    dispatch(chatActions.unlockInput())
    dispatch(chatActions.setConversationLoadingId(null))
  }

  const deleteConversation = async (conversation: ConversationSummary) => {
    const label = conversation.title || conversation.id
    const ok = window.confirm(`Delete "${label}"? This cannot be undone.`)
    if (!ok) return
    dispatch(chatActions.setConversationDeletingId(conversation.id))
    try {
      await deleteConversationById(conversation.id)
      dispatch(chatActions.removeConversation(conversation.id))
    } catch (error) {
      dispatch(chatActions.pushBanner({
        tone: 'error',
        text: `Failed to delete conversation: ${messageForError(error)}`,
      }))
    } finally {
      dispatch(chatActions.setConversationDeletingId(null))
    }
  }

  const clearReconnectTimer = () => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
  }

  const closeTransport = () => {
    eventSourceRef.current?.close()
    eventSourceRef.current = null
    streamIdRef.current = null
    connectPromiseRef.current = null
  }

  const resetTransport = () => {
    clearReconnectTimer()
    closeTransport()
  }

  const pushReconnectExhaustedBanner = () => {
    const exists = store.getState().chat.banners.some((b) => b.text === STREAM_RECONNECT_EXHAUSTED_TEXT)
    if (!exists) {
      dispatch(chatActions.pushBanner({
        tone: 'warning',
        text: STREAM_RECONNECT_EXHAUSTED_TEXT,
        placement: 'composer',
      }))
    }
  }

  const scheduleStreamReconnect = (reason?: string) => {
    if (!authedRef.current || reconnectTimerRef.current !== null || connectPromiseRef.current) return
    const attempt = reconnectAttemptRef.current
    if (attempt >= STREAM_RECONNECT_DELAYS_MS.length) {
      console.warn('SSE stream reconnect attempts exhausted', { reason })
      pushReconnectExhaustedBanner()
      return
    }
    const delay = STREAM_RECONNECT_DELAYS_MS[attempt]
    reconnectAttemptRef.current = attempt + 1
    console.info('Scheduling SSE stream reconnect', { attempt: attempt + 1, delay, reason })
    reconnectTimerRef.current = window.setTimeout(() => {
      reconnectTimerRef.current = null
      void connectStream().catch((error) => {
        console.warn('SSE stream reconnect failed', error)
        scheduleStreamReconnect('reconnect_failed')
      })
    }, delay)
  }

  const handleStreamDisconnect = (reason?: string) => {
    closeTransport()
    dispatch(chatActions.setConnectionState('disconnected'))
    scheduleStreamReconnect(reason)
  }

  const handleServiceEvent = (env: ChatServiceEnvelope) => {
    const data = (env.data || {}) as Record<string, unknown>
    const rateLimit = (env.data?.rate_limit || null) as RateLimitPayload | null

    let tone: BannerTone = (rateLimit?.notification_type || data.notification_type || 'warning') as BannerTone
    let message = ''

    switch (env.type) {
      case 'rate_limit.warning':
      case 'rate_limit.denied':
      case 'rate_limit.post_run_exceeded':
        message = rateLimit?.user_message || fallbackRateLimitMessage(rateLimit || undefined, data)
        break
      case 'rate_limit.no_funding':
        tone = (rateLimit?.notification_type as BannerTone | undefined) || (data.notification_type as BannerTone | undefined) || 'warning'
        message =
          rateLimit?.user_message ||
          (data.user_message as string | undefined) ||
          'This service is not available for your account type.'
        break
      case 'rate_limit.subscription_exhausted':
        tone = (rateLimit?.notification_type as BannerTone | undefined) || (data.notification_type as BannerTone | undefined) || 'warning'
        message =
          rateLimit?.user_message ||
          (data.user_message as string | undefined) ||
          'Your subscription balance is exhausted. Top up your balance to continue.'
        break
      case 'rate_limit.project_exhausted': {
        tone = 'warning'
        const hasPersonalBudget = Boolean(data.has_personal_budget)
        const usdShort = typeof data.usd_short === 'number' ? data.usd_short : null
        if (hasPersonalBudget && usdShort && usdShort > 0) {
          message = `Project budget exhausted. You need $${usdShort.toFixed(2)} more in personal credits to run this request.`
        } else if (!hasPersonalBudget) {
          message = 'Project budget exhausted. Please contact your administrator to add funds.'
        } else {
          message = 'Project budget exhausted. Unable to process this request.'
        }
        break
      }
      case 'rate_limit.attachment_failure':
        tone = (rateLimit?.notification_type as BannerTone | undefined) || (data.notification_type as BannerTone | undefined) || 'error'
        message =
          rateLimit?.user_message ||
          (data.user_message as string | undefined) ||
          'Attachment was rejected.'
        break
      case 'rate_limit.lane_switch':
      case 'economics.user_underfunded_absorbed':
        return
      default: {
        const explicit = rateLimit?.user_message || (data.user_message as string | undefined)
        if (!explicit) {
          console.debug('Ignoring non-user-facing service event', env.type)
          return
        }
        message = explicit
      }
    }

    dispatch(chatActions.pushBanner({ tone, text: message, placement: 'composer' }))
    if (env.type === 'rate_limit.attachment_failure') {
      dispatch(chatActions.setComposerFiles([]))
    }
  }

  const connectStream = async () => {
    if (!authedRef.current) {
      requestAuthRequired()
      return
    }
    if (eventSourceRef.current && streamIdRef.current) {
      return
    }
    if (connectPromiseRef.current) {
      await connectPromiseRef.current
      return
    }
    connectPromiseRef.current = (async () => {
      dispatch(chatActions.setConnectionState('connecting'))
      const transport = await openChatStream({
        sessionId: sessionIdRef.current,
        onChatStart: (env) => dispatch(chatActions.chatStarted(env)),
        onChatStep: (env) => {
          dispatch(chatActions.chatStep(env))
          forwardCanvasPatchEvent(env)
        },
        onChatDelta: (env) => dispatch(chatActions.chatDelta(env)),
        onChatComplete: (env) => {
          dispatch(chatActions.chatCompleted(env))
          void refreshConversationList()
        },
        onChatError: (env) => dispatch(chatActions.chatErrored(env)),
        onConversationStatus: (env) => dispatch(chatActions.convStatusUpdated(env)),
        onChatService: handleServiceEvent,
        onDisconnect: handleStreamDisconnect,
      })

      eventSourceRef.current = transport.eventSource
      streamIdRef.current = transport.streamId
      sessionIdRef.current = transport.sessionId
      dispatch(chatActions.setConnectionState('connected'))
      dispatch(chatActions.setSessionId(transport.sessionId))
      window.setTimeout(() => {
        if (eventSourceRef.current === transport.eventSource) {
          reconnectAttemptRef.current = 0
        }
      }, STREAM_RECONNECT_STABLE_MS)
      if (stateRef.current.conversationId) {
        void requestConversationStatusForCurrentStream(stateRef.current.conversationId)
      }
    })()

    try {
      await connectPromiseRef.current
    } catch (error) {
      resetTransport()
      dispatch(chatActions.setConnectionState('disconnected'))
      throw error
    } finally {
      connectPromiseRef.current = null
    }
  }

  const sendMessage = async (textOverride?: string, requestedReactiveEventType?: string) => {
    if (!authedRef.current) {
      promptLogin()
      return
    }
    const previousTail = sendQueueRef.current
    let resolveOurs!: () => void
    const ours = new Promise<void>((res) => { resolveOurs = res })
    sendQueueRef.current = ours
    try {
      await previousTail
    } catch {
      /* prior send handled its own error; just advance serialization */
    }

    try {
      /* Read from the store directly (NOT stateRef) so we see the previous
       * queued send's submitAck dispatch — stateRef syncs via a post-commit
       * useEffect, which fires after a microtask boundary. */
      const snapshot = store.getState().chat
      const activeTurn = findActiveTurn(snapshot.turns)
      const reactiveEventType = requestedReactiveEventType ?? (activeTurn ? 'event.user.followup' : 'event.user.prompt')
      const isSteer = reactiveEventType === 'event.user.steer'
      const isContinuation = Boolean(
        activeTurn && (
          reactiveEventType === 'event.user.followup' ||
          reactiveEventType === 'event.user.steer'
        ),
      )
      const additionalEventType = reactiveEventType === 'event.user.steer' ? 'event.user.steer' : 'event.user.followup'
      const targetTurnId = isContinuation ? activeTurn?.id : undefined
      const draftText = (textOverride ?? snapshot.composerText).trim()
      const draftFiles = isSteer || textOverride !== undefined ? [] : snapshot.composerFiles
      const draftContexts = isSteer || textOverride !== undefined ? [] : snapshot.composerContexts
      const visibleDraftText = messageWithContextChips(draftText, draftContexts)
      const target = chatTarget(storyIdFromContexts(draftContexts))
      const externalEvents = buildExternalEventBatch(draftContexts, {
        agentId: 'main',
        eventId: (prefix) => createLocalId(prefix),
        text: draftText,
        files: draftFiles,
        reactiveEventType,
        target,
        defaults: CHAT_EVENT_DEFAULTS,
      })
      if (!draftText && draftFiles.length === 0 && draftContexts.length === 0 && !isSteer) return

      if (dryRunEnabled) {
        setDryRunLoading(true)
        setDryRunError(null)
        setDryRunPreview(null)
        try {
          const preview = await previewReactContext({
            bundleId,
            conversationId: snapshot.conversationId,
            turnId: targetTurnId,
            externalEvents,
            target,
          })
          setDryRunPreview(preview)
          if (!preview.ok) {
            setDryRunError(preview.error || 'Preview failed.')
          } else {
            dispatch(chatActions.pushBanner({
              tone: 'info',
              text: `Dry run rendered ${preview.event_count ?? externalEvents.length} events; ReAct was not invoked.`,
              placement: 'composer',
            }))
          }
        } catch (error) {
          const text = messageForError(error)
          setDryRunError(text)
          console.error('react context preview failed', error)
        } finally {
          setDryRunLoading(false)
        }
        return
      }

      const sentAt = Date.now()
      const existingConversationId = snapshot.conversationId
      dispatch(chatActions.clearComposer())

    try {
      await connectStream()
      const streamId = streamIdRef.current
      if (!streamId) {
        throw new Error('No SSE stream is available.')
      }
      const response = await submitChatMessage({
        streamId,
        bundleId,
        conversationId: existingConversationId,
        text: draftText,
        files: draftFiles,
        chatHistory: isContinuation ? [] : buildChatHistory(snapshot.turns),
        reactiveEventType,
        target,
        externalEvents,
        ...(isContinuation
          ? {
              activeTurnId: targetTurnId,
              targetTurnId,
            }
          : {}),
      })
      if (!response.turnId) {
        throw new Error('sse/chat response did not include a turn_id')
      }
      const turnId = response.turnId
      const draftAttachments = draftFiles.map((file, index) =>
        normalizeTurnAttachment(
          {
            filename: file.name,
            size: file.size,
            mime: file.type,
          },
          `live:${turnId}:${index}`,
          file,
        ),
      )
      dispatch(chatActions.submitAck({
        response: {
          conversationId: response.conversationId,
          turnId: response.turnId || turnId,
          status: typeof response.status === 'string' ? response.status : null,
          eventId: response.eventId ?? null,
          queuedTurnId: response.queuedTurnId ?? null,
          activeTurnId: response.activeTurnId ?? null,
          liveOwnerDetected: response.liveOwnerDetected,
          isContinuation: response.isContinuation,
        },
        existingConversationId,
        isContinuation,
        isSteer,
        targetTurnId: targetTurnId ?? null,
        draftText: visibleDraftText,
        draftAttachments,
        sentAt,
        additionalEventType,
      }))
      void refreshConversationList()
    } catch (error) {
      const text = messageForError(error)
      if (/\b(401|403|unauthorized|forbidden)\b/i.test(text)) {
        applyAuthed(false)
        resetTransport()
        dispatch(chatActions.setConnectionState('disconnected'))
        promptLogin()
        return
      }
      console.error('send failed', text)
      if (draftText) dispatch(chatActions.setComposerText(draftText))
      if (draftFiles.length > 0) dispatch(chatActions.setComposerFiles(draftFiles))
      const { text: noticeText, tone: noticeTone } = describeSendError(text)
      dispatch(chatActions.pushBanner({ tone: noticeTone, text: noticeText, placement: 'composer' }))
    }
    } finally {
      resolveOurs()
    }
  }

  const handleReconnect = async () => {
    resetTransport()
    reconnectAttemptRef.current = 0
    try {
      await connectStream()
      setBootError(null)
    } catch (error) {
      setBootError(messageForError(error))
    }
  }

  const pinConversationToCanvas = () => {
    const conversationId = stateRef.current.conversationId
    if (!conversationId) return
    if (!window.parent || window.parent === window) return
    window.parent.postMessage({
      type: 'kdcube-pin-conversation',
      source: 'versatile.chat',
      conversation_id: conversationId,
      title: stateRef.current.conversationTitle || 'Conversation',
      agent: 'main',
    }, '*')
  }

  const resolveAuthAndConnect = async () => {
    const profile = await fetchProfile()
    sessionIdRef.current = profile.sessionId
    dispatch(chatActions.setSessionId(profile.sessionId))
    const userType = (profile.userType || '').toLowerCase()
    const isAuthed = userType
      ? userType !== 'anonymous'
      : Boolean(settings.getAccessToken() || settings.getIdToken())
    applyAuthed(isAuthed)
    if (isAuthed) {
      const prompt = store.getState().chat.banners.find((b) => b.text === AUTH_PROMPT_TEXT)
      if (prompt) dispatch(chatActions.dismissBanner(prompt.id))
      if (!eventSourceRef.current) await connectStream()
      void refreshConversationList()
    } else {
      dispatch(chatActions.setConnectionState('disconnected'))
    }
  }

  useEffect(() => {
    let mounted = true
    const onAuthChanged = (event: MessageEvent) => {
      const data = event.data
      if (!data || typeof data !== 'object' || data.type !== 'kdcube-auth-changed') return
      if (!mounted) return
      void resolveAuthAndConnect().catch((error) => {
        console.warn('Re-auth after host auth change failed', error)
      })
    }
    window.addEventListener('message', onAuthChanged)
    ;(async () => {
      try {
        await settings.setupParentListener()
        if (!mounted) return
        setReady(true)
        await resolveAuthAndConnect()
        settings.onConfigReceived(() => {
          if (!mounted) return
          void resolveAuthAndConnect().catch((error) => {
            console.warn('Re-auth after config update failed', error)
          })
        })
      } catch (error) {
        if (!mounted) return
        setBootError(messageForError(error))
      }
    })()

    return () => {
      mounted = false
      window.removeEventListener('message', onAuthChanged)
      resetTransport()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!ready) return
    void refreshConversationList()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, bundleId])

  /* Public, stable action wrappers. */
  const send = useCallback((textOverride?: string, requestedReactiveEventType?: string) => {
    void sendMessage(textOverride, requestedReactiveEventType)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const steer = useCallback(() => {
    void sendMessage('', 'event.user.steer')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const loadConversationStable = useCallback((conversationId: string) => {
    void loadConversation(conversationId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const newChat = useCallback(() => { startNewChat() // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const deleteConversationStable = useCallback((conversation: ConversationSummary) => {
    void deleteConversation(conversation)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const refreshConversationListStable = useCallback(() => {
    void refreshConversationList()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const handleReconnectStable = useCallback(() => {
    void handleReconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const attachContext = useCallback((contexts: AttachedContext | AttachedContext[]) => {
    const list = Array.isArray(contexts) ? contexts : [contexts]
    list.forEach((ctx) => dispatch(chatActions.addComposerContext(ctx)))
  }, [dispatch])
  const removeContext = useCallback((ids: string | string[]) => {
    const list = Array.isArray(ids) ? ids : [ids]
    const uniqueIds = Array.from(new Set(list.map((id) => String(id || '').trim()).filter(Boolean)))
    if (!uniqueIds.length) return
    uniqueIds.forEach((id) => dispatch(chatActions.removeComposerContext(id)))
    try {
      if (window.parent !== window) {
        window.parent.postMessage({ type: CHAT_CONTEXT_REMOVE_MESSAGE, ids: uniqueIds }, '*')
      }
    } catch {
      /* parent sync is best-effort */
    }
  }, [dispatch])
  const openContextChip = useCallback((context: ActionableContext) => {
    activateContextPin(context).catch((error) => {
      const { text, tone } = contextPinActionNotice(error)
      dispatch(chatActions.pushBanner({ tone, text: tone === 'error' ? `Context action failed: ${text}` : text }))
    })
  }, [dispatch])
  const downloadFile = useCallback((ref: string, filename?: string, mime?: string) => {
    void downloadObjectRef(ref, filename, mime).catch((error: unknown) => {
      dispatch(chatActions.pushBanner({ tone: 'error', text: `Download failed: ${messageForError(error)}` }))
    })
  }, [dispatch])
  const submitFeedback = useCallback((turnId: string, reaction: TurnReaction | null, text?: string) => {
    const snapshot = store.getState().chat
    const conversationId = snapshot.conversationId
    if (!conversationId) return
    const previous = snapshot.feedback[turnId] ?? null
    dispatch(chatActions.setTurnFeedback({ turnId, reaction }))
    void submitTurnFeedback(conversationId, turnId, reaction, text).catch((error) => {
      dispatch(chatActions.setTurnFeedback({ turnId, reaction: previous }))
      dispatch(chatActions.pushBanner({
        tone: 'error',
        text: `Could not save feedback: ${messageForError(error)}`,
      }))
    })
  }, [dispatch])
  const clearDryRunPreview = useCallback(() => {
    setDryRunPreview(null)
    setDryRunError(null)
  }, [])

  return {
    state,
    ready,
    bootError,
    setBootError,
    authed,
    hostView,
    setHostView,
    setHostViewLocal,
    kdcubePreview,
    bundleId,
    send,
    steer,
    loadConversation: loadConversationStable,
    newChat,
    deleteConversation: deleteConversationStable,
    refreshConversationList: refreshConversationListStable,
    attachContext,
    removeContext,
    openContextChip,
    downloadFile,
    submitFeedback,
    handleReconnect: handleReconnectStable,
    pinConversationToCanvas,
    promptLogin,
    dryRun: {
      enabled: dryRunEnabled,
      loading: dryRunLoading,
      preview: dryRunPreview,
      error: dryRunError,
      setEnabled: setDryRunEnabled,
      clearPreview: clearDryRunPreview,
    },
  }
}

/** Mount-once host: builds the engine and provides it to descendants. Rendered
 *  by ChatStoreProvider INSIDE <Provider> so the engine's redux hooks work. */
export function ChatEngineHost({ children }: { children: ReactNode }) {
  const engine = useChatEngineImpl()
  return <ChatEngineContext.Provider value={engine}>{children}</ChatEngineContext.Provider>
}

/** Consume the chat engine. Must be rendered under <ChatStoreProvider>. */
export function useChatEngine(): ChatEngine {
  const engine = useContext(ChatEngineContext)
  if (!engine) {
    throw new Error('useChatEngine() must be used within <ChatStoreProvider>.')
  }
  return engine
}
