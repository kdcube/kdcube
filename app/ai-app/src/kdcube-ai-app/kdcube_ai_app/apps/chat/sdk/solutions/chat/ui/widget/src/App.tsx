import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  deleteConversationById,
  fetchConversationById,
  fetchTurnFeedbacks,
  listBundleConversations,
  openChatStream,
  previewReactContext,
  requestConversationStatus,
  submitChatMessage,
  submitTurnFeedback,
} from './service.ts'
import type {
  BannerTone,
  ChatStepEnvelope,
  ChatServiceEnvelope,
  ConversationSummary,
  RateLimitPayload,
  ReactContextPreviewResponse,
  TurnReaction,
} from './service.ts'
import {
  BUILT_BUNDLE_ID,
  CHAT_ATTACHMENT_EVENT_SOURCE_ID,
  CHAT_BRAND_LABEL,
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
} from './settings.ts'

import type {
  AdditionalUserMessage,
  AttachedContext,
  ChatState,
  ChatTurn,
} from './features/chat/chatTypes.ts'
import {
  buildChatHistory,
  fallbackRateLimitMessage,
  findActiveTurn,
  normalizeTurnAttachment,
} from './features/chat/chatReducers.ts'
import { messageForError } from './components/utils.ts'

import { useAppDispatch, useAppSelector, useStableCallback } from './app/hooks.ts'
import { store } from './app/store.ts'
import { chatActions } from './features/chat/chatSlice.ts'

import { BannerStrip } from './features/banners/BannerStrip.tsx'
import { ConversationsSidebar } from './features/conversations/ConversationsSidebar.tsx'
import { Composer } from './features/composer/Composer.tsx'
import { buildExternalEventBatch } from './features/context/eventBatch.ts'
import { TurnView } from './features/chat/TurnView.tsx'
import { FileDropZone } from './components/FileDropZone.tsx'
import { CopyButton } from './components/CopyButton.tsx'
import { WebappPane, WebappModal } from './components/WebappPane.tsx'
import { fetchProfile } from './api/transport.ts'
import {
  isKdcubePreviewContext,
  isHostEmbedMode,
  notifyHostWidgetFocus,
  recognizeContextMessage,
  recognizeContextRemoval,
  requestAuthRequired,
  requestHostView,
} from './host.ts'

/* Gentle inline hint shown when an anonymous visitor tries to send. The
 * host also raises its own login surface; this banner explains why the
 * message did not go through if the visitor dismisses that surface. */
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
 *  one-liner + tone. The server crafts concise messages for live-turn
 *  `chat.error` events, but a rejected POST surfaces the raw HTTP body (e.g.
 *  a 413 HTML page), which must never reach the UI verbatim. User-fixable
 *  problems (too large, missing message, transient) are warnings (yellow);
 *  hard failures are errors (red). */
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

/** Short conversation timestamp for the compact picker: time if today,
 *  otherwise a short month/day. */
function formatConversationDate(ts?: number | null): string {
  if (!ts) return ''
  const date = new Date(ts)
  const now = new Date()
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
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

export default function App() {
  const state = useAppSelector((s) => s.chat)
  const dispatch = useAppDispatch()
  const [ready, setReady] = useState(false)
  const [bootError, setBootError] = useState<string | null>(null)
  const [conversationQuery, setConversationQuery] = useState('')
  /* Landing-page embed: 'expanded' asks the host to promote this chat
   * iframe to a fullscreen overlay. The host drives the overlay; the
   * widget only signals intent and stays in sync via `kdcube-set-view`.
   *
   * `hostView` also drives the internal layout: 'compact' is the single-
   * column tile view (no conversations sidebar, trimmed appbar) for the
   * landing embed; 'expanded' is the usual full view with the sidebar.
   * Switching between them is a pure view flip — all data lives in Redux,
   * so expanding never refetches. Default to compact only when embedded;
   * a standalone full-page open starts expanded so it keeps its sidebar. */
  const [hostView, setHostView] = useState<'compact' | 'expanded'>(() =>
    typeof window !== 'undefined' && window.parent !== window ? 'compact' : 'expanded',
  )
  /* Left-column mode. `chats` shows ConversationsSidebar (default).
   * `webapp` is reserved for a future bundle side panel. `collapsed`
   * hides the column entirely so the chat takes full width. */
  const [leftPaneMode, setLeftPaneMode] = useState<'chats' | 'webapp' | 'collapsed'>('chats')
  const [webappModalOpen, setWebappModalOpen] = useState(false)
  /* Compact view conversation picker (no sidebar there). A small dropdown in
   * the appbar lets the user switch chats / start new without leaving the
   * tile. Selecting just calls loadConversation — no stream refetch. */
  const [convMenuOpen, setConvMenuOpen] = useState(false)
  /* Public embedding: the chat renders for anonymous visitors, but the
   * server is the authority on who may open the stream and send. `authed`
   * starts optimistic from token presence (direct/non-embedded use) and is
   * confirmed/corrected by the `/profile` user_type. While anonymous the
   * stream stays closed, user-bound surfaces (settings/memories) are
   * hidden, and a send attempt asks the host to show its login surface. */
  const [authed, setAuthed] = useState<boolean>(() =>
    Boolean(settings.getAccessToken() || settings.getIdToken()),
  )
  const authedRef = useRef<boolean>(authed)
  const applyAuthed = useCallback((next: boolean) => {
    authedRef.current = next
    setAuthed(next)
  }, [])
  /* Ask the host to show its login surface and leave a dismissible hint.
   * Deduped by text so repeated send clicks don't stack banners. */
  const promptLogin = useCallback(() => {
    requestAuthRequired()
    const exists = store.getState().chat.banners.some((b) => b.text === AUTH_PROMPT_TEXT)
    if (!exists) dispatch(chatActions.pushBanner({ tone: 'info', text: AUTH_PROMPT_TEXT, placement: 'composer' }))
  }, [dispatch])

  const stateRef = useRef<ChatState>(state)
  const eventSourceRef = useRef<EventSource | null>(null)
  const connectPromiseRef = useRef<Promise<void> | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)
  const reconnectAttemptRef = useRef(0)
  const sessionIdRef = useRef<string | null>(null)
  const streamIdRef = useRef<string | null>(null)
  /* Tail of the in-flight sendMessage chain. Each new send awaits this
   * tail before it runs, then becomes the new tail. Serializes the
   * `submitChatMessage` POSTs so rapid clicks don't race conversation
   * creation (without serialization, two concurrent sends both read
   * `conversationId === null` and the server either creates two
   * conversations or rejects the second with 404 "conversation not
   * found" when the second arrives before the first's index row is
   * persisted — the failure mode the user reported with attachments). */
  const sendQueueRef = useRef<Promise<void>>(Promise.resolve())
  const bottomRef = useRef<HTMLDivElement | null>(null)
  /* The scrollable messages region. Both compact and expanded views are
   * viewport-height (the root is h-screen + overflow-hidden), so this region
   * is always the internal scroller and the window itself never scrolls. */
  const scrollContainerRef = useRef<HTMLDivElement | null>(null)
  const autoScrollRef = useRef(true)
  const [showScrollDown, setShowScrollDown] = useState(false)
  const [dryRunEnabled, setDryRunEnabled] = useState(false)
  const [dryRunLoading, setDryRunLoading] = useState(false)
  const [dryRunPreview, setDryRunPreview] = useState<ReactContextPreviewResponse | null>(null)
  const [dryRunError, setDryRunError] = useState<string | null>(null)

  /* The messages region is the scroller only where its overflow is active
   * (compact always; expanded at lg+). Below lg the expanded layout scrolls
   * the window instead, so resolve the real scroller from computed overflow
   * rather than assuming. */
  const activeScroller = (): HTMLElement | null => {
    const el = scrollContainerRef.current
    if (!el) return null
    const oy = window.getComputedStyle(el).overflowY
    return oy === 'auto' || oy === 'scroll' ? el : null
  }

  /* Host -> widget view sync. When the host closes its fullscreen overlay
   * (backdrop / Esc) it posts `kdcube-set-view`, keeping the expand
   * control in sync with the host. */
  useEffect(() => {
    function onHostMessage(event: MessageEvent) {
      const data = event.data
      if (!data || typeof data !== 'object') return
      if (data.type === 'kdcube-set-view') {
        if (data.view === 'compact' || data.view === 'expanded') setHostView(data.view)
        return
      }
      const removedContextIds = recognizeContextRemoval(data)
      if (removedContextIds.length > 0) {
        removedContextIds.forEach((id) => dispatch(chatActions.removeComposerContext(id)))
        return
      }
      /* Host dropped a structured context card onto the chat. Attach it only
       * if we recognize it (known kind/id) — the demo of the assistant
       * "naming" familiar surrounding objects as context chips. */
      const recognized = recognizeContextMessage(data)
      if (recognized.length > 0) {
        recognized.forEach((ctx) => dispatch(chatActions.addComposerContext(ctx)))
        const source = typeof data.source === 'string' ? data.source : ''
        const silent = data.silent === true || source === CHAT_CONTEXT_REFRESH_SOURCE
        if (!silent) {
          /* Best-effort focus so the visitor sees an explicitly attached chip
           * land next to the input. Silent refreshes keep focus where the user
           * is typing, for example in the task/story editor. */
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

  useEffect(() => {
    if (!convMenuOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setConvMenuOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [convMenuOpen])

  const toggleHostView = useCallback(() => {
    setHostView((prev) => {
      const next = prev === 'expanded' ? 'compact' : 'expanded'
      requestHostView(next)
      return next
    })
  }, [])

  /* Dev affordance: when the bundle main view is iframed inside a same-origin
   * KDCube frame (e.g. the control plane), expose a local compact/full preview
   * toggle so both layouts can be exercised before the public landing host —
   * which is cross-origin and drives the overlay itself — is wired up. Flips
   * the view locally only; it does not message any host. */
  const kdcubePreview = useMemo(() => isKdcubePreviewContext(), [])
  const toggleViewLocal = useCallback(() => {
    setHostView((prev) => (prev === 'compact' ? 'expanded' : 'compact'))
  }, [])

  useEffect(() => {
    stateRef.current = state
  }, [state])

  useEffect(() => {
    const measure = () => {
      const scroller = activeScroller()
      if (scroller) {
        const remaining = scroller.scrollHeight - (scroller.scrollTop + scroller.clientHeight)
        const near = remaining < 140
        autoScrollRef.current = near
        setShowScrollDown(!near && scroller.scrollHeight > scroller.clientHeight + 80)
      } else {
        const doc = document.documentElement
        const scrollTop = window.scrollY || doc.scrollTop || 0
        const remaining = doc.scrollHeight - (scrollTop + window.innerHeight)
        const near = remaining < 140
        autoScrollRef.current = near
        setShowScrollDown(!near && doc.scrollHeight > window.innerHeight + 80)
      }
    }

    measure()
    /* Bind to BOTH the container and the window: the active scroller can flip
     * after this effect runs (e.g. the iframe is promoted to fullscreen width
     * only after hostView changes, switching the scroller from window to the
     * container). measure() reads whichever is active, so showScrollDown stays
     * correct regardless of which one the user scrolls. */
    const el = scrollContainerRef.current
    if (el) el.addEventListener('scroll', measure, { passive: true })
    window.addEventListener('scroll', measure, { passive: true })
    window.addEventListener('resize', measure)
    return () => {
      if (el) el.removeEventListener('scroll', measure)
      window.removeEventListener('scroll', measure)
      window.removeEventListener('resize', measure)
    }
    /* `ready` re-runs this once the messages container mounts so the scroll
     * listener binds to the container (the only scroller) rather than the
     * window. The user scrolling up sets autoScrollRef=false, which halts the
     * stream auto-follow until they return near the bottom. */
  }, [hostView, ready])

  const scrollToBottom = () => {
    /* "Latest" re-pins to the bottom so streaming auto-follows again, until the
     * user scrolls up. Set the intent immediately rather than waiting for the
     * smooth scroll to settle and re-trigger measure(). */
    autoScrollRef.current = true
    setShowScrollDown(false)
    const scroller = activeScroller()
    if (scroller) scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' })
    else bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }

  /* Step between user messages (turn anchors). "first" jumps to the top;
   * "prev"/"next" go to the user message just above / below the current scroll
   * position. Jumping is a "read here" intent, so it unpins the streaming
   * auto-follow (only Latest re-pins). */
  const scrollToTurn = (direction: 'first' | 'prev' | 'next') => {
    const container = scrollContainerRef.current
    if (!container) return
    const anchors = Array.from(container.querySelectorAll<HTMLElement>('[data-turn-anchor]'))
    if (!anchors.length) return
    autoScrollRef.current = false
    setShowScrollDown(true)
    let target: HTMLElement | null = null
    if (direction === 'first') {
      target = anchors[0]
    } else {
      const scroller = activeScroller()
      const refTop = scroller ? scroller.getBoundingClientRect().top : 0
      const tol = 8
      if (direction === 'next') {
        target = anchors.find((a) => a.getBoundingClientRect().top > refTop + tol) || anchors[anchors.length - 1]
      } else {
        const above = anchors.filter((a) => a.getBoundingClientRect().top < refTop - tol)
        target = above.length ? above[above.length - 1] : anchors[0]
      }
    }
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  /* Auto-scroll dep tracks a compact signature of "what has visually
   * grown" — turn count + the active turn's answer length + banner
   * count + ready. This fires on streaming deltas (so the page keeps
   * up with the answer) but skips no-op renders that didn't add height. */
  const lastTurn = state.turns[state.turns.length - 1]
  const scrollSignature = `${state.turns.length}:${lastTurn?.id ?? ''}:${lastTurn?.answer.length ?? 0}:${lastTurn?.timeline.length ?? 0}:${lastTurn?.artifacts.length ?? 0}:${state.banners.length}:${ready ? 1 : 0}`
  useEffect(() => {
    if (!autoScrollRef.current) return
    const scroller = activeScroller()
    if (scroller) scroller.scrollTop = scroller.scrollHeight
    else bottomRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' })
  }, [scrollSignature, hostView])

  const hasPendingTurn = state.turns.some((turn) => turn.state === 'pending' || turn.state === 'running')
  const bundleId = settings.getBundleId() || BUILT_BUNDLE_ID
  const filteredConversations = useMemo(() => {
    const query = conversationQuery.trim().toLowerCase()
    const items = state.conversations.slice().sort((left, right) => (right.lastActivityAt || 0) - (left.lastActivityAt || 0))
    if (!query) return items
    return items.filter((item) => {
      const haystack = `${item.title || ''} ${item.id}`.toLowerCase()
      return haystack.includes(query)
    })
  }, [conversationQuery, state.conversations])

  const refreshConversationList = async () => {
    if (!bundleId) return
    /* Conversations are user-bound; an anonymous visitor has none and the
     * list endpoint would 401. Keep the sidebar empty instead of noisy. */
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

      /* Best-effort: restore the user's saved thumbs for this conversation
       * (hydrateConversation cleared the map). Never blocks the load. */
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

  const startNewChat = () => {
    dispatch(chatActions.startNewConversation())
    dispatch(chatActions.clearComposer())
    dispatch(chatActions.unlockInput())
    dispatch(chatActions.setConversationLoadingId(null))
  }

  const deleteConversation = async (conversation: ConversationSummary) => {
    /* Irreversible — confirm with the user first. The backend handler in
     * `conversations.py:delete_conversation` removes index rows for
     * {user_id, conversation_id} and best-effort deletes message JSONs,
     * attachments, and execution artifacts. */
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
        /* Economic / budget problems are chat-blocking but user-actionable —
         * yellow (warning), not red, and shown above the composer. */
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
        /* Unknown / internal service events (telemetry such as
         * react.tool.call, accounting.usage) are not user-facing — surface
         * one only if it carries an explicit user_message, otherwise ignore
         * it so it doesn't become banner noise. */
        const explicit = rateLimit?.user_message || (data.user_message as string | undefined)
        if (!explicit) {
          console.debug('Ignoring non-user-facing service event', env.type)
          return
        }
        message = explicit
      }
    }

    /* Surface as a dismissible notice right above the composer (a chat-send
     * concern) — never lock the composer. The user can send again and will
     * simply see the notice again if still limited. */
    dispatch(chatActions.pushBanner({ tone, text: message, placement: 'composer' }))
    if (env.type === 'rate_limit.attachment_failure') {
      dispatch(chatActions.setComposerFiles([]))
    }
  }

  const connectStream = async () => {
    /* The stream only opens for an authenticated profile — this is the
     * single client-side guarantee that an anonymous visitor cannot start
     * a chat (the server enforces the same). Any caller (mount, send,
     * reconnect) routes through here, so anonymous never reaches the SSE. */
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
    /* Anonymous visitors can read but not send — ask the host to show its
     * login surface and stop here (no stream, no POST). Checked before the
     * queue so we don't churn the send chain. */
    if (!authedRef.current) {
      promptLogin()
      return
    }
    /* Queue this send behind any in-flight one. New tail = our promise.
     * `previousTail` is captured before we install ours, so concurrent
     * callers each wait for the prior tail and stack in arrival order. */
    const previousTail = sendQueueRef.current
    let resolveOurs!: () => void
    const ours = new Promise<void>((res) => { resolveOurs = res })
    sendQueueRef.current = ours
    try {
      await previousTail
    } catch {
      /* Swallow — prior send already handled its own error inside its
       * try/catch; we just want the serialization to advance. */
    }

    try {
      /* Read from the store directly (NOT stateRef) so we see the
       * previous queued send's submitAck dispatch — stateRef is synced
       * via a post-commit useEffect, which fires after a microtask
       * boundary and would leave us reading stale state here. */
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
      /* Host-dropped context objects ride along as event
       * occurrences before the reactive user prompt. They are never appended
       * into the message text: canvas, wizard, and canvas pins are separate
       * context events with their own source ids and refs. */
      const draftContexts = isSteer || textOverride !== undefined ? [] : snapshot.composerContexts
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
      if (!draftText && draftFiles.length === 0 && !isSteer) return

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
        draftText,
        draftAttachments,
        sentAt,
        additionalEventType,
      }))
      void refreshConversationList()
    } catch (error) {
      const text = messageForError(error)
      /* Server-authority backstop: a token can expire mid-session. If the
       * POST is rejected as unauthenticated, drop to anonymous, close the
       * stream, and prompt login instead of surfacing a raw error turn. */
      if (/\b(401|403|unauthorized|forbidden)\b/i.test(text)) {
        applyAuthed(false)
        resetTransport()
        dispatch(chatActions.setConnectionState('disconnected'))
        promptLogin()
        return
      }
      /* A rejected send POST returns the raw HTTP body (e.g. a 413 HTML
       * page). Never surface that as an error turn. Restore the draft so the
       * concise notice sits right next to the message / attachment that
       * caused it (the attachment pill reappears in the composer), and keep
       * the raw text in the console for debugging. */
      console.error('send failed', text)
      if (draftText) dispatch(chatActions.setComposerText(draftText))
      if (draftFiles.length > 0) dispatch(chatActions.setComposerFiles(draftFiles))
      const { text: noticeText, tone: noticeTone } = describeSendError(text)
      dispatch(chatActions.pushBanner({ tone: noticeTone, text: noticeText, placement: 'composer' }))
    }
    } finally {
      /* Always advance the queue, even if the body returned early
       * (empty draft) or threw. */
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

  /* Resolve who the visitor is (server-authoritative via /profile) and,
   * if authenticated, open the stream and load their conversations.
   * Called at boot and again whenever the host re-posts runtime config
   * (e.g. after a successful login), so signing in upgrades the session
   * in place with no reload. */
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
      /* Anonymous: keep the stream closed but leave the composer enabled
       * (a non-'booting' connection state) so a send attempt can trigger
       * the host login surface. */
      dispatch(chatActions.setConnectionState('disconnected'))
    }
  }

  useEffect(() => {
    let mounted = true
    /* Host popup login finished: the landing signs in via a popup (no page
     * reload), then posts `kdcube-auth-changed`. The cookies are already set
     * same-origin, so re-resolving auth picks up the signed-in profile and
     * opens the stream in place — no reload, the typed message / dropped
     * context survive. */
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
        /* React to later runtime-config pushes: a host login success
         * re-posts CONFIG_RESPONSE with tokens, so re-resolve auth and
         * open the stream in place. */
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
  }, [])

  useEffect(() => {
    if (!ready) return
    void refreshConversationList()
  }, [ready, bundleId])

  /* Stable per-render handlers — must be declared BEFORE any early
   * return so hook call order is identical on every render (the
   * `if (!ready) return …` guard below would otherwise skip these on
   * the boot render and add them on the ready render, tripping React
   * #310). `useStableCallback` returns a function reference that never
   * changes across renders but always invokes the latest closure;
   * that's what lets `memo(TurnView)` / `memo(Composer)` /
   * `memo(ConversationsSidebar)` actually skip work during streaming.
   *
   * The closures reference helpers (`loadConversation`,
   * `deleteConversation`, `refreshConversationList`, `startNewChat`,
   * `sendMessage`) defined later in this function body. JS closure
   * semantics make that safe — the lookup happens at invocation time,
   * not at the useStableCallback call. */
  const handleBannerDismiss = useStableCallback((id: string) => {
    if (id === 'boot-error') {
      setBootError(null)
      return
    }
    dispatch(chatActions.dismissBanner(id))
  })
  const handleConversationSelect = useStableCallback((conversationId: string) => {
    void loadConversation(conversationId)
  })
  const handleConversationDelete = useStableCallback((conversation: ConversationSummary) => {
    void deleteConversation(conversation)
  })
  const handleConversationRefresh = useStableCallback(() => {
    void refreshConversationList()
  })
  const handleStartNewChat = useStableCallback(() => {
    startNewChat()
  })
  const handleCompactConvSelect = useStableCallback((conversationId: string) => {
    setConvMenuOpen(false)
    void loadConversation(conversationId)
  })
  const handleCompactNewChat = useStableCallback(() => {
    setConvMenuOpen(false)
    startNewChat()
  })
  const handleTurnDownloadError = useStableCallback((text: string) => {
    dispatch(chatActions.pushBanner({ tone: 'error', text: `Download failed: ${text}` }))
  })
  /* Thumbs up/down on an assistant turn. Optimistic — reflect the choice
   * immediately, persist in the background, and revert + warn on failure.
   * `reaction === null` clears. Feedback is a signed-in action, but turns
   * only exist for authenticated sessions so no extra anon guard is needed. */
  const handleTurnFeedback = useStableCallback(
    (turnId: string, reaction: TurnReaction | null, text?: string) => {
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
    },
  )
  /* Clicking a suggested followup chip populates the composer with
   * the suggested text and focuses it — the user reviews / edits and
   * presses Send (or Cmd/Ctrl+Enter) themselves. Sending immediately
   * was surprising; users want a beat to revise the question. */
  const handleTurnFollowup = useStableCallback((text: string) => {
    const snapshot = stateRef.current
    if (snapshot.inputLocked || snapshot.connection === 'booting') return
    dispatch(chatActions.setComposerText(text))
    /* Best-effort focus on the composer textarea so the cursor is
     * already there for editing. */
    window.requestAnimationFrame(() => {
      const textarea = document.querySelector('.k-composer textarea') as HTMLTextAreaElement | null
      if (textarea) {
        textarea.focus()
        const end = textarea.value.length
        textarea.setSelectionRange(end, end)
      }
    })
  })
  const handleComposerTextChange = useStableCallback((value: string) => {
    dispatch(chatActions.setComposerText(value))
  })
  const handleComposerFilesAdd = useStableCallback((files: FileList | null) => {
    if (files) dispatch(chatActions.addComposerFiles(Array.from(files)))
  })
  const handleDropFiles = useStableCallback((files: File[]) => {
    if (files.length > 0) dispatch(chatActions.addComposerFiles(files))
  })
  const handleOpenWebapp = useStableCallback(() => setLeftPaneMode('webapp'))
  const handleBackToChats = useStableCallback(() => setLeftPaneMode('chats'))
  const handleCollapseLeftPane = useStableCallback(() => setLeftPaneMode('collapsed'))
  const handleExpandWebapp = useStableCallback(() => setWebappModalOpen(true))
  const handleCloseWebappModal = useStableCallback(() => setWebappModalOpen(false))
  const handleComposerFileRemove = useStableCallback((index: number) => {
    dispatch(chatActions.removeComposerFile(index))
  })
  const handleContextRemoveMany = useStableCallback((ids: string[]) => {
    const uniqueIds = Array.from(new Set(ids.map((id) => String(id || '').trim()).filter(Boolean)))
    if (!uniqueIds.length) return
    uniqueIds.forEach((id) => dispatch(chatActions.removeComposerContext(id)))
    try {
      if (window.parent !== window) {
        window.parent.postMessage({ type: CHAT_CONTEXT_REMOVE_MESSAGE, ids: uniqueIds }, '*')
      }
    } catch {
      // Parent sync is best-effort; local composer removal already happened.
    }
  })
  const handleContextRemove = useStableCallback((id: string) => {
    handleContextRemoveMany([id])
  })
  const handleContextsAdd = useStableCallback((contexts: AttachedContext[]) => {
    contexts.forEach((ctx) => dispatch(chatActions.addComposerContext(ctx)))
  })
  const handleComposerSubmit = useStableCallback(() => {
    if (!authedRef.current) {
      promptLogin()
      return
    }
    const snapshot = store.getState().chat
    if (snapshot.inputLocked || snapshot.connection === 'booting') return
    /* Pass no event type — sendMessage reads fresh store state
     * AFTER awaiting the previous queued send, so it correctly sees
     * the newly-created active turn from a just-completed first send
     * and submits `event.user.followup`. */
    void sendMessage()
  })
  const handleComposerStop = useStableCallback(() => {
    const snapshot = stateRef.current
    if (snapshot.inputLocked || snapshot.connection === 'booting') return
    void sendMessage('', 'event.user.steer')
  })

  /* Reserved for a future bundle side panel. Keep the hook in the same
   * position as the copied shell, but hide the affordance until that widget is
   * declared by the bundle. */
  const webappWidgetUrl = useMemo(() => '', [])

  if (!ready) {
    return (
      <div className="shell-grid flex min-h-screen items-center justify-center px-6">
        <div className="glass-panel px-6 py-5 text-center">
          <div className="k-status k-live justify-center">Connecting application config…</div>
        </div>
      </div>
    )
  }

  const connectionDotClass =
    state.connection === 'connected'
      ? 'k-status'
      : state.connection === 'disconnected'
        ? 'k-status k-crit k-pulse-slow'
        : 'k-status k-live'
  const connectionLabel =
    state.connection === 'connected'
      ? `${settings.getTenant() || 'tenant'} / ${settings.getProject() || 'project'}`
      : state.connection === 'disconnected'
        ? 'Disconnected'
        : state.connection === 'connecting'
          ? 'Connecting'
          : state.connection

  /* Pre-compute prop values that drive memoised children so JSX doesn't
   * re-evaluate them inline (and so the dependent components see the
   * same boolean reference when nothing has changed). */
  const sendingDisabled = state.inputLocked || state.connection === 'booting'
  const reconnectDisabled = !authed || state.connection === 'booting' || state.connection === 'connecting'
  const reconnectLabel = state.connection === 'connecting' ? 'Connecting...' : 'Reconnect'
  /* Compact (landing tile) view: no sidebar, trimmed appbar. The composer
   * keeps full functionality (attach + stop) in both views. */
  const compact = hostView === 'compact'
  const hostEmbedMode = isHostEmbedMode()
  const leftPaneVisible = !compact && leftPaneMode !== 'collapsed'
  /* Dev preview: inside the KDCube frame the iframe is full-window, so the
   * compact view would fill the screen and you can't judge the landing tile.
   * Box it to a fixed tile size (centered, on a faint stage) so it looks the
   * way it will when the landing host sizes the iframe. Never applies to the
   * real embed (kdcubePreview is false there) — there the host sets the size
   * and compact simply fills it. */
  const previewTile = compact && kdcubePreview
  /* Notices split by placement, each capped to the 2 newest so they never
   * accumulate or grow the view, and all dismissible:
   *  - composer: chat-send concerns (rate-limit / economic / send errors /
   *    sign-in hint) shown right ABOVE the chat input.
   *  - top: app-level (boot/connection, list errors) at the top strip. */
  const composerBanners = state.banners.filter((b) => b.placement === 'composer').slice(0, 2)
  const topBanners = (
    bootError
      ? [{ id: 'boot-error', tone: 'error' as BannerTone, text: bootError }, ...state.banners.filter((b) => b.placement !== 'composer')]
      : state.banners.filter((b) => b.placement !== 'composer')
  ).slice(0, 2)
  const handleDismissAllBanners = () => {
    setBootError(null)
    dispatch(chatActions.clearBanners())
  }

  return (
    <div className={`shell-grid ${previewTile ? 'k-preview-stage' : ''}`} onPointerDownCapture={notifyHostWidgetFocus}>
      <div
        className={`relative flex w-full flex-col ${hostEmbedMode ? 'mx-0' : 'mx-auto'} ${
          previewTile
            ? 'my-6 h-[560px] max-w-[600px] overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--surface)] shadow-lg'
            : compact
              ? `k-chat-compact h-screen ${hostEmbedMode ? 'max-w-none' : 'max-w-[1320px]'} overflow-hidden`
              : `min-h-screen lg:h-screen ${hostEmbedMode ? 'max-w-none' : 'max-w-[1320px]'} lg:overflow-hidden`
        }`}
      >
        {/* Turn navigation cluster (vertical stack, fixed to the viewport;
            absolute inside the synthetic boxed preview). First/Prev/Next step
            between user messages; Latest jumps to the bottom. Shown for
            multi-turn chats, or single-turn when scrolled away from the bottom. */}
        {(!compact && state.turns.length > 1) || showScrollDown ? (
          <div className={`k-turn-nav ${previewTile ? 'k-scroll-in-tile' : ''}`}>
            {!compact && state.turns.length > 1 ? (
              <>
                <button type="button" className="k-turn-nav-btn" onClick={() => scrollToTurn('first')} aria-label="Jump to first message" title="First message">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M5 4h14M12 20V9M7 14l5-5 5 5" /></svg>
                </button>
                <button type="button" className="k-turn-nav-btn" onClick={() => scrollToTurn('prev')} aria-label="Previous message" title="Previous message">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 15l6-6 6 6" /></svg>
                </button>
                <button type="button" className="k-turn-nav-btn" onClick={() => scrollToTurn('next')} aria-label="Next message" title="Next message">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 9l6 6 6-6" /></svg>
                </button>
              </>
            ) : null}
            <button type="button" className="k-turn-nav-btn k-turn-nav-latest" onClick={scrollToBottom} aria-label="Scroll to latest" title="Latest">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12l7 7 7-7" /></svg>
              <span>Latest</span>
            </button>
          </div>
        ) : null}
        <header className="k-appbar">
          <div className="k-brand min-w-0">
            {/* KDCube favicon (same robot/cube mark as kdcube.tech). Inlined
                rather than fetched so it never 404s under the bundle's
                dynamic serving path. */}
            <svg
              className="k-brand-mark"
              width="20"
              height="20"
              viewBox="0 0 64 64"
              aria-hidden="true"
              xmlns="http://www.w3.org/2000/svg"
            >
              <defs>
                <linearGradient id="taskTrackerBrandBody" x1="0%" y1="0%" x2="0%" y2="100%">
                  <stop offset="0%" stopColor="#C6F3F1" />
                  <stop offset="100%" stopColor="#4372C3" />
                </linearGradient>
              </defs>
              <line x1="32" y1="7" x2="32" y2="17" stroke="#2B4B8A" strokeWidth="3" strokeLinecap="round" />
              <circle cx="32" cy="5" r="5" fill="#6B63FE" stroke="#06101E" strokeWidth="1.5" />
              <rect x="7" y="17" width="50" height="40" fill="url(#taskTrackerBrandBody)" stroke="#06101E" strokeWidth="2.5" rx="4" />
              <circle cx="23" cy="35" r="9" fill="white" stroke="#06101E" strokeWidth="1.5" />
              <circle cx="23" cy="35" r="4" fill="#06101E" />
              <circle cx="41" cy="35" r="9" fill="white" stroke="#06101E" strokeWidth="1.5" />
              <circle cx="41" cy="35" r="4" fill="#06101E" />
              <path d="M 23 48 Q 32 55 41 48" stroke="#06101E" strokeWidth="2.5" fill="none" strokeLinecap="round" />
              <rect x="13" y="57" width="12" height="7" fill="#4372C3" stroke="#06101E" strokeWidth="1.5" rx="1.5" />
              <rect x="39" y="57" width="12" height="7" fill="#4372C3" stroke="#06101E" strokeWidth="1.5" rx="1.5" />
            </svg>
            {compact ? (
              /* Compact tile: two-line brand so the current conversation
                 title is always visible (the title header bar is hidden in
                 this view). Conversation id is on hover of the title. */
              <span className="flex min-w-0 flex-col leading-tight">
                <span className="text-[10px] font-semibold uppercase tracking-[0.05em] text-[var(--muted)]">
                  {CHAT_BRAND_LABEL}
                </span>
                <span
                  className="truncate text-[13px] font-semibold text-[var(--ink)]"
                  title={state.conversationId || undefined}
                >
                  {state.conversationTitle || (state.conversationId ? 'Untitled conversation' : 'New chat')}
                </span>
              </span>
            ) : (
              <>
                <span className="k-brand-name">{CHAT_BRAND_LABEL}</span>
                <span className="k-brand-sep">/</span>
                <span className="k-brand-path">{bundleId}</span>
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            {authed ? (
              <span
                className={connectionDotClass}
                title={`${settings.getTenant() || 'tenant'} / ${settings.getProject() || 'project'}`}
              >
                {connectionLabel}
              </span>
            ) : (
              <span className="k-status k-live" title="Public preview — sign in to start chatting">
                Sign in to chat
              </span>
            )}
            {/* Compact conversation picker — replaces the sidebar in the
                tile so chats can still be switched / started here. */}
            {compact && authed ? (
              <button
                type="button"
                onClick={() => setConvMenuOpen((open) => !open)}
                className={`k-iconbtn ${convMenuOpen ? 'k-iconbtn-active' : ''}`}
                aria-label="Conversations"
                title="Conversations"
                aria-haspopup="menu"
                aria-expanded={convMenuOpen}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
              </button>
            ) : null}
            {!compact ? (
              <button
                type="button"
                onClick={() =>
                  setLeftPaneMode(leftPaneMode === 'collapsed' ? 'chats' : 'collapsed')
                }
                className="k-iconbtn"
                aria-label={leftPaneMode === 'collapsed' ? 'Show side panel' : 'Hide side panel'}
                title={leftPaneMode === 'collapsed' ? 'Show side panel' : 'Hide side panel'}
                aria-pressed={leftPaneMode !== 'collapsed'}
              >
                {/* Sidebar-toggle icon — a panel with a chevron pointing in
                    the direction the panel would move when toggled. */}
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="4" width="18" height="16" rx="2" />
                  <path d="M9 4v16" />
                  {leftPaneMode === 'collapsed' ? (
                    <path d="M14 9l3 3-3 3" />
                  ) : (
                    <path d="M17 9l-3 3 3 3" />
                  )}
                </svg>
              </button>
            ) : null}
            {/* Settings opens the user-bound memories webapp — hidden for
                anonymous visitors who have no user-scoped state, and in the
                compact view whose left pane (its target) is not shown. */}
            {!compact && authed && webappWidgetUrl ? (
              <button
                type="button"
                onClick={handleOpenWebapp}
                className={`k-iconbtn ${leftPaneMode === 'webapp' ? 'k-iconbtn-active' : ''}`}
                aria-label="Open settings widget"
                title="Settings (memories)"
                aria-pressed={leftPaneMode === 'webapp'}
              >
                {/* Gear icon. */}
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
                </svg>
              </button>
            ) : null}
            {authed ? (
              <button
                type="button"
                onClick={handleReconnect}
                className={`k-reconnect-btn ${state.connection === 'disconnected' ? 'k-reconnect-btn-warn' : ''}`}
                aria-label={reconnectLabel}
                title={reconnectLabel}
                disabled={reconnectDisabled}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 12a9 9 0 1 1-3-6.7" />
                  <path d="M21 3v6h-6" />
                </svg>
                <span>{reconnectLabel}</span>
              </button>
            ) : null}
            {/* Dev-only: switch compact ↔ full layout locally, shown only when
                iframed inside a same-origin KDCube frame (e.g. the control
                plane) so the two views can be tested without a landing host. */}
            {kdcubePreview ? (
              <button
                type="button"
                onClick={toggleViewLocal}
                className={`k-iconbtn ${!compact ? 'k-iconbtn-active' : ''}`}
                aria-label={compact ? 'Preview full view' : 'Preview compact view'}
                title={compact ? 'KDCube preview: switch to full view' : 'KDCube preview: switch to compact view'}
                aria-pressed={!compact}
              >
                {/* Two-pane layout icon (distinct from the sidebar toggle). */}
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="5" width="7" height="14" rx="1.5" />
                  <rect x="14" y="5" width="7" height="14" rx="1.5" />
                </svg>
              </button>
            ) : null}
            <button
              type="button"
              onClick={toggleHostView}
              className={`k-iconbtn ${hostView === 'expanded' ? 'k-iconbtn-active' : ''}`}
              aria-label={hostView === 'expanded' ? 'Collapse' : 'Expand'}
              title={hostView === 'expanded' ? 'Collapse' : 'Expand'}
              aria-pressed={hostView === 'expanded'}
            >
              {/* Expand / collapse (host-driven fullscreen overlay). */}
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                {hostView === 'expanded' ? (
                  <path d="M9 3H5a2 2 0 0 0-2 2v4M21 9V5a2 2 0 0 0-2-2h-4M15 21h4a2 2 0 0 0 2-2v-4M3 15v4a2 2 0 0 0 2 2h4" />
                ) : (
                  <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                )}
              </svg>
            </button>
            {!compact ? (
              <button
                type="button"
                onClick={startNewChat}
                disabled={hasPendingTurn}
                className="k-btn"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 5v14M5 12h14" />
                </svg>
                New chat
              </button>
            ) : null}
          </div>
        </header>

        {/* Compact conversation dropdown. Anchored just below the appbar and
            kept inside the tile; a full-tile backdrop closes it on outside
            click. Selecting loads the chat without reopening the stream. */}
        {compact && authed && convMenuOpen ? (
          <>
            <div
              className="absolute inset-0 z-20"
              onClick={() => setConvMenuOpen(false)}
              aria-hidden="true"
            />
            <div
              className="absolute inset-x-2 top-[50px] z-30 flex flex-col overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--surface)] shadow-lg"
              role="menu"
            >
              <button type="button" className="k-conv-menu-item k-conv-menu-new" onClick={handleCompactNewChat}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 5v14M5 12h14" />
                </svg>
                New chat
              </button>
              <div className="max-h-[240px] overflow-y-auto border-t border-[var(--line-soft)]">
                {filteredConversations.length === 0 ? (
                  <div className="px-3 py-2 text-[12px] text-[var(--muted)]">
                    {state.conversationsLoading ? 'Loading chats…' : 'No saved chats yet.'}
                  </div>
                ) : (
                  filteredConversations.map((conversation) => (
                    <button
                      key={conversation.id}
                      type="button"
                      className={`k-conv-menu-item ${conversation.id === state.conversationId ? 'is-active' : ''}`}
                      onClick={() => handleCompactConvSelect(conversation.id)}
                      title={conversation.title || conversation.id}
                    >
                      <span className="truncate">{conversation.title || 'Untitled conversation'}</span>
                      {(() => {
                        const when = formatConversationDate(conversation.lastActivityAt ?? conversation.startedAt)
                        return when ? <span className="k-conv-menu-date">{when}</span> : null
                      })()}
                    </button>
                  ))
                )}
              </div>
            </div>
          </>
        ) : null}

        <main className={`flex-1 ${compact ? 'flex min-h-0 flex-col overflow-hidden' : 'lg:flex lg:min-h-0 lg:flex-col lg:overflow-hidden px-3 py-3 sm:px-4 sm:py-4 lg:px-6 lg:py-5'}`}>
          {topBanners.length > 0 ? (
            <div className={compact ? 'px-3 pt-2' : 'pb-3'}>
              {topBanners.length > 1 ? (
                <div className="flex justify-end pb-1">
                  <button type="button" className="k-btn k-sm k-ghost" onClick={handleDismissAllBanners}>
                    Dismiss all
                  </button>
                </div>
              ) : null}
              <BannerStrip banners={topBanners} onDismiss={handleBannerDismiss} />
            </div>
          ) : null}

          <div
            className={`grid gap-3 lg:gap-4 ${compact ? 'min-h-0 flex-1 grid-rows-[minmax(0,1fr)]' : 'lg:min-h-0 lg:flex-1 lg:grid-rows-[minmax(0,1fr)]'} ${
              leftPaneVisible
                ? 'lg:grid-cols-[260px_minmax(0,1fr)]'
                : 'lg:grid-cols-[minmax(0,1fr)]'
            }`}
          >
            {leftPaneVisible && leftPaneMode === 'chats' ? (
              <ConversationsSidebar
                conversations={filteredConversations}
                query={conversationQuery}
                activeConversationId={state.conversationId}
                disabled={hasPendingTurn}
                loading={state.conversationsLoading}
                error={state.conversationsError}
                loadingConversationId={state.conversationLoadingId}
                deletingConversationId={state.conversationDeletingId}
                onQueryChange={setConversationQuery}
                onRefresh={handleConversationRefresh}
                onSelect={handleConversationSelect}
                onStartNew={handleStartNewChat}
                onDelete={handleConversationDelete}
              />
            ) : null}

            {leftPaneVisible && leftPaneMode === 'webapp' && webappWidgetUrl ? (
              <WebappPane
                src={webappWidgetUrl}
                title="Memories"
                onBackToChats={handleBackToChats}
                onExpand={handleExpandWebapp}
                onCollapse={handleCollapseLeftPane}
              />
            ) : null}

            <FileDropZone
              onFiles={handleDropFiles}
              disabled={sendingDisabled}
              className={`min-w-0 flex ${compact ? 'min-h-0' : 'lg:min-h-0'}`}
            >
            <div className={`glass-panel min-w-0 overflow-hidden flex flex-col flex-1 ${compact ? 'min-h-0 k-flush' : 'lg:min-h-0'}`}>
              {/* Conversation-title header bar — hidden in the compact tile
                  for the clean single-surface look (the appbar carries the
                  identity there). Shown in the full view. */}
              {!compact ? (
              <section className="flex items-center justify-between gap-3 border-b border-[var(--line-soft)] px-4 py-2.5">
                <div className="group min-w-0">
                  <div className="flex min-w-0 items-center gap-1.5">
                    {/* Conversation id is no longer printed as a line; it is
                        available on hover (tooltip) and via the copy button. */}
                    <span
                      className="truncate text-[15px] font-semibold text-[var(--ink)]"
                      title={state.conversationId || undefined}
                    >
                      {state.conversationTitle || (state.conversationId ? 'Untitled conversation' : 'New chat')}
                    </span>
                    {state.conversationId ? (
                      <span className="opacity-0 transition-opacity group-hover:opacity-100">
                        <CopyButton value={state.conversationId} title="Copy conversation id" />
                      </span>
                    ) : null}
                  </div>
                  {!state.conversationId ? (
                    <div className="truncate text-[11px] text-[var(--muted)]">
                      {state.conversationsLoading ? 'Refreshing chats…' : `${state.conversations.length} saved chat${state.conversations.length === 1 ? '' : 's'}`}
                    </div>
                  ) : null}
                </div>
              </section>
              ) : null}

              <div
                ref={scrollContainerRef}
                className={`k-chat-scroll px-4 py-3 ${compact ? 'min-h-0 flex-1 overflow-y-auto' : 'flex-1 lg:min-h-0 lg:overflow-y-auto'}`}
              >
                {state.turns.length === 0 ? (
                  <div className="k-empty">
                    <div className="k-empty-title">No turns yet</div>
                    <div className="k-empty-body">Ask anything — attachments, web search, and code exec are available.</div>
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {[
                        'Summarize the last attachment as markdown',
                        'Search the web and cite three sources',
                        'Create a report with diagrams',
                      ].map((prompt) => (
                        <button
                          key={prompt}
                          type="button"
                          className="k-followup"
                          onClick={() => dispatch(chatActions.setComposerText(prompt))}
                        >
                          {prompt}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col gap-4">
                    {state.turns.map((turn) => (
                      <TurnView
                        key={turn.id}
                        turn={turn}
                        sendingDisabled={sendingDisabled}
                        reaction={state.feedback[turn.id] ?? null}
                        onFeedback={handleTurnFeedback}
                        onDownloadError={handleTurnDownloadError}
                        onFollowup={handleTurnFollowup}
                      />
                    ))}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>

              <div className="k-composer-zone border-t border-[var(--line-soft)] px-3 py-3">
                <div className="k-react-preview-toggle">
                  <label>
                    <input
                      type="checkbox"
                      checked={dryRunEnabled}
                      onChange={(event) => setDryRunEnabled(event.target.checked)}
                    />
                    <span>Dry run ReAct context</span>
                  </label>
                  {dryRunLoading ? <span className="k-react-preview-status">Rendering…</span> : null}
                  {dryRunPreview?.debug_paths?.full ? (
                    <span className="k-react-preview-status" title={dryRunPreview.debug_paths.full}>
                      debug file written
                    </span>
                  ) : null}
                </div>
                {dryRunError || dryRunPreview ? (
                  <section className="k-react-preview-panel" aria-label="ReAct context dry-run preview">
                    <div className="k-react-preview-head">
                      <div>
                        <strong>ReAct Context Preview</strong>
                        <span>
                          {dryRunPreview
                            ? `${dryRunPreview.event_count ?? 0} events · ${dryRunPreview.block_count ?? 0} blocks · ${dryRunPreview.announce_block_count ?? 0} announce`
                            : 'preview failed'}
                        </span>
                      </div>
                      <button
                        type="button"
                        className="k-iconbtn"
                        aria-label="Close preview"
                        onClick={() => {
                          setDryRunPreview(null)
                          setDryRunError(null)
                        }}
                      >
                        ×
                      </button>
                    </div>
                    {dryRunError ? (
                      <div className="k-react-preview-error">{dryRunError}</div>
                    ) : null}
                    {dryRunPreview ? (
                      <div className="k-react-preview-sections">
                        <details open>
                          <summary>ANNOUNCE</summary>
                          <pre>{dryRunPreview.announce_text || '(no announce blocks)'}</pre>
                        </details>
                        <details>
                          <summary>Timeline</summary>
                          <pre>{dryRunPreview.timeline_text || '(empty timeline)'}</pre>
                        </details>
                        <details>
                          <summary>Full Rendered Context</summary>
                          <pre>{dryRunPreview.rendered_text || '(empty render)'}</pre>
                        </details>
                      </div>
                    ) : null}
                  </section>
                ) : null}
                {/* Chat-send notices (rate-limit / economic / send errors /
                    sign-in hint) sit right above the input, concise and
                    dismissible — never as error turns or a frozen composer. */}
                {composerBanners.length > 0 ? (
                  <div className="pb-2">
                    <BannerStrip banners={composerBanners} onDismiss={handleBannerDismiss} />
                  </div>
                ) : null}
                <Composer
                  text={state.composerText}
                  files={state.composerFiles}
                  contexts={state.composerContexts}
                  disabled={sendingDisabled || dryRunLoading}
                  inProgress={hasPendingTurn}
                  lockedMessage={state.inputLockMessage}
                  onTextChange={handleComposerTextChange}
                  onFilesAdd={handleComposerFilesAdd}
                  onFileRemove={handleComposerFileRemove}
                  onContextsAdd={handleContextsAdd}
                  onContextRemove={handleContextRemove}
                  onContextRemoveMany={handleContextRemoveMany}
                  onSubmit={handleComposerSubmit}
                  onStop={handleComposerStop}
                />
              </div>
            </div>
            </FileDropZone>
          </div>
        </main>
      </div>
      {webappModalOpen && webappWidgetUrl ? (
        <WebappModal
          src={webappWidgetUrl}
          title="Memories"
          onClose={handleCloseWebappModal}
        />
      ) : null}
    </div>
  )
}
