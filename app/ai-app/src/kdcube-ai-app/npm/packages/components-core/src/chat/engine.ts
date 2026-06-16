/**
 * createChatEngine — the framework-agnostic chat controller.
 *
 * This is the widget's `useChatEngine()` orchestration, ported to a vanilla
 * controller. The transport/send-queue/reconnect/service-event logic is verbatim
 * (including the deliberate `store.getState()` reads inside the queued send to
 * dodge post-commit lag). Two things change by design:
 *   - the `settings` singleton becomes the injected `EngineRuntime`; and
 *   - the `host.ts` postMessage calls + the `window` message/drag listeners are
 *     gone — the engine emits host events (`unauthorized`, `view-change`,
 *     `pin-conversation`, `object-open`, `canvas-patch`, `context-removed`) and
 *     exposes methods the host adapter calls (loadConversation/attachContext/…).
 */
import type { EngineConfig } from '../shared/index.ts'
import { createHostEventEmitter } from '../shared/index.ts'
import { buildRuntime } from './runtime.ts'
import { createChatStore } from './store.ts'
import { chatActions } from './slice.ts'
import {
  buildChatHistory,
  fallbackRateLimitMessage,
  findActiveTurn,
  normalizeTurnAttachment,
} from './reducers.ts'
import { messageWithContextChips } from './contextChips.ts'
import { activateContextPin, contextPinActionNotice } from './contextPinActions.ts'
import { buildExternalEventBatch } from './eventBatch.ts'
import { messageForError } from './util.ts'
import {
  deleteConversationById,
  downloadObjectRef,
  fetchConversationById,
  fetchProfile,
  fetchTurnFeedbacks,
  listBundleConversations,
  openChatStream,
  previewReactContext,
  requestConversationStatus,
  submitChatMessage,
  submitTurnFeedback,
} from './transport/index.ts'
import type { AttachedContext } from './state.ts'
import type {
  ConversationSummary,
  ChatServiceEnvelope,
  ChatStepEnvelope,
  RateLimitPayload,
  BannerTone,
} from './protocol.ts'
import type {
  ChatEngine,
  ChatEngineStatus,
  HostView,
  AttachContextInput,
  OpenContextInput,
  FeedbackReaction,
} from './types.ts'

const AUTH_PROMPT_TEXT = 'Sign in to start chatting.'
const STREAM_RECONNECT_DELAYS_MS = [1000, 2500, 5000]
const STREAM_RECONNECT_STABLE_MS = 30000
const STREAM_RECONNECT_EXHAUSTED_TEXT = 'Connection lost. Send again or use Reconnect to open a fresh stream.'

/** Default event-source / surface names (the widget read these from `settings`;
 *  here they are the package defaults, matching the widget's `chat` prefix). */
const CHAT_SURFACE = 'chat_chat'
const CHAT_USER_EVENT_SOURCE_ID = 'chat.main.chat.user'
const CHAT_CANVAS_PATCH_STEP = 'chat.canvas.patch'
const CHAT_EVENT_DEFAULTS = {
  userEventSourceId: CHAT_USER_EVENT_SOURCE_ID,
  attachmentEventSourceId: 'chat.main.chat.attachment',
  contextEventSourceId: 'chat.context.focus',
  chatSurface: CHAT_SURFACE,
  canvasStateEventSourceId: 'chat.canvas.state',
  canvasFocusEventSourceId: 'chat.canvas.focus',
  canvasSurface: 'chat_canvas',
  snapshotEventSourceId: 'chat.snapshot',
  snapshotSurface: 'chat_wizard',
}

/** Map a raw send failure to a concise, friendly one-liner + tone. */
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

export function createChatEngine(config: EngineConfig): ChatEngine {
  const runtime = buildRuntime(config)
  const emitter = createHostEventEmitter()
  const store = createChatStore()
  const dispatch = store.dispatch
  const bundleId = runtime.bundleId
  const getChat = () => store.getState().chat

  // --- Engine-level (non-Redux) status ---
  let status: ChatEngineStatus = {
    ready: false,
    authed: false,
    bootError: null,
    hostView: 'expanded',
    dryRun: { enabled: false, loading: false, preview: null, error: null },
  }
  const statusListeners = new Set<() => void>()
  function emitStatus() {
    for (const listener of [...statusListeners]) listener()
  }
  function setStatus(partial: Partial<ChatEngineStatus>) {
    status = { ...status, ...partial }
    emitStatus()
  }
  function setDryRun(partial: Partial<ChatEngineStatus['dryRun']>) {
    status = { ...status, dryRun: { ...status.dryRun, ...partial } }
    emitStatus()
  }
  function setBootError(value: string | null) {
    setStatus({ bootError: value })
  }

  // --- Transport / send refs ---
  let authedRef = false
  let eventSource: EventSource | null = null
  let connectPromise: Promise<void> | null = null
  let reconnectTimer: number | null = null
  let reconnectAttempt = 0
  let sessionId: string | null = null
  let streamId: string | null = null
  let sendQueue: Promise<void> = Promise.resolve()
  let disposed = false

  function applyAuthed(next: boolean) {
    authedRef = next
    setStatus({ authed: next })
  }

  function promptLogin() {
    emitter.emit('unauthorized', {})
    const exists = getChat().banners.some((b) => b.text === AUTH_PROMPT_TEXT)
    if (!exists) dispatch(chatActions.pushBanner({ tone: 'info', text: AUTH_PROMPT_TEXT, placement: 'composer' }))
  }

  function forwardCanvasPatchEvent(env: ChatStepEnvelope) {
    if (env.event?.step !== CHAT_CANVAS_PATCH_STEP) return
    if (!env.data || typeof env.data !== 'object') return
    emitter.emit('canvas-patch', { event: env.data })
  }

  const refreshConversationList = async () => {
    if (!bundleId) return
    if (!authedRef) {
      dispatch(chatActions.setConversations([]))
      return
    }
    dispatch(chatActions.setConversationsLoading(true))
    dispatch(chatActions.setConversationsError(null))
    try {
      const conversations = await listBundleConversations(runtime, bundleId)
      dispatch(chatActions.setConversations(conversations))
      dispatch(chatActions.setConversationsLoading(false))
    } catch (error) {
      const message = messageForError(error)
      dispatch(chatActions.setConversationsLoading(false))
      dispatch(chatActions.setConversationsError(message))
    }
  }

  const requestConversationStatusForCurrentStream = async (conversationId: string) => {
    if (!streamId) return
    try {
      await requestConversationStatus(runtime, conversationId, streamId)
    } catch (error) {
      console.warn('Unable to request conversation status', error)
    }
  }

  const loadConversation = async (conversationId: string) => {
    dispatch(chatActions.setConversationLoadingId(conversationId))
    dispatch(chatActions.unlockInput())
    try {
      const conversation = await fetchConversationById(runtime, conversationId)
      dispatch(chatActions.hydrateConversation({ conversation }))
      dispatch(chatActions.clearComposer())
      dispatch(chatActions.setConversationLoadingId(null))
      void fetchTurnFeedbacks(runtime, conversation.conversation_id)
        .then((map) => dispatch(chatActions.setFeedbackMap(map)))
        .catch(() => {})
      if (getChat().connection === 'connected') {
        void requestConversationStatusForCurrentStream(conversation.conversation_id)
      }
    } catch (error) {
      const message = messageForError(error)
      dispatch(chatActions.setConversationLoadingId(null))
      setBootError(message)
    }
  }

  const startNewChat = () => {
    dispatch(chatActions.startNewConversation())
    dispatch(chatActions.clearComposer())
    dispatch(chatActions.unlockInput())
    dispatch(chatActions.setConversationLoadingId(null))
  }

  const deleteConversation = async (conversation: ConversationSummary) => {
    // The host confirms before calling (irreversible). Core does not pop a dialog.
    dispatch(chatActions.setConversationDeletingId(conversation.id))
    try {
      await deleteConversationById(runtime, conversation.id)
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
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  const closeTransport = () => {
    eventSource?.close()
    eventSource = null
    streamId = null
    connectPromise = null
  }

  const resetTransport = () => {
    clearReconnectTimer()
    closeTransport()
  }

  const pushReconnectExhaustedBanner = () => {
    const exists = getChat().banners.some((b) => b.text === STREAM_RECONNECT_EXHAUSTED_TEXT)
    if (!exists) {
      dispatch(chatActions.pushBanner({
        tone: 'warning',
        text: STREAM_RECONNECT_EXHAUSTED_TEXT,
        placement: 'composer',
      }))
    }
  }

  const scheduleStreamReconnect = (reason?: string) => {
    if (!authedRef || reconnectTimer !== null || connectPromise) return
    const attempt = reconnectAttempt
    if (attempt >= STREAM_RECONNECT_DELAYS_MS.length) {
      console.warn('SSE stream reconnect attempts exhausted', { reason })
      pushReconnectExhaustedBanner()
      return
    }
    const delay = STREAM_RECONNECT_DELAYS_MS[attempt]
    reconnectAttempt = attempt + 1
    console.info('Scheduling SSE stream reconnect', { attempt: attempt + 1, delay, reason })
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      void connectStream().catch((error) => {
        console.warn('SSE stream reconnect failed', error)
        scheduleStreamReconnect('reconnect_failed')
      })
    }, delay) as unknown as number
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
    if (!authedRef) {
      emitter.emit('unauthorized', {})
      return
    }
    if (eventSource && streamId) {
      return
    }
    if (connectPromise) {
      await connectPromise
      return
    }
    connectPromise = (async () => {
      dispatch(chatActions.setConnectionState('connecting'))
      const transport = await openChatStream(runtime, {
        sessionId,
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

      eventSource = transport.eventSource
      streamId = transport.streamId
      sessionId = transport.sessionId
      dispatch(chatActions.setConnectionState('connected'))
      dispatch(chatActions.setSessionId(transport.sessionId))
      setTimeout(() => {
        if (eventSource === transport.eventSource) {
          reconnectAttempt = 0
        }
      }, STREAM_RECONNECT_STABLE_MS)
      if (getChat().conversationId) {
        void requestConversationStatusForCurrentStream(getChat().conversationId!)
      }
    })()

    try {
      await connectPromise
    } catch (error) {
      resetTransport()
      dispatch(chatActions.setConnectionState('disconnected'))
      throw error
    } finally {
      connectPromise = null
    }
  }

  const sendMessage = async (textOverride?: string, requestedReactiveEventType?: string) => {
    if (!authedRef) {
      promptLogin()
      return
    }
    const previousTail = sendQueue
    let resolveOurs!: () => void
    const ours = new Promise<void>((res) => { resolveOurs = res })
    sendQueue = ours
    try {
      await previousTail
    } catch {
      /* prior send handled its own error; just advance serialization */
    }

    try {
      /* Read from the store directly so we see the previous queued send's
       * submitAck dispatch (no React post-commit lag here, but keep the
       * snapshot-at-send semantics identical to the widget). */
      const snapshot = getChat()
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
        eventId: (prefix) => runtime.createLocalId(prefix),
        text: draftText,
        files: draftFiles,
        reactiveEventType,
        target,
        defaults: CHAT_EVENT_DEFAULTS,
      })
      if (!draftText && draftFiles.length === 0 && draftContexts.length === 0 && !isSteer) return

      if (status.dryRun.enabled) {
        setDryRun({ loading: true, error: null, preview: null })
        try {
          const preview = await previewReactContext(runtime, {
            bundleId,
            conversationId: snapshot.conversationId,
            turnId: targetTurnId,
            externalEvents,
            target,
          })
          setDryRun({ preview })
          if (!preview.ok) {
            setDryRun({ error: preview.error || 'Preview failed.' })
          } else {
            dispatch(chatActions.pushBanner({
              tone: 'info',
              text: `Dry run rendered ${preview.event_count ?? externalEvents.length} events; ReAct was not invoked.`,
              placement: 'composer',
            }))
          }
        } catch (error) {
          const text = messageForError(error)
          setDryRun({ error: text })
          console.error('react context preview failed', error)
        } finally {
          setDryRun({ loading: false })
        }
        return
      }

      const sentAt = Date.now()
      const existingConversationId = snapshot.conversationId
      dispatch(chatActions.clearComposer())

      try {
        await connectStream()
        if (!streamId) {
          throw new Error('No SSE stream is available.')
        }
        const response = await submitChatMessage(runtime, {
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
    reconnectAttempt = 0
    try {
      await connectStream()
      setBootError(null)
    } catch (error) {
      setBootError(messageForError(error))
    }
  }

  const pinConversationToCanvas = () => {
    const conversationId = getChat().conversationId
    if (!conversationId) return
    emitter.emit('pin-conversation', {
      conversationId,
      title: getChat().conversationTitle || 'Conversation',
    })
  }

  const resolveAuthAndConnect = async () => {
    const profile = await fetchProfile(runtime)
    sessionId = profile.sessionId
    dispatch(chatActions.setSessionId(profile.sessionId))
    const userType = (profile.userType || '').toLowerCase()
    let isAuthed: boolean
    if (userType) {
      isAuthed = userType !== 'anonymous'
    } else {
      const tokens = await runtime.getTokens()
      isAuthed = Boolean(tokens.accessToken || tokens.idToken)
    }
    applyAuthed(isAuthed)
    if (isAuthed) {
      const prompt = getChat().banners.find((b) => b.text === AUTH_PROMPT_TEXT)
      if (prompt) dispatch(chatActions.dismissBanner(prompt.id))
      if (!eventSource) await connectStream()
      void refreshConversationList()
    } else {
      dispatch(chatActions.setConnectionState('disconnected'))
    }
  }

  const refreshAuth = () => {
    void resolveAuthAndConnect().catch((error) => {
      console.warn('Re-auth failed', error)
    })
  }

  const start = async () => {
    try {
      if (disposed) return
      setStatus({ ready: true })
      emitter.emit('ready', {})
      await resolveAuthAndConnect()
    } catch (error) {
      if (disposed) return
      setBootError(messageForError(error))
    }
  }

  // --- Public action surface ---
  const engine: ChatEngine = {
    store,
    bundleId,
    getState: getChat,
    subscribe(listener) {
      return store.subscribe(listener)
    },
    getStatus() {
      return status
    },
    subscribeStatus(listener) {
      statusListeners.add(listener)
      return () => statusListeners.delete(listener)
    },
    on: emitter.on,
    refreshAuth,
    send(textOverride, requestedReactiveEventType) {
      void sendMessage(textOverride, requestedReactiveEventType)
    },
    steer() {
      void sendMessage('', 'event.user.steer')
    },
    loadConversation(conversationId) {
      void loadConversation(conversationId)
    },
    newChat() {
      startNewChat()
    },
    deleteConversation(conversation) {
      void deleteConversation(conversation)
    },
    refreshConversations() {
      void refreshConversationList()
    },
    attachContext(contexts: AttachContextInput | AttachContextInput[]) {
      const list = Array.isArray(contexts) ? contexts : [contexts]
      list.forEach((ctx) => dispatch(chatActions.addComposerContext(ctx)))
    },
    removeContext(ids: string | string[], opts?: { silent?: boolean }) {
      const list = Array.isArray(ids) ? ids : [ids]
      const uniqueIds = Array.from(new Set(list.map((id) => String(id || '').trim()).filter(Boolean)))
      if (!uniqueIds.length) return
      uniqueIds.forEach((id) => dispatch(chatActions.removeComposerContext(id)))
      if (!opts?.silent) emitter.emit('context-removed', { ids: uniqueIds })
    },
    openContextChip(context: OpenContextInput) {
      activateContextPin(runtime, emitter, context).catch((error) => {
        const { text, tone } = contextPinActionNotice(error)
        dispatch(chatActions.pushBanner({ tone, text: tone === 'error' ? `Context action failed: ${text}` : text }))
      })
    },
    downloadFile(ref, filename, mime) {
      void downloadObjectRef(runtime, ref, filename ?? ref, mime).catch((error: unknown) => {
        dispatch(chatActions.pushBanner({ tone: 'error', text: `Download failed: ${messageForError(error)}` }))
      })
    },
    submitFeedback(turnId, reaction: FeedbackReaction, text) {
      const snapshot = getChat()
      const conversationId = snapshot.conversationId
      if (!conversationId) return
      const previous = snapshot.feedback[turnId] ?? null
      dispatch(chatActions.setTurnFeedback({ turnId, reaction }))
      void submitTurnFeedback(runtime, conversationId, turnId, reaction, text).catch((error) => {
        dispatch(chatActions.setTurnFeedback({ turnId, reaction: previous }))
        dispatch(chatActions.pushBanner({
          tone: 'error',
          text: `Could not save feedback: ${messageForError(error)}`,
        }))
      })
    },
    handleReconnect() {
      void handleReconnect()
    },
    pinConversationToCanvas,
    promptLogin,
    setHostView(next: HostView, opts?: { silent?: boolean }) {
      setStatus({ hostView: next })
      if (!opts?.silent) emitter.emit('view-change', { view: next })
    },
    setBootError,
    setDryRunEnabled(value) {
      setDryRun({ enabled: value })
    },
    clearDryRunPreview() {
      setDryRun({ preview: null, error: null })
    },
    dispose() {
      disposed = true
      resetTransport()
      statusListeners.clear()
    },
  }

  void start()
  return engine
}
