/**
 * @kdcube/components-react (root) — re-exports the shared core contracts so a
 * host can import config/event types from one place. Per-component React
 * bindings live behind subpaths, e.g. `@kdcube/components-react/chat`.
 */
export type {
  EngineConfig,
  EngineConnection,
  EngineAuth,
  AuthMode,
  TransportKind,
  HostEventMap,
  HostEventName,
  ObjectRef,
  ConnectionStatus,
  NoticeTone,
} from '@kdcube/components-core'
