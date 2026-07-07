/**
 * App — the default chat view. All orchestration (transport, send pipeline,
 * conversation lifecycle, host messaging, auth/boot, context, feedback, host
 * view-form state) now lives in `useChatEngine()`. This component is a thin,
 * fully-swappable view: it consumes the engine and owns only view-local
 * concerns (search box, left-pane mode, the compact conversation dropdown,
 * scroll behavior) and the markup.
 *
 * A custom UI replaces only this file:
 *   <ChatStoreProvider config={...}><MyOwnChatUI /></ChatStoreProvider>
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { BannerTone, ConnectionsConsentOpen, ConversationSummary, NamespaceStyleMap } from '@kdcube/components-core/chat'
import type { ChatTurn } from '@kdcube/components-core/chat'
import { findActiveTurn } from '@kdcube/components-core/chat'
import { useAppDispatch, useStableCallback } from './support/hooks.ts'
import { chatActions } from '@kdcube/components-core/chat'
import { useChatViewModel } from './context.tsx'
import { BannerStrip } from './features/banners/BannerStrip.tsx'
import { ConversationsSidebar } from './features/conversations/ConversationsSidebar.tsx'
import { Composer } from './features/composer/Composer.tsx'
import { TurnView } from './features/chat/TurnView.tsx'
import { FileDropZone } from './components/FileDropZone.tsx'
import { CopyButton } from './components/CopyButton.tsx'
import { WebappPane, WebappModal } from './components/WebappPane.tsx'

function isVisibleTurn(turn: ChatTurn): boolean {
  if (turn.state === 'pending' || turn.state === 'running' || turn.state === 'error') return true
  return Boolean(
    turn.userMessage.trim() ||
    turn.userAttachments.length ||
    turn.additionalUserMessages.length ||
    turn.answer.trim() ||
    turn.error ||
    Object.keys(turn.steps).length ||
    turn.artifacts.length ||
    turn.timeline.length ||
    turn.followups.length ||
    turn.costUsd != null ||
    turn.elapsedMs != null,
  )
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

export interface ChatShellProps {
  /** Brand label shown in the header. Defaults to "Chat". */
  brandLabel?: string
  /** Identity shown in the connected status pill. Defaults to the bundle id. */
  accountLabel?: string
  /** Host-embed (full-bleed) layout. Defaults to auto-detected iframe embedding. */
  embedded?: boolean
  /** Optional left-pane sibling bundle widget. Off by default. */
  webapp?: { src: string; title?: string }
  /** Namespace-owned visual styles supplied by the host/app config. */
  namespaceStyles?: NamespaceStyleMap
}

const DEFAULT_BRAND_LABEL = 'Chat'

/** Auto-detect host-embed layout from the `chat_embed_mode=host` query param
 *  (ported from the in-tree widget's host.ts). */
function isEmbedded(): boolean {
  try {
    if (typeof window === 'undefined') return false
    return new URLSearchParams(window.location.search || '').get('chat_embed_mode') === 'host'
  } catch {
    return false
  }
}

/** Host focus-promotion is a host-scene affordance; in the package it's a no-op
 *  (a host can wire focus via the engine event bus instead). */
const NOOP = () => {}

export function ChatShell({
  brandLabel = DEFAULT_BRAND_LABEL,
  accountLabel,
  embedded = isEmbedded(),
  webapp,
  namespaceStyles = {},
}: ChatShellProps = {}) {
  const engine = useChatViewModel()
  const {
    state,
    ready,
    bootError,
    setBootError,
    authed,
    isSuperAdmin,
    hostView,
    kdcubePreview,
    bundleId,
    dryRun,
    pinConversationToCanvas,
    handleReconnect,
    promptLogin,
    send,
    steer,
  } = engine
  const dispatch = useAppDispatch()

  const {
    enabled: dryRunEnabled,
    loading: dryRunLoading,
    preview: dryRunPreview,
    error: dryRunError,
    setEnabled: setDryRunEnabled,
    clearPreview: clearDryRunPreview,
  } = dryRun

  /* View-local state. */
  const [conversationQuery, setConversationQuery] = useState('')
  const [leftPaneMode, setLeftPaneMode] = useState<'chats' | 'webapp' | 'collapsed'>('chats')
  const [webappModalOpen, setWebappModalOpen] = useState(false)
  const [convMenuOpen, setConvMenuOpen] = useState(false)
  const [showScrollDown, setShowScrollDown] = useState(false)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const scrollContainerRef = useRef<HTMLDivElement | null>(null)
  const autoScrollRef = useRef(true)

  const activeScroller = (): HTMLElement | null => {
    const el = scrollContainerRef.current
    if (!el) return null
    const oy = window.getComputedStyle(el).overflowY
    return oy === 'auto' || oy === 'scroll' ? el : null
  }

  useEffect(() => {
    if (!convMenuOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setConvMenuOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [convMenuOpen])

  /* The compact tile always opens with the conversation picker collapsed. */
  useEffect(() => {
    if (hostView === 'compact') setConvMenuOpen(false)
  }, [hostView])

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
    const el = scrollContainerRef.current
    if (el) el.addEventListener('scroll', measure, { passive: true })
    window.addEventListener('scroll', measure, { passive: true })
    window.addEventListener('resize', measure)
    return () => {
      if (el) el.removeEventListener('scroll', measure)
      window.removeEventListener('scroll', measure)
      window.removeEventListener('resize', measure)
    }
  }, [hostView, ready])

  const scrollToBottom = () => {
    autoScrollRef.current = true
    setShowScrollDown(false)
    const scroller = activeScroller()
    if (scroller) scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' })
    else bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }

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

  const visibleTurns = useMemo(() => state.turns.filter(isVisibleTurn), [state.turns])
  const lastTurn = visibleTurns[visibleTurns.length - 1]
  const scrollSignature = `${visibleTurns.length}:${lastTurn?.id ?? ''}:${lastTurn?.answer.length ?? 0}:${lastTurn?.timeline.length ?? 0}:${lastTurn?.artifacts.length ?? 0}:${state.banners.length}:${ready ? 1 : 0}`
  useEffect(() => {
    if (!autoScrollRef.current) return
    const scroller = activeScroller()
    if (scroller) scroller.scrollTop = scroller.scrollHeight
    else bottomRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' })
  }, [scrollSignature, hostView])

  const hasPendingTurn = Boolean(findActiveTurn(state.turns))
  const filteredConversations = useMemo(() => {
    const query = conversationQuery.trim().toLowerCase()
    const items = state.conversations.slice().sort((left, right) => (right.lastActivityAt || 0) - (left.lastActivityAt || 0))
    if (!query) return items
    return items.filter((item) => {
      const haystack = `${item.title || ''} ${item.id}`.toLowerCase()
      return haystack.includes(query)
    })
  }, [conversationQuery, state.conversations])

  /* Host view-form controls (engine owns the state + host messaging). */
  const toggleHostView = () => engine.setHostView(hostView === 'expanded' ? 'compact' : 'expanded')
  const toggleViewLocal = engine.setHostViewLocal
  const startNewChat = engine.newChat

  /* Stable handlers for the memoized children. Engine-backed actions are
   * already stable; the few view-only dispatches are wrapped here. */
  const handleBannerDismiss = useStableCallback((id: string) => {
    if (id === 'boot-error') {
      setBootError(null)
      return
    }
    dispatch(chatActions.dismissBanner(id))
  })
  /* Consent-card actions route through the host's connections surface (the
   * `connections.hub.open` contract) when the host wired `open-connections`;
   * with no handler BannerStrip keeps the plain deep-link anchor. */
  const handleBannerOpenConnections = useStableCallback((consent: ConnectionsConsentOpen) => {
    engine.connections.open('consent-card', consent)
  })
  const bannerOpenConnections = engine.connections.available() ? handleBannerOpenConnections : undefined
  const handleConversationSelect = engine.loadConversation
  const handleConversationDelete = engine.deleteConversation
  const handleConversationRefresh = engine.refreshConversationList
  const handleStartNewChat = engine.newChat
  const handleCompactConversationMenuToggle = useStableCallback(() => {
    setConvMenuOpen((open) => {
      const next = !open
      if (next) handleConversationRefresh()
      return next
    })
  })
  const handleCompactConvSelect = useStableCallback((conversationId: string) => {
    setConvMenuOpen(false)
    engine.loadConversation(conversationId)
  })
  const handleCompactNewChat = useStableCallback(() => {
    setConvMenuOpen(false)
    engine.newChat()
  })
  const handleTurnDownloadError = useStableCallback((text: string) => {
    dispatch(chatActions.pushBanner({ tone: 'error', text: `Download failed: ${text}` }))
  })
  const handleContextActionError = useStableCallback((text: string, tone: BannerTone = 'warning') => {
    dispatch(chatActions.pushBanner({
      tone,
      text: tone === 'error' ? `Context action failed: ${text}` : text,
    }))
  })
  const handleTurnFeedback = engine.submitFeedback
  const handleTurnFollowup = useStableCallback((text: string) => {
    if (state.inputLocked || state.connection === 'booting') return
    dispatch(chatActions.setComposerText(text))
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
  const handleBackToChats = useStableCallback(() => {
    setLeftPaneMode('chats')
    handleConversationRefresh()
  })
  const handleConversationPaneToggle = useStableCallback(() => {
    setLeftPaneMode((mode) => {
      const next = mode === 'collapsed' ? 'chats' : 'collapsed'
      if (next === 'chats') handleConversationRefresh()
      return next
    })
  })
  const handleCollapseLeftPane = useStableCallback(() => setLeftPaneMode('collapsed'))
  const handleExpandWebapp = useStableCallback(() => setWebappModalOpen(true))
  const handleCloseWebappModal = useStableCallback(() => setWebappModalOpen(false))
  const handleComposerFileRemove = useStableCallback((index: number) => {
    dispatch(chatActions.removeComposerFile(index))
  })
  const handleContextRemoveMany = engine.removeContext
  const handleContextRemove = engine.removeContext
  const handleContextsAdd = engine.attachContext
  const handleComposerSubmit = useStableCallback(() => {
    if (!authed) {
      promptLogin()
      return
    }
    if (state.inputLocked || state.connection === 'booting') return
    send()
  })
  const handleComposerStop = useStableCallback(() => {
    if (state.inputLocked || state.connection === 'booting') return
    steer()
  })

  /* Reserved for a future bundle side panel. */
  const webappWidgetUrl = useMemo(() => webapp?.src || '', [webapp])

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
      ? `${accountLabel ?? bundleId}`
      : state.connection === 'disconnected'
        ? 'Disconnected'
        : state.connection === 'connecting'
          ? 'Connecting'
          : state.connection

  const sendingDisabled = state.inputLocked || state.connection === 'booting'
  const reconnectDisabled = !authed || state.connection === 'booting' || state.connection === 'connecting'
  const reconnectLabel = state.connection === 'connecting' ? 'Connecting...' : 'Reconnect'
  const compact = hostView === 'compact'
  const hostEmbedMode = embedded
  const leftPaneVisible = !compact && leftPaneMode !== 'collapsed'
  const previewTile = compact && kdcubePreview
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
    <div className={`shell-grid ${previewTile ? 'k-preview-stage' : ''}`} onPointerDownCapture={NOOP}>
      <div
        className={`relative flex w-full flex-col ${hostEmbedMode ? 'mx-0' : 'mx-auto'} ${
          previewTile
            ? 'my-6 h-[560px] max-w-[600px] overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--surface)] shadow-lg'
            : compact
              ? `k-chat-compact h-screen ${hostEmbedMode ? 'max-w-none' : 'max-w-[1320px]'} overflow-hidden`
              : `min-h-screen lg:h-screen ${hostEmbedMode ? 'max-w-none' : 'max-w-[1320px]'} lg:overflow-hidden`
        }`}
      >
        {visibleTurns.length > 1 || showScrollDown ? (
          <div className={`k-turn-nav ${previewTile ? 'k-scroll-in-tile' : ''}`}>
            {visibleTurns.length > 1 ? (
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
              <span className="flex min-w-0 flex-col leading-tight">
                <span className="text-[10px] font-semibold uppercase tracking-[0.05em] text-[var(--muted)]">
                  {brandLabel}
                </span>
                <span className="flex min-w-0 items-center gap-1.5">
                  <span
                    className="truncate text-[13px] font-semibold text-[var(--ink)]"
                    title={state.conversationId || undefined}
                  >
                    {state.conversationTitle || (state.conversationId ? 'Untitled conversation' : 'New chat')}
                  </span>
                  {state.conversationId ? (
                    <button
                      type="button"
                      onClick={pinConversationToCanvas}
                      className="k-conv-pin shrink-0 text-[var(--muted)]"
                      aria-label="Pin this conversation to the canvas"
                      title="Pin this conversation to the canvas"
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 17v5" />
                        <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" />
                      </svg>
                    </button>
                  ) : null}
                </span>
              </span>
            ) : (
              <>
                <span className="k-brand-name">{brandLabel}</span>
                <span className="k-brand-sep">/</span>
                <span className="k-brand-path">{bundleId}</span>
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            {authed ? (
              <span
                className={connectionDotClass}
                title={`${accountLabel ?? bundleId}`}
              >
                {connectionLabel}
              </span>
            ) : (
              <span className="k-status k-live" title="Public preview — sign in to start chatting">
                Sign in to chat
              </span>
            )}
            {compact && authed ? (
              <button
                type="button"
                onClick={handleCompactConversationMenuToggle}
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
                onClick={handleConversationPaneToggle}
                className="k-iconbtn"
                aria-label={leftPaneMode === 'collapsed' ? 'Show side panel' : 'Hide side panel'}
                title={leftPaneMode === 'collapsed' ? 'Show side panel' : 'Hide side panel'}
                aria-pressed={leftPaneMode !== 'collapsed'}
              >
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
            {!compact && authed && webappWidgetUrl ? (
              <button
                type="button"
                onClick={handleOpenWebapp}
                className={`k-iconbtn ${leftPaneMode === 'webapp' ? 'k-iconbtn-active' : ''}`}
                aria-label="Open settings widget"
                title="Settings (memories)"
                aria-pressed={leftPaneMode === 'webapp'}
              >
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
            {kdcubePreview ? (
              <button
                type="button"
                onClick={toggleViewLocal}
                className={`k-iconbtn ${!compact ? 'k-iconbtn-active' : ''}`}
                aria-label={compact ? 'Preview full view' : 'Preview compact view'}
                title={compact ? 'KDCube preview: switch to full view' : 'KDCube preview: switch to compact view'}
                aria-pressed={!compact}
              >
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
              <BannerStrip banners={topBanners} onDismiss={handleBannerDismiss} onOpenConnections={bannerOpenConnections} />
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
              {!compact ? (
              <section className="flex items-center justify-between gap-3 border-b border-[var(--line-soft)] px-4 py-2.5">
                <div className="group min-w-0">
                  <div className="flex min-w-0 items-center gap-1.5">
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
                    {state.conversationId ? (
                      <button
                        type="button"
                        onClick={pinConversationToCanvas}
                        className="k-conv-pin opacity-0 transition-opacity group-hover:opacity-100"
                        aria-label="Pin this conversation to the canvas"
                        title="Pin this conversation to the canvas"
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M12 17v5" />
                          <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" />
                        </svg>
                      </button>
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
                {visibleTurns.length === 0 ? (
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
                    {visibleTurns.map((turn) => (
                      <TurnView
                        key={turn.id}
                        turn={turn}
                        conversationId={state.conversationId}
                        sendingDisabled={sendingDisabled}
                        reaction={state.feedback[turn.id] ?? null}
                        onFeedback={handleTurnFeedback}
                        onDownloadError={handleTurnDownloadError}
                        onContextActionError={handleContextActionError}
                        onFollowup={handleTurnFollowup}
                        namespaceStyles={namespaceStyles}
                      />
                    ))}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>

              <div className="k-composer-zone border-t border-[var(--line-soft)] px-3 py-3">
                {/* The ReAct dry-run tooling is a super-admin-only affordance. The
                    check is reactive: roles are re-probed from /profile on each
                    host auth-changed broadcast, so this appears/disappears when
                    auth changes. */}
                {isSuperAdmin ? (
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
                ) : null}
                {isSuperAdmin && (dryRunError || dryRunPreview) ? (
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
                        onClick={clearDryRunPreview}
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
                {composerBanners.length > 0 ? (
                  <div className="k-composer-banners pb-2">
                    <BannerStrip banners={composerBanners} onDismiss={handleBannerDismiss} onOpenConnections={bannerOpenConnections} />
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
                  onContextActionError={handleContextActionError}
                  onSubmit={handleComposerSubmit}
                  onStop={handleComposerStop}
                  namespaceStyles={namespaceStyles}
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
