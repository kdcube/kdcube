/**
 * Engine configuration — explicit, injected, host-agnostic.
 *
 * IMPORTANT: this is deliberately NOT the widget's `settings.ts`. That module
 * blends connection config with iframe parent-handshake and query-param
 * resolution — both are HOST concerns, not core concerns. The core takes only
 * what it needs to talk to the server; resolving where those values come from
 * (route, query, parent frame, a website's own config) is the host adapter's
 * job, done before `createChatEngine(config)`.
 */

export interface EngineConnection {
  /** Server origin, no trailing slash (e.g. https://api.example.com). */
  baseUrl: string
  tenant: string
  project: string
  /** The bundle whose operations/streams this engine talks to. */
  bundleId: string
}

/**
 * Auth model: login lives OUTSIDE the components. The host authenticates
 * elsewhere; the engine only carries credentials and bubbles `unauthorized`
 * when the server rejects a call.
 *
 *  - `cookie` (default): requests use `credentials: 'include'`; the host's
 *    session cookie (set by an external login) rides along. Nothing to supply.
 *  - `token`: the host provides bearer/id tokens via callbacks (re-read per
 *    request, so refresh is transparent).
 */
export type AuthMode = 'cookie' | 'token'

export interface EngineAuth {
  mode?: AuthMode
  /** token mode: bearer access token (Authorization: Bearer …). */
  getAccessToken?: () => string | null | Promise<string | null>
  /** token mode: id token sent under `idTokenHeader`. */
  getIdToken?: () => string | null | Promise<string | null>
  /** Header name for the id token. Default 'X-ID-Token'. */
  idTokenHeader?: string
}

/** Which wire transport the engine uses for streaming responses. */
export type TransportKind = 'auto' | 'socket' | 'sse'

export interface EngineConfig {
  connection: EngineConnection
  auth?: EngineAuth
  /** Default 'auto' (prefer Socket.IO, fall back to SSE). */
  transport?: TransportKind
}

export function resolveAuthMode(config: EngineConfig): AuthMode {
  return config.auth?.mode ?? 'cookie'
}

export function resolveIdTokenHeader(config: EngineConfig): string {
  return config.auth?.idTokenHeader ?? 'X-ID-Token'
}
