/**
 * @kdcube/components-core/session: the session client.
 *
 * Framework-free. Nothing here reads or writes a cookie: the session cookie
 * is HttpOnly and the server owns it. A surface probes `profileUrl`, sends
 * the browser to `loginUrl` with a same-origin `next`, and posts to
 * `logoutUrl`. Sign-in state travels between surfaces as the
 * `kdcube-auth-changed` event (in the page) and the same message relayed
 * into child frames, always after a profile probe, never from token state.
 */

import {
  DEFAULT_CONFIG_URL,
  DEFAULT_LOGOUT_PATH,
  DEFAULT_PROFILE_PATH,
  SESSION_CHANGED_EVENT,
  type PlatformSession,
  type SessionChangedDetail,
  type SessionConfig,
  type SessionEnvironment,
  type SessionFrame,
  type SessionLocation,
  type SessionUser,
  type SignOutResult,
} from './types'

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => text(item)).filter(Boolean) : []
}

function browserLocation(env: SessionEnvironment): SessionLocation | null {
  if (env.location) return env.location
  if (typeof window !== 'undefined' && window.location) return window.location
  return null
}

function browserWindow(env: SessionEnvironment): EventTarget | null {
  if (env.window) return env.window
  if (typeof window !== 'undefined') return window
  return null
}

function doFetch(env: SessionEnvironment): typeof fetch {
  if (env.fetch) return env.fetch
  if (typeof fetch === 'function') return fetch.bind(globalThis)
  throw new Error('no fetch available for the session client')
}

function currentPath(location: SessionLocation | null): string {
  return location ? `${location.pathname}${location.search}${location.hash}` : '/'
}

function resolveUrl(origin: string, value: unknown, fallbackPath: string): string {
  const raw = text(value) || fallbackPath
  if (!raw) return ''
  try {
    return new URL(raw, origin).toString()
  } catch {
    return ''
  }
}

// Control characters (C0 and DEL) never belong in a path.
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/

/**
 * A same-origin absolute path for `next`, or `fallback`. The same rule the
 * platform applies when the login starts: a scheme, a host, `//`, or a
 * backslash never survive, so a sign-in can only return into this origin.
 * An absolute URL on `origin` is reduced to its path.
 */
export function safeNextPath(raw: unknown, fallback = '/', origin = ''): string {
  let value = text(raw)
  if (!value) return fallback
  if (origin && (value.startsWith(`${origin}/`) || value === origin)) {
    value = value.slice(origin.length) || '/'
  }
  if (!value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) return fallback
  if (value.includes('\\') || CONTROL_CHARS.test(value)) return fallback
  return value
}

/** The session contract from the `/api/cp-frontend-config` document. */
export function normalizeSessionConfig(raw: unknown, origin: string): SessionConfig {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const auth = (data.auth && typeof data.auth === 'object' ? data.auth : {}) as Record<string, unknown>
  return {
    authType: text(auth.authType).toLowerCase(),
    sessionLane: text(auth.sessionLane).toLowerCase(),
    loginUrl: resolveUrl(origin, auth.loginUrl, ''),
    profileUrl: resolveUrl(origin, auth.profileUrl, DEFAULT_PROFILE_PATH),
    logoutUrl: resolveUrl(origin, auth.logoutUrl, DEFAULT_LOGOUT_PATH),
    origin,
  }
}

/** Fetch and normalize the frontend config; `null` when it cannot be read. */
export async function loadSessionConfig(env: SessionEnvironment = {}, configUrl = DEFAULT_CONFIG_URL): Promise<SessionConfig | null> {
  const origin = browserLocation(env)?.origin || ''
  try {
    const response = await doFetch(env)(resolveUrl(origin, configUrl, DEFAULT_CONFIG_URL), {
      credentials: 'include',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) return null
    return normalizeSessionConfig(await response.json(), origin)
  } catch {
    return null
  }
}

export function sessionFromProfile(profile: unknown): PlatformSession {
  const data = (profile && typeof profile === 'object' ? profile : null) as Record<string, unknown> | null
  const userType = text(data?.user_type).toLowerCase()
  const userId = text(data?.user_id)
  const username = text(data?.username)
  const authenticated = Boolean(data) && userType !== '' && userType !== 'anonymous' && Boolean(userId || username)
  const user: SessionUser | null = authenticated
    ? {
        userId,
        username,
        email: text(data?.email),
        name: text(data?.name) || username,
        roles: list(data?.roles),
        permissions: list(data?.permissions),
      }
    : null
  return { ready: true, authenticated, userType: userType || 'anonymous', sessionId: text(data?.session_id), user, raw: data }
}

/** Ask the server whether this browser is signed in. Never throws: a failed probe is an anonymous session. */
export async function probeSession(config: SessionConfig, env: SessionEnvironment = {}): Promise<PlatformSession> {
  try {
    const response = await doFetch(env)(config.profileUrl, {
      credentials: 'include',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) return sessionFromProfile(null)
    return sessionFromProfile(await response.json())
  } catch {
    return sessionFromProfile(null)
  }
}

/** The sign-in URL carrying a same-origin `next`; '' when the deployment offers no sign-in. */
export function signInUrl(config: SessionConfig, next?: string, env: SessionEnvironment = {}): string {
  if (!config.loginUrl) return ''
  const url = new URL(config.loginUrl)
  url.searchParams.set('next', safeNextPath(next ?? currentPath(browserLocation(env)), '/', config.origin))
  return url.toString()
}

/** Send the browser to sign in. Returns false when there is nowhere to go. */
export function signIn(config: SessionConfig, next?: string, env: SessionEnvironment = {}): boolean {
  const url = signInUrl(config, next, env)
  const location = browserLocation(env)
  if (!url || !location) return false
  location.assign(url)
  return true
}

/**
 * End the session on the server. With `followUpstream`, the browser then
 * goes to the identity provider's sign-out (which returns to `next`).
 */
export async function signOut(
  config: SessionConfig,
  options: { next?: string; followUpstream?: boolean } = {},
  env: SessionEnvironment = {},
): Promise<SignOutResult> {
  const location = browserLocation(env)
  const url = new URL(config.logoutUrl)
  url.searchParams.set('next', safeNextPath(options.next ?? currentPath(location), '/', config.origin))
  let result: SignOutResult = { ended: false, upstreamLogoutUrl: '', raw: null }
  try {
    const response = await doFetch(env)(url.toString(), {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
    const raw = response.ok ? ((await response.json().catch(() => null)) as Record<string, unknown> | null) : null
    result = { ended: response.ok, upstreamLogoutUrl: text(raw?.upstreamLogoutUrl), raw }
  } catch {
    result = { ended: false, upstreamLogoutUrl: '', raw: null }
  }
  if (options.followUpstream && result.upstreamLogoutUrl && location) {
    location.assign(result.upstreamLogoutUrl)
  }
  return result
}

function defaultChildFrames(): SessionFrame[] {
  if (typeof document === 'undefined') return []
  return Array.from(document.querySelectorAll<HTMLIFrameElement>('iframe[data-kdcube], iframe[data-widget]'))
}

/** Tell this page and its frames that the session changed. */
export function emitSessionChanged(detail: SessionChangedDetail, env: SessionEnvironment = {}): void {
  const target = browserWindow(env)
  if (target && typeof CustomEvent === 'function') {
    target.dispatchEvent(new CustomEvent(SESSION_CHANGED_EVENT, { detail }))
  }
  const location = browserLocation(env)
  const frames = env.childFrames ? env.childFrames() : defaultChildFrames()
  for (const frame of frames) {
    try {
      frame.contentWindow?.postMessage({ type: SESSION_CHANGED_EVENT, auth: detail }, location?.origin || '*')
    } catch {
      // a frame that is gone, or cross-origin without a listener, is not our problem
    }
  }
}

/**
 * Subscribe to session changes: the in-page event, and the same message
 * relayed by a parent frame on this origin. Returns the unsubscribe.
 */
export function onSessionChanged(handler: (detail: SessionChangedDetail) => void, env: SessionEnvironment = {}): () => void {
  const target = browserWindow(env)
  if (!target) return () => undefined
  const location = browserLocation(env)
  const onEvent = (event: Event) => {
    const detail = (event as CustomEvent<SessionChangedDetail>).detail
    handler(detail && typeof detail === 'object' ? detail : { ready: true, authenticated: false, reason: 'unknown' })
  }
  const onMessage = (event: Event) => {
    const message = event as MessageEvent
    const data = message.data as { type?: unknown; auth?: unknown } | null
    if (!data || data.type !== SESSION_CHANGED_EVENT) return
    if (location && message.origin && message.origin !== location.origin) return
    const detail = data.auth && typeof data.auth === 'object' ? (data.auth as SessionChangedDetail) : null
    handler(detail || { ready: true, authenticated: false, reason: 'relayed' })
  }
  target.addEventListener(SESSION_CHANGED_EVENT, onEvent)
  target.addEventListener('message', onMessage)
  return () => {
    target.removeEventListener(SESSION_CHANGED_EVENT, onEvent)
    target.removeEventListener('message', onMessage)
  }
}

export function sessionChangedDetail(session: PlatformSession, reason: string): SessionChangedDetail {
  return { ready: true, authenticated: session.authenticated, reason, user: session.user }
}

export interface SessionClient {
  readonly config: SessionConfig
  probe(): Promise<PlatformSession>
  signInUrl(next?: string): string
  signIn(next?: string): boolean
  signOut(options?: { next?: string; followUpstream?: boolean }): Promise<SignOutResult>
  onChanged(handler: (detail: SessionChangedDetail) => void): () => void
  /** Probe, then announce the result to this page and its frames. */
  refresh(reason?: string): Promise<PlatformSession>
}

/** One object a surface keeps: the contract plus the moves bound to it. */
export function createSessionClient(config: SessionConfig, env: SessionEnvironment = {}): SessionClient {
  return {
    config,
    probe: () => probeSession(config, env),
    signInUrl: (next) => signInUrl(config, next, env),
    signIn: (next) => signIn(config, next, env),
    signOut: (options) => signOut(config, options, env),
    onChanged: (handler) => onSessionChanged(handler, env),
    refresh: async (reason = 'profile') => {
      const session = await probeSession(config, env)
      emitSessionChanged(sessionChangedDetail(session, reason), env)
      return session
    },
  }
}

/** Load the config and build the client in one call; `null` when the config cannot be read. */
export async function connectSession(env: SessionEnvironment = {}, configUrl = DEFAULT_CONFIG_URL): Promise<SessionClient | null> {
  const config = await loadSessionConfig(env, configUrl)
  return config ? createSessionClient(config, env) : null
}
