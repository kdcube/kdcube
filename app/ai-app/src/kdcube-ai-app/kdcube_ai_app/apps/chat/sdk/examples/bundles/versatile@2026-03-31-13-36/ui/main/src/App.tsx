import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  deleteConversationById,
  fetchConversationById,
  listBundleConversations,
  openChatStream,
  requestConversationStatus,
  submitChatMessage,
} from './service.ts'
import type {
  BannerTone,
  ChatServiceEnvelope,
  ContinuationKind,
  ConversationSummary,
  RateLimitPayload,
} from './service.ts'
import { BUILT_BUNDLE_ID, createLocalId, settings } from './settings.ts'

import type {
  AdditionalUserMessage,
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
import { chatActions } from './features/chat/chatSlice.ts'

import { BannerStrip } from './features/banners/BannerStrip.tsx'
import { ConversationsSidebar } from './features/conversations/ConversationsSidebar.tsx'
import { Composer } from './features/composer/Composer.tsx'
import { TurnView } from './features/chat/TurnView.tsx'

export default function App() {
  const state = useAppSelector((s) => s.chat)
  const dispatch = useAppDispatch()
  const [ready, setReady] = useState(false)
  const [bootError, setBootError] = useState<string | null>(null)
  const [conversationQuery, setConversationQuery] = useState('')

  const stateRef = useRef<ChatState>(state)
  const eventSourceRef = useRef<EventSource | null>(null)
  const connectPromiseRef = useRef<Promise<void> | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const streamIdRef = useRef<string | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const autoScrollRef = useRef(true)
  const [showScrollDown, setShowScrollDown] = useState(false)

  useEffect(() => {
    stateRef.current = state
  }, [state])

  useEffect(() => {
    const updateAutoScroll = () => {
      const doc = document.documentElement
      const scrollTop = window.scrollY || doc.scrollTop || 0
      const remaining = doc.scrollHeight - (scrollTop + window.innerHeight)
      const near = remaining < 140
      autoScrollRef.current = near
      setShowScrollDown(!near && doc.scrollHeight > window.innerHeight + 80)
    }

    updateAutoScroll()
    window.addEventListener('scroll', updateAutoScroll, { passive: true })
    window.addEventListener('resize', updateAutoScroll)
    return () => {
      window.removeEventListener('scroll', updateAutoScroll)
      window.removeEventListener('resize', updateAutoScroll)
    }
  }, [])

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }

  /* Auto-scroll dep tracks a compact signature of "what has visually
   * grown" — turn count + the active turn's answer length + banner
   * count + ready. This fires on streaming deltas (so the page keeps
   * up with the answer) but skips no-op renders that didn't add height. */
  const lastTurn = state.turns[state.turns.length - 1]
  const scrollSignature = `${state.turns.length}:${lastTurn?.id ?? ''}:${lastTurn?.answer.length ?? 0}:${lastTurn?.timeline.length ?? 0}:${lastTurn?.artifacts.length ?? 0}:${state.banners.length}:${ready ? 1 : 0}`
  useEffect(() => {
    if (!autoScrollRef.current) return
    bottomRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' })
  }, [scrollSignature])

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

  const resetTransport = () => {
    eventSourceRef.current?.close()
    eventSourceRef.current = null
    streamIdRef.current = null
    connectPromiseRef.current = null
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
        tone = (data.notification_type as BannerTone | undefined) || 'error'
        message = (data.user_message as string | undefined) || 'This service is not available for your account type.'
        break
      case 'rate_limit.subscription_exhausted':
        tone = (data.notification_type as BannerTone | undefined) || 'error'
        message =
          (data.user_message as string | undefined) ||
          'Your subscription balance is exhausted. Top up your balance to continue.'
        break
      case 'rate_limit.project_exhausted': {
        tone = 'error'
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
        tone = (data.notification_type as BannerTone | undefined) || 'error'
        message = (data.user_message as string | undefined) || 'Attachment was rejected.'
        break
      case 'rate_limit.lane_switch':
      case 'economics.user_underfunded_absorbed':
        return
      default:
        message =
          rateLimit?.user_message ||
          (data.user_message as string | undefined) ||
          `${env.type}: service message received`
    }

    dispatch(chatActions.pushBanner({ tone, text: message }))
    const shouldLockInput =
      tone === 'error' &&
      env.type !== 'rate_limit.attachment_failure' &&
      env.type !== 'rate_limit.warning'
    if (shouldLockInput) {
      dispatch(chatActions.lockInput(message))
    }
    if (env.type === 'rate_limit.attachment_failure') {
      dispatch(chatActions.setComposerFiles([]))
    }
  }

  const connectStream = async () => {
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
        onChatStep: (env) => dispatch(chatActions.chatStep(env)),
        onChatDelta: (env) => dispatch(chatActions.chatDelta(env)),
        onChatComplete: (env) => {
          dispatch(chatActions.chatCompleted(env))
          void refreshConversationList()
        },
        onChatError: (env) => dispatch(chatActions.chatErrored(env)),
        onConversationStatus: (env) => dispatch(chatActions.convStatusUpdated(env)),
        onChatService: handleServiceEvent,
        onDisconnect: () => dispatch(chatActions.setConnectionState('disconnected')),
      })

      eventSourceRef.current = transport.eventSource
      streamIdRef.current = transport.streamId
      sessionIdRef.current = transport.sessionId
      dispatch(chatActions.setConnectionState('connected'))
      dispatch(chatActions.setSessionId(transport.sessionId))
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

  const sendMessage = async (textOverride?: string, requestedKind?: ContinuationKind) => {
    const snapshot = stateRef.current
    const activeTurn = findActiveTurn(snapshot.turns)
    let continuationKind: ContinuationKind = requestedKind ?? (activeTurn ? 'followup' : 'regular')
    if (continuationKind !== 'regular' && !activeTurn) {
      continuationKind = 'regular'
    }
    const isContinuation = continuationKind === 'followup' || continuationKind === 'steer'
    const isSteer = continuationKind === 'steer'
    const continuationMessageKind: Exclude<ContinuationKind, 'regular'> =
      continuationKind === 'steer' ? 'steer' : 'followup'
    const targetTurnId = isContinuation ? activeTurn?.id : undefined
    const draftText = (textOverride ?? snapshot.composerText).trim()
    const draftFiles = isSteer || textOverride !== undefined ? [] : snapshot.composerFiles
    if (!draftText && draftFiles.length === 0 && !isSteer) return

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
        ...(isContinuation
          ? {
              messageKind: continuationKind,
              continuationKind,
              activeTurnId: targetTurnId,
              targetTurnId,
              followup: continuationKind === 'followup',
              steer: continuationKind === 'steer',
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
        },
        existingConversationId,
        isContinuation,
        isSteer,
        targetTurnId: targetTurnId ?? null,
        draftText,
        draftAttachments,
        sentAt,
        continuationMessageKind,
      }))
      void refreshConversationList()
    } catch (error) {
      const text = messageForError(error)
      const errorTurnId = isContinuation && targetTurnId ? targetTurnId : createLocalId('client_submit_error')
      const latest = stateRef.current
      dispatch(chatActions.chatErrored({
        type: 'chat.error',
        timestamp: new Date().toISOString(),
        service: { request_id: createLocalId('request') },
        conversation: {
          session_id: latest.sessionId || '',
          conversation_id: existingConversationId || latest.conversationId || '',
          turn_id: errorTurnId,
        },
        event: {
          step: 'send',
          status: 'error',
          title: 'Send failed',
        },
        data: { error: text },
      }))
    }
  }

  const handleReconnect = async () => {
    resetTransport()
    try {
      await connectStream()
      setBootError(null)
    } catch (error) {
      setBootError(messageForError(error))
    }
  }

  useEffect(() => {
    let mounted = true
    ;(async () => {
      try {
        await settings.setupParentListener()
        if (!mounted) return
        setReady(true)
        await connectStream()
      } catch (error) {
        if (!mounted) return
        setBootError(messageForError(error))
      }
    })()

    return () => {
      mounted = false
      resetTransport()
    }
  }, [])

  useEffect(() => {
    if (!ready) return
    void refreshConversationList()
  }, [ready, bundleId])

  if (!ready) {
    return (
      <div className="shell-grid flex min-h-screen items-center justify-center px-6">
        <div className="glass-panel px-6 py-5 text-center">
          <div className="k-status k-live justify-center">Connecting application config…</div>
        </div>
      </div>
    )
  }

  /* Stable per-render handlers — `useStableCallback` returns a function
   * reference that never changes across renders but always invokes the
   * latest closure. This is what lets `memo(TurnView)` /
   * `memo(Composer)` / `memo(ConversationsSidebar)` actually skip work
   * during streaming, since otherwise every inline `onX={() => …}`
   * would allocate a fresh function each render and bust memoization. */
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
  const handleTurnDownloadError = useStableCallback((text: string) => {
    dispatch(chatActions.pushBanner({ tone: 'error', text: `Download failed: ${text}` }))
  })
  const handleTurnFollowup = useStableCallback((text: string) => {
    const snapshot = stateRef.current
    if (snapshot.inputLocked || snapshot.connection === 'booting') return
    const hasPending = snapshot.turns.some(
      (turn) => turn.state === 'pending' || turn.state === 'running',
    )
    void sendMessage(text, hasPending ? 'followup' : 'regular')
  })
  const handleComposerTextChange = useStableCallback((value: string) => {
    dispatch(chatActions.setComposerText(value))
  })
  const handleComposerFilesAdd = useStableCallback((files: FileList | null) => {
    if (files) dispatch(chatActions.addComposerFiles(Array.from(files)))
  })
  const handleComposerFileRemove = useStableCallback((index: number) => {
    dispatch(chatActions.removeComposerFile(index))
  })
  const handleComposerSubmit = useStableCallback(() => {
    const snapshot = stateRef.current
    if (snapshot.inputLocked || snapshot.connection === 'booting') return
    const hasPending = snapshot.turns.some(
      (turn) => turn.state === 'pending' || turn.state === 'running',
    )
    void sendMessage(undefined, hasPending ? 'followup' : 'regular')
  })
  const handleComposerStop = useStableCallback(() => {
    const snapshot = stateRef.current
    if (snapshot.inputLocked || snapshot.connection === 'booting') return
    void sendMessage('', 'steer')
  })

  const connectionDotClass =
    state.connection === 'connected'
      ? 'k-status'
      : state.connection === 'disconnected'
        ? 'k-status k-crit'
        : 'k-status k-live'

  /* Pre-compute prop values that drive memoised children so JSX doesn't
   * re-evaluate them inline (and so the dependent components see the
   * same boolean reference when nothing has changed). */
  const sendingDisabled = state.inputLocked || state.connection === 'booting'

  return (
    <div className="shell-grid">
      <button
        type="button"
        className={`k-scroll-to-bottom ${showScrollDown ? 'k-show' : ''}`}
        onClick={scrollToBottom}
        aria-label="Scroll to latest"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 5v14M5 12l7 7 7-7" />
        </svg>
        <span>Latest</span>
      </button>
      <div className="mx-auto flex min-h-screen w-full max-w-[1320px] flex-col">
        <header className="k-appbar">
          <div className="k-brand min-w-0">
            <span className="k-brand-mark" aria-hidden="true" />
            <span className="k-brand-name">Versatile</span>
            <span className="k-brand-sep">/</span>
            <span className="k-brand-path">{bundleId}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className={connectionDotClass}>
              {state.connection === 'connected'
                ? `${settings.getTenant() || 'tenant'} / ${settings.getProject() || 'project'}`
                : state.connection}
            </span>
            <button
              type="button"
              onClick={handleReconnect}
              className="k-iconbtn"
              aria-label="Reconnect"
              title="Reconnect"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 12a9 9 0 1 1-3-6.7" />
                <path d="M21 3v6h-6" />
              </svg>
            </button>
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
          </div>
        </header>

        <main className="flex-1 px-3 py-3 sm:px-4 sm:py-4 lg:px-6 lg:py-5">
          {bootError || state.banners.length > 0 ? (
            <div className="pb-3">
              <BannerStrip
                banners={bootError ? [{ id: 'boot-error', tone: 'error', text: bootError }, ...state.banners] : state.banners}
                onDismiss={handleBannerDismiss}
              />
            </div>
          ) : null}

          <div className="grid gap-3 lg:gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
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

            <div className="glass-panel min-w-0 overflow-hidden flex flex-col">
              <section className="flex items-center justify-between gap-3 border-b border-[var(--line-soft)] px-4 py-2.5">
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-semibold text-[var(--ink)]">
                    {state.conversationTitle || (state.conversationId ? 'Untitled conversation' : 'New chat')}
                  </div>
                  <div className="truncate text-[11px] text-[var(--muted)]">
                    {state.conversationId || (state.conversationsLoading ? 'Refreshing chats…' : `${state.conversations.length} saved chat${state.conversations.length === 1 ? '' : 's'}`)}
                  </div>
                </div>
              </section>

              <div className="flex-1 px-4 py-3">
                {state.turns.length === 0 ? (
                  <div className="k-empty">
                    <div className="k-empty-title">No turns yet</div>
                    <div className="k-empty-body">Ask anything — attachments, web search, and code exec are available.</div>
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {[
                        'Summarize the last attachment as markdown',
                        'Search the web and cite three sources',
                        'Run a small exec report',
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
                        onDownloadError={handleTurnDownloadError}
                        onFollowup={handleTurnFollowup}
                      />
                    ))}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>

              <div className="border-t border-[var(--line-soft)] px-3 py-3">
                <Composer
                  text={state.composerText}
                  files={state.composerFiles}
                  disabled={sendingDisabled}
                  inProgress={hasPendingTurn}
                  lockedMessage={state.inputLockMessage}
                  onTextChange={handleComposerTextChange}
                  onFilesAdd={handleComposerFilesAdd}
                  onFileRemove={handleComposerFileRemove}
                  onSubmit={handleComposerSubmit}
                  onStop={handleComposerStop}
                />
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}
