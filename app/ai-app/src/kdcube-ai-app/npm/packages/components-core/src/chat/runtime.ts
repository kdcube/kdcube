/**
 * Engine runtime — the config-derived context the transport reads instead of
 * the widget's `settings` singleton.
 *
 * The widget's transport called `settings.getBaseUrl()`, `makeAuthHeaders()`,
 * etc. (a module singleton that blended connection config with iframe handshake
 * and query-param resolution). In the package the transport takes an explicit
 * `EngineRuntime`, built once from the injected `EngineConfig`. Auth is the only
 * behavioural change: it follows the cookie/token model (login stays external).
 */
import type { EngineConfig } from '../shared/index.ts'
import { resolveAuthMode, resolveIdTokenHeader } from '../shared/index.ts'

export interface ResolvedTokens {
  accessToken: string | null
  idToken: string | null
}

export interface EngineRuntime {
  readonly baseUrl: string
  readonly tenant: string
  readonly project: string
  readonly bundleId: string
  /** Header name the id token is sent under (token mode). */
  readonly idTokenHeader: string
  /** Fetch credentials mode — `'include'` so an external session cookie rides along. */
  readonly credentials: RequestCredentials
  /** Resolve auth tokens (token mode → callbacks; cookie mode → nulls). Used by
   *  header builders AND by SSE/Socket.IO, which carry tokens as query/auth payload. */
  getTokens(): Promise<ResolvedTokens>
  /** Build request headers, resolving token-mode auth (async). */
  authHeaders(base?: HeadersInit): Promise<Headers>
  /** Stable local id, e.g. for optimistic turns/streams. */
  createLocalId(prefix: string): string
  /** Caller timezone + UTC offset, sent with chat messages. */
  clientTimezone(): { tz?: string; utcOffsetMin: number }
}

function stripTrailingSlash(url: string): string {
  return url.replace(/\/$/, '')
}

export function createLocalId(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2, 10)
  return `${prefix}_${random}`
}

export function getClientTimezone(): { tz?: string; utcOffsetMin: number } {
  let tz: string | undefined
  try {
    tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    tz = undefined
  }
  return { tz, utcOffsetMin: -new Date().getTimezoneOffset() }
}

export function buildRuntime(config: EngineConfig): EngineRuntime {
  const { baseUrl, tenant, project, bundleId } = config.connection
  const mode = resolveAuthMode(config)
  const idTokenHeader = resolveIdTokenHeader(config)

  async function getTokens(): Promise<ResolvedTokens> {
    if (mode !== 'token') return { accessToken: null, idToken: null }
    const accessToken = (await config.auth?.getAccessToken?.()) ?? null
    const idToken = (await config.auth?.getIdToken?.()) ?? null
    return { accessToken, idToken }
  }

  return {
    baseUrl: stripTrailingSlash(baseUrl),
    tenant,
    project,
    bundleId,
    idTokenHeader,
    credentials: 'include',
    getTokens,
    async authHeaders(base?: HeadersInit): Promise<Headers> {
      const headers = new Headers(base)
      const { accessToken, idToken } = await getTokens()
      if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
      if (idToken) headers.set(idTokenHeader, idToken)
      return headers
    },
    createLocalId,
    clientTimezone: getClientTimezone,
  }
}
