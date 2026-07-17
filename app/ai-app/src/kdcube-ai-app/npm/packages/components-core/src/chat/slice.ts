/**
 * Wave 3 chatSlice — single Redux Toolkit slice that owns the entire chat
 * surface state (turns, banners, composer, conversations list, connection,
 * lock state, conversation title/id).
 *
 * Strategy: each state-machine event (`chat.start`, `chat.delta`,
 * `chat.step`, `chat.complete`, `chat.error`, `conv.status`) is a thin
 * reducer that delegates to the original `apply*` pure function in
 * `./chatReducers.ts`. Immer accepts a returned-from-reducer state as the
 * new state, so the legacy `ChatState → ChatState` shape is preserved
 * end-to-end.
 *
 * Component-facing actions (composer text, banner dismiss, conversation
 * list, etc.) are written as small Immer mutations on the draft.
 */

import { createSlice } from '@reduxjs/toolkit'
import type { PayloadAction } from '@reduxjs/toolkit'
import type {
  BannerTone,
  BaseEnvelope,
  ChatCompleteEnvelope,
  ChatDeltaEnvelope,
  ChatErrorEnvelope,
  ChatStartEnvelope,
  ChatStepEnvelope,
  ConversationDTO,
  ConversationSummary,
  ConvStatusEnvelope,
  TurnReaction,
} from './protocol.ts'
import {
  initialState,
} from './state.ts'
import type {
  AdditionalUserMessage,
  AttachedContext,
  ChatState,
  ChatTurn,
  ConnectionState,
  SubagentThread,
} from './state.ts'
import {
  addBanner,
  applyChatComplete,
  applyChatDelta,
  applyChatError,
  applyChatStart,
  applyChatStep,
  applyConvStatus,
  hydrateHistoricalConversation,
} from './reducers.ts'
import {
  applySubagentEnvelope,
  hydrateSubagentThread,
  subagentThreadsFromConversation,
} from './subagents.ts'
import type { SubagentStreamKind } from './subagents.ts'
import { applySelectionPatch, initialCapabilitiesState } from './capabilities.ts'
import type {
  AgentCachePolicy,
  AgentCapabilitiesInventory,
  AgentModelPick,
  AgentSelectionDisabled,
  AgentSelectionPatch,
  AgentSelectionPending,
} from './capabilities.ts'

function contextStringData(context: AttachedContext, key: string): string {
  const value = context.data?.[key]
  return typeof value === 'string' ? value.trim() : ''
}

function contextRef(context: AttachedContext): string {
  return context.logicalPath || context.ref || ''
}

function contextStoryKey(context: AttachedContext): string {
  const storyId = contextStringData(context, 'story_id') || contextStringData(context, 'storyId')
  if (storyId) return storyId
  return ''
}

function isWizardContext(context: AttachedContext): boolean {
  return context.kind === 'wizard' || context.kind === 'wizard.snapshot'
}

function hasStoryContext(context: AttachedContext): boolean {
  return Boolean(contextStoryKey(context)) || context.kind === 'story' || context.kind === 'story.ref'
}

function contextMergeKey(context: AttachedContext): string {
  if (isWizardContext(context)) return 'surface:wizard'
  if (context.kind === 'canvas') {
    return `surface:canvas:${context.canvasId || context.canvasName || context.id}`
  }
  if (hasStoryContext(context)) {
    const storyKey = contextStoryKey(context)
    if (storyKey) return `story:${storyKey}`
  }
  const ref = contextRef(context)
  return ref ? `ref:${ref}` : `id:${context.id}`
}

function mergeComposerContexts(
  current: ReadonlyArray<AttachedContext>,
  incoming: ReadonlyArray<AttachedContext>,
): AttachedContext[] {
  let out = current.slice()
  for (const context of incoming) {
    const key = contextMergeKey(context)
    const storyKey = contextStoryKey(context)

    if (isWizardContext(context)) {
      out = out.filter((existing) => {
        if (isWizardContext(existing)) return false
        return !(storyKey && hasStoryContext(existing) && contextStoryKey(existing) === storyKey)
      })
    } else if (hasStoryContext(context) && storyKey) {
      const alreadyCoveredByWizard = out.some((existing) => (
        isWizardContext(existing) && contextStoryKey(existing) === storyKey
      ))
      if (alreadyCoveredByWizard) continue
    }

    const index = out.findIndex((existing) => contextMergeKey(existing) === key)
    if (index >= 0) out[index] = context
    else out.push(context)
  }
  return out
}

const slice = createSlice({
  name: 'chat',
  initialState,
  reducers: {
    // --- State machine event reducers (delegate to pure apply*) ---
    chatStarted(state, action: PayloadAction<ChatStartEnvelope>) {
      return applyChatStart(state as ChatState, action.payload)
    },
    chatDelta(state, action: PayloadAction<ChatDeltaEnvelope>) {
      return applyChatDelta(state as ChatState, action.payload)
    },
    chatStep(state, action: PayloadAction<ChatStepEnvelope>) {
      return applyChatStep(state as ChatState, action.payload)
    },
    chatCompleted(state, action: PayloadAction<ChatCompleteEnvelope>) {
      return applyChatComplete(state as ChatState, action.payload)
    },
    chatErrored(state, action: PayloadAction<ChatErrorEnvelope>) {
      return applyChatError(state as ChatState, action.payload)
    },
    convStatusUpdated(state, action: PayloadAction<ConvStatusEnvelope>) {
      return applyConvStatus(state as ChatState, action.payload)
    },

    // --- Subagent threads (stamped child-conversation traffic) ---
    /** One stamped envelope, routed by the engine instead of the main-lane
     *  reducers: ensures the thread and runs the child's stream through the
     *  same apply* pipeline over the thread's own turn list. */
    subagentStreamEvent(
      state,
      action: PayloadAction<{ kind: SubagentStreamKind; envelope: BaseEnvelope }>,
    ) {
      return applySubagentEnvelope(state as ChatState, action.payload.kind, action.payload.envelope)
    },
    subagentThreadLoading(state, action: PayloadAction<string>) {
      const thread = state.threads[action.payload]
      if (!thread) return
      thread.hydration = 'loading'
      thread.hydrationError = null
    },
    /** A fetched child conversation folds into its thread (reload expand). */
    subagentThreadHydrated(
      state,
      action: PayloadAction<{ childConversationId: string; conversation: ConversationDTO }>,
    ) {
      const thread = state.threads[action.payload.childConversationId]
      if (!thread) return
      state.threads[action.payload.childConversationId] = hydrateSubagentThread(
        thread as SubagentThread,
        action.payload.conversation,
      )
    },
    subagentThreadLoadError(
      state,
      action: PayloadAction<{ childConversationId: string; error: string }>,
    ) {
      const thread = state.threads[action.payload.childConversationId]
      if (!thread) return
      thread.hydration = 'error'
      thread.hydrationError = action.payload.error
    },

    // --- Connection lifecycle ---
    setConnectionState(state, action: PayloadAction<ConnectionState>) {
      state.connection = action.payload
    },
    setSessionId(state, action: PayloadAction<string | null>) {
      state.sessionId = action.payload
    },

    // --- Conversation pointer + title ---
    setConversationId(state, action: PayloadAction<string | null>) {
      state.conversationId = action.payload
    },
    setConversationTitle(state, action: PayloadAction<string | null>) {
      state.conversationTitle = action.payload
    },

    // --- Composer ---
    setComposerText(state, action: PayloadAction<string>) {
      state.composerText = action.payload
    },
    setComposerFiles(state, action: PayloadAction<File[]>) {
      state.composerFiles = action.payload
    },
    addComposerFiles(state, action: PayloadAction<File[]>) {
      state.composerFiles = [...state.composerFiles, ...action.payload]
    },
    removeComposerFile(state, action: PayloadAction<number>) {
      state.composerFiles = state.composerFiles.filter((_, idx) => idx !== action.payload)
    },
    addComposerContext(state, action: PayloadAction<AttachedContext>) {
      state.composerContexts = mergeComposerContexts(state.composerContexts, [action.payload])
    },
    removeComposerContext(state, action: PayloadAction<string>) {
      state.composerContexts = state.composerContexts.filter((c) => c.id !== action.payload)
    },
    clearComposer(state) {
      state.composerText = ''
      state.composerFiles = []
      state.composerContexts = []
    },

    // --- Banners ---
    pushBanner(state, action: PayloadAction<{ tone: BannerTone; text: string; placement?: 'top' | 'composer' }>) {
      return addBanner(state as ChatState, action.payload.tone, action.payload.text, action.payload.placement ?? 'top')
    },
    dismissBanner(state, action: PayloadAction<string>) {
      // A dismissed consent banner stays dismissed for the conversation: its
      // signature is remembered, and the identical consent state stays quiet.
      // A CHANGED claims set carries a new signature and shows again.
      const target = state.banners.find((banner) => banner.id === action.payload)
      if (target?.consentSignature && !state.dismissedConsentSignatures.includes(target.consentSignature)) {
        state.dismissedConsentSignatures.push(target.consentSignature)
      }
      state.banners = state.banners.filter((banner) => banner.id !== action.payload)
    },
    clearBanners(state) {
      state.banners = []
    },

    // --- Composer-menu tool spotlight (consent banner's "turn off tools") ---
    spotlightTools(state, action: PayloadAction<string[]>) {
      const tools = action.payload.map((item) => String(item || '').trim()).filter(Boolean)
      state.toolSpotlight = tools.length ? { tools, nonce: Date.now() } : null
    },
    clearToolSpotlight(state) {
      state.toolSpotlight = null
    },

    // --- Host-driven turn jump ("bring me here" from an undocked window) ---
    requestTurnJump(
      state,
      action: PayloadAction<{ conversationId: string; turnId: string; role?: string | null }>,
    ) {
      const conversationId = String(action.payload.conversationId || '').trim()
      const turnId = String(action.payload.turnId || '').trim()
      if (!conversationId || !turnId) return
      const role = String(action.payload.role || '').trim() || null
      state.turnJump = { conversationId, turnId, role, nonce: Date.now() }
    },
    clearTurnJump(state) {
      state.turnJump = null
    },

    // --- Turn feedback (signed-in user's reaction per assistant turn) ---
    setTurnFeedback(state, action: PayloadAction<{ turnId: string; reaction: TurnReaction | null }>) {
      const { turnId, reaction } = action.payload
      if (reaction) {
        state.feedback[turnId] = reaction
      } else {
        delete state.feedback[turnId]
      }
    },
    setFeedbackMap(state, action: PayloadAction<Record<string, TurnReaction>>) {
      state.feedback = action.payload
    },

    // --- Input lock ---
    lockInput(state, action: PayloadAction<string | null>) {
      state.inputLocked = true
      state.inputLockMessage = action.payload
    },
    unlockInput(state) {
      state.inputLocked = false
      state.inputLockMessage = null
    },

    // --- Conversations list ---
    setConversations(state, action: PayloadAction<ConversationSummary[]>) {
      state.conversations = action.payload
      /* The list is the source of truth for titles. Sync the active
       * conversation's (server-generated) title into the header so a new
       * chat stops showing "Untitled" once the backend names it. */
      if (state.conversationId) {
        const active = action.payload.find((c) => c.id === state.conversationId)
        if (active?.title) state.conversationTitle = active.title
      }
    },
    setConversationsLoading(state, action: PayloadAction<boolean>) {
      state.conversationsLoading = action.payload
    },
    setConversationsError(state, action: PayloadAction<string | null>) {
      state.conversationsError = action.payload
    },
    setConversationLoadingId(state, action: PayloadAction<string | null>) {
      state.conversationLoadingId = action.payload
    },
    setConversationDeletingId(state, action: PayloadAction<string | null>) {
      state.conversationDeletingId = action.payload
    },
    removeConversation(state, action: PayloadAction<string>) {
      state.conversations = state.conversations.filter((c) => c.id !== action.payload)
      /* If the deleted conversation was the open one, clear the active
       * pointer so the right pane resets to "New chat". The caller is
       * responsible for any further composer/lock reset. */
      if (state.conversationId === action.payload) {
        state.conversationId = null
        state.conversationTitle = null
        state.turns = []
        state.threads = {}
      }
    },

    // --- Turn helpers (user send / new conversation / hydrate) ---
    startNewConversation(state) {
      state.turns = []
      state.conversationId = null
      state.conversationTitle = null
      state.feedback = {}
      state.dismissedConsentSignatures = []
      state.toolSpotlight = null
      state.threads = {}
    },
    appendTurn(state, action: PayloadAction<ChatTurn>) {
      state.turns = [...state.turns, action.payload]
    },
    appendFollowupMessage(
      state,
      action: PayloadAction<{ turnId: string; message: AdditionalUserMessage }>,
    ) {
      const idx = state.turns.findIndex((turn) => turn.id === action.payload.turnId)
      if (idx < 0) return
      const next = { ...state.turns[idx] }
      next.additionalUserMessages = [...next.additionalUserMessages, action.payload.message]
      state.turns[idx] = next
    },
    hydrateConversation(
      state,
      action: PayloadAction<{ conversation: ConversationDTO }>,
    ) {
      const conv = action.payload.conversation
      state.turns = hydrateHistoricalConversation(conv)
      /* Reload half of the thread contract: parent turns' `forks` descriptors
       * become collapsed thread stubs; expanding fetches the child. */
      state.threads = subagentThreadsFromConversation(conv)
      /* Cleared here; App re-hydrates the map via fetchTurnFeedbacks once
       * the conversation turns are in place. */
      state.feedback = {}
      state.dismissedConsentSignatures = []
      state.toolSpotlight = null
      state.conversationId = conv.conversation_id
      state.conversationTitle =
        (conv as unknown as { conversation_title?: string | null }).conversation_title
        ?? (conv as unknown as { title?: string | null }).title
        ?? null
    },

    // --- Per-user agent capabilities (composer "+" menu) ---
    capabilitiesLoading(state) {
      state.capabilities.status = 'loading'
      state.capabilities.error = null
    },
    capabilitiesLoaded(
      state,
      action: PayloadAction<{
        agent: string
        inventory: AgentCapabilitiesInventory
        disabled: AgentSelectionDisabled
        model?: AgentModelPick | null
        instructions?: string | null
        cachePolicy?: AgentCachePolicy | null
        pending?: AgentSelectionPending | null
      }>,
    ) {
      state.capabilities.status = 'ready'
      state.capabilities.error = null
      state.capabilities.agent = action.payload.agent
      state.capabilities.inventory = action.payload.inventory
      state.capabilities.disabled = action.payload.disabled
      state.capabilities.model = action.payload.model ?? null
      state.capabilities.instructions = action.payload.instructions ?? null
      state.capabilities.cachePolicy = action.payload.cachePolicy ?? null
      state.capabilities.pending = action.payload.pending ?? null
      state.capabilities.dirty = false
    },
    capabilitiesLoadError(state, action: PayloadAction<string>) {
      state.capabilities.status = 'error'
      state.capabilities.error = action.payload
    },
    capabilitiesReset(state) {
      state.capabilities = { ...initialCapabilitiesState, disabled: {} }
    },
    /** Apply a toggle patch to the local deny-list/model draft; the explicit
     *  Save changes command sends the matching merge-write. */
    capabilitiesPatchApplied(state, action: PayloadAction<AgentSelectionPatch>) {
      state.capabilities.disabled = applySelectionPatch(state.capabilities.disabled, action.payload)
      if (action.payload.model !== undefined) {
        state.capabilities.model = action.payload.model
      }
      if (action.payload.instructions !== undefined) {
        state.capabilities.instructions = action.payload.instructions
      }
      state.capabilities.dirty = true
      state.capabilities.saveError = null
    },
    capabilitiesSaving(state, action: PayloadAction<boolean>) {
      state.capabilities.saving = action.payload
    },
    /** Reconcile with the server's clamped record after a save round-trips. */
    capabilitiesSelectionSaved(
      state,
      action: PayloadAction<{
        disabled: AgentSelectionDisabled
        model?: AgentModelPick | null
        instructions?: string | null
        pending?: AgentSelectionPending | null
      }>,
    ) {
      state.capabilities.disabled = action.payload.disabled
      state.capabilities.model = action.payload.model ?? null
      state.capabilities.instructions = action.payload.instructions ?? null
      if (action.payload.pending !== undefined) {
        state.capabilities.pending = action.payload.pending
      }
      state.capabilities.dirty = false
      state.capabilities.saving = false
      state.capabilities.saveError = null
    },
    capabilitiesSaveError(state, action: PayloadAction<string>) {
      state.capabilities.saving = false
      state.capabilities.saveError = action.payload
    },

    // --- Reset (used on disconnect / hard-reset) ---
    resetState() {
      return initialState
    },

    /**
     * submitAck — apply the result of `submitChatMessage` to the local
     * turn list.
     *
     *   - If `stillOwnsTurn` or `canBindConversation` does not hold,
     *     bail (Immer treats this as no-op).
     *   - Bind `conversationId` from the ack.
     *   - For accepted live followup/steer continuations, append a new
     *     `additionalUserMessages` entry to the active turn.
     *   - For continuations the server rejected (which started a new
     *     turn), create the new turn entry.
     *   - For non-continuation sends, either update an existing turn
     *     entry or create a fresh one for the server-issued turn id.
     */
    submitAck(
      state,
      action: PayloadAction<{
        response: {
          conversationId: string | null
          turnId: string
          status: string | null
          eventId: string | null
          queuedTurnId: string | null
          activeTurnId: string | null
          liveOwnerDetected: boolean | null | undefined
          isContinuation: boolean | null | undefined
        }
        existingConversationId: string | null
        isContinuation: boolean
        isSteer: boolean
        targetTurnId: string | null
        draftText: string
        draftAttachments: import('./state.ts').TurnAttachment[]
        sentAt: number
        additionalEventType: string
      }>,
    ) {
      const {
        response,
        existingConversationId,
        isContinuation,
        isSteer,
        targetTurnId,
        draftText,
        draftAttachments,
        sentAt,
        additionalEventType,
      } = action.payload
      const effectiveContinuation = typeof response.isContinuation === 'boolean'
        ? response.isContinuation
        : isContinuation
      const serverTurnId = response.turnId
      const visualContinuationTurnId = response.activeTurnId || targetTurnId
      const stillOwnsTurn = effectiveContinuation
        ? state.turns.some((turn) => turn.id === visualContinuationTurnId)
        : true
      const canBindConversation =
        !state.conversationId ||
        state.conversationId === existingConversationId ||
        state.conversationId === response.conversationId
      if (!stillOwnsTurn || !canBindConversation) return
      state.conversationId = response.conversationId
      const continuationAccepted = effectiveContinuation
      const continuationMessageId = response.eventId || response.queuedTurnId || serverTurnId

      if (effectiveContinuation) {
        /* When the user sent a followup/steer, the local turn we want
         * to attach the message bubble to is the *active* one
         * (`visualContinuationTurnId`), regardless of whether the
         * server reports a live owner or only queued the continuation.
         * We do NOT speculatively push a new turn for continuations
         * here — that previously caused a blank panel to appear when
         * the server treated the request as a queued followup but the
         * client didn't receive the continuation marker
         * (race or transport variance). If the server actually did
         * start a brand-new turn (e.g. because the conversation state
         * was idle by the time the POST arrived), the subsequent
         * `chat.start` event will create that turn via
         * `applyChatStart`'s `ensureTurn`, with the user's text from
         * the start envelope's `data.message`. */
        if (
          !isSteer &&
          visualContinuationTurnId &&
          (continuationAccepted || response.liveOwnerDetected === false)
        ) {
          const target = state.turns.find((turn) => turn.id === visualContinuationTurnId)
          if (target) {
            const messageId = `continuation:${continuationMessageId}`
            if (!target.additionalUserMessages.some((message) => message.id === messageId)) {
              target.additionalUserMessages.push({
                id: messageId,
                text: draftText,
                timestamp: sentAt,
                attachments: draftAttachments,
                eventType: additionalEventType,
              })
            }
          }
        }
        return
      }

      /* Non-continuation send: speculatively create or update the
       * pending turn entry so the user's bubble appears immediately,
       * before the first `chat.start` envelope arrives. */
      const existingIndex = state.turns.findIndex((turn) => turn.id === serverTurnId)
      if (existingIndex >= 0) {
        const existing = state.turns[existingIndex]
        /* Prefer draftText over whatever chat.start may have seeded the turn
         * with. The server's chat.start envelope used to carry a 100-char
         * preview as data.message; if the SSE event won the race, the
         * existing.userMessage was that truncated preview. Even after the
         * server fix this client-side preference is the safer default: the
         * client is authoritative for what was actually sent. */
        state.turns[existingIndex] = {
          ...existing,
          state: existing.state === 'idle' as never ? 'pending' : existing.state,
          userMessage: draftText || existing.userMessage,
          userAttachments: existing.userAttachments.length
            ? existing.userAttachments
            : draftAttachments,
        }
      } else {
        state.turns.push({
          id: serverTurnId,
          state: 'pending',
          createdAt: sentAt,
          userMessage: draftText,
          userAttachments: draftAttachments,
          additionalUserMessages: [],
          answer: '',
          error: null,
          steps: {},
          artifacts: [],
          timeline: [],
          followups: [],
        })
      }
    },
  },
})

export const chatSlice = slice
export const chatActions = slice.actions
export const chatReducer = slice.reducer
