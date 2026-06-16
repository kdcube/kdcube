/**
 * @kdcube/components-core (root) — cross-component primitives shared by every
 * engine: the host event bus and the engine config/auth contract.
 *
 * Per-component engines live behind subpaths, e.g. `@kdcube/components-core/chat`.
 */
export type {
  ObjectRef,
  ConnectionStatus,
  NoticeTone,
  HostEventMap,
  HostEventName,
  HostEventHandler,
  HostEventEmitter,
} from './events.ts'
export { createHostEventEmitter } from './events.ts'

export type {
  EngineConnection,
  AuthMode,
  EngineAuth,
  TransportKind,
  EngineConfig,
} from './config.ts'
export { resolveAuthMode, resolveIdTokenHeader } from './config.ts'

export type { ContextItem, ContextDragEnvelope } from './contextPin.ts'
export {
  CONTEXT_DRAG_MIME,
  CONTEXT_ATTACH_TYPE,
  buildContextDrag,
  parseContextDrop,
} from './contextPin.ts'
