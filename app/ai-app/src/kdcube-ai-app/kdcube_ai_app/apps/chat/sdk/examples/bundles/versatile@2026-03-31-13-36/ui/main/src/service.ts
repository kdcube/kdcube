/**
 * Wave 4 barrel — every type, function, and helper used to live here.
 *
 * The implementation now lives under `./api/`:
 *   - ./api/types.ts        — wire types (BannerTone, envelopes, DTOs, …)
 *   - ./api/transport.ts    — buildRequestHeaders, resolveAbsoluteUrl,
 *                             downloadBlobAsFile, requireScope,
 *                             fetchProfileSessionId
 *   - ./api/sseTransport.ts — openChatStream
 *   - ./api/client.ts       — listBundleConversations, fetchConversationById,
 *                             requestConversationStatus, submitChatMessage,
 *                             downloadResourceByRN, downloadHostedFile
 *
 * This file is kept as a re-export barrel so that all existing
 * `from './service.ts'` imports (App.tsx, features/*, etc.) continue to
 * resolve without churn. New code is encouraged to import from the
 * specific `./api/*` module instead.
 */

export type {
  BannerTone,
  StepStatus,
  ServiceInfo,
  ConversationInfo,
  BaseEnvelope,
  ChatStartEnvelope,
  ChatStepEnvelope,
  ChatDeltaEnvelope,
  ChatCompleteEnvelope,
  ChatErrorEnvelope,
  ConvStatusEnvelope,
  RateLimitPayload,
  ChatServiceEnvelope,
  ConversationSummary,
  ConversationArtifactDTO,
  ConversationTurnDTO,
  ConversationDTO,
  ChatHistoryItem,
  ContinuationKind,
  OpenChatStreamOptions,
  OpenChatStreamResult,
  SubmitChatMessageParams,
  SubmitChatMessageResponse,
} from './api/types.ts'

export { downloadBlobAsFile } from './api/transport.ts'
export { openChatStream } from './api/sseTransport.ts'
export {
  deleteConversationById,
  downloadHostedFile,
  downloadResourceByRN,
  fetchConversationById,
  listBundleConversations,
  requestConversationStatus,
  submitChatMessage,
} from './api/client.ts'
