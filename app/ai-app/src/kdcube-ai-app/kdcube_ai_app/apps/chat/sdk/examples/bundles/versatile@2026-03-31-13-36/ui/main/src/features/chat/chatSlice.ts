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
  ChatCompleteEnvelope,
  ChatDeltaEnvelope,
  ChatErrorEnvelope,
  ChatStartEnvelope,
  ChatStepEnvelope,
  ConversationDTO,
  ConversationSummary,
  ConvStatusEnvelope,
  TurnReaction,
} from '../../service.ts'
import {
  initialState,
} from './chatTypes.ts'
import type {
  AdditionalUserMessage,
  AttachedContext,
  ChatState,
  ChatTurn,
  ConnectionState,
} from './chatTypes.ts'
import {
  addBanner,
  applyChatComplete,
  applyChatDelta,
  applyChatError,
  applyChatStart,
  applyChatStep,
  applyConvStatus,
  hydrateHistoricalConversation,
} from './chatReducers.ts'

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
      /* Dedupe by id so dropping the same card twice doesn't stack chips. */
      if (state.composerContexts.some((c) => c.id === action.payload.id)) return
      state.composerContexts = [...state.composerContexts, action.payload]
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
      state.banners = state.banners.filter((banner) => banner.id !== action.payload)
    },
    clearBanners(state) {
      state.banners = []
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
      }
    },

    // --- Turn helpers (user send / new conversation / hydrate) ---
    startNewConversation(state) {
      state.turns = []
      state.conversationId = null
      state.conversationTitle = null
      state.feedback = {}
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
      /* Cleared here; App re-hydrates the map via fetchTurnFeedbacks once
       * the conversation turns are in place. */
      state.feedback = {}
      state.conversationId = conv.conversation_id
      state.conversationTitle =
        (conv as unknown as { conversation_title?: string | null }).conversation_title
        ?? (conv as unknown as { title?: string | null }).title
        ?? null
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
        }
        existingConversationId: string | null
        isContinuation: boolean
        isSteer: boolean
        targetTurnId: string | null
        draftText: string
        draftAttachments: import('./chatTypes.ts').TurnAttachment[]
        sentAt: number
        continuationMessageKind: 'followup' | 'steer'
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
        continuationMessageKind,
      } = action.payload
      const stillOwnsTurn = isContinuation
        ? state.turns.some((turn) => turn.id === targetTurnId)
        : true
      const canBindConversation =
        !state.conversationId ||
        state.conversationId === existingConversationId ||
        state.conversationId === response.conversationId
      if (!stillOwnsTurn || !canBindConversation) return
      state.conversationId = response.conversationId
      const ackStatus = typeof response.status === 'string' ? response.status : null
      const serverTurnId = response.turnId
      const continuationAccepted = ackStatus === 'followup_accepted' || ackStatus === 'steer_accepted'
      const visualContinuationTurnId = response.activeTurnId || targetTurnId
      const continuationMessageId = response.eventId || response.queuedTurnId || serverTurnId

      if (isContinuation) {
        /* When the user sent a followup/steer, the local turn we want
         * to attach the message bubble to is the *active* one
         * (`visualContinuationTurnId`), regardless of whether the
         * server reports a live owner or only queued the continuation.
         * We do NOT speculatively push a new turn for continuations
         * here — that previously caused a blank panel to appear when
         * the server treated the request as a queued followup but the
         * client didn't see the canonical `followup_accepted` status
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
                continuationKind: continuationMessageKind,
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
