/**
 * @kdcube/components-core/session: types.
 *
 * The browser contract of a KDCube surface: one server-held session behind an
 * HttpOnly cookie, `profileUrl` the only "am I signed in" probe, `loginUrl`
 * the sign-in redirect, `logoutUrl` the sign-out. A surface never sees a
 * token; it sees these URLs and the profile document.
 */

export interface SessionConfig {
  /** `bundle` (platform or application session), `cognito`, `simple`, `none`, ... */
  authType: string
  /** `platform` when the platform hosts the sign-in itself; else ''. */
  sessionLane: string
  /** Absolute sign-in URL, or '' when this deployment offers none. */
  loginUrl: string
  /** Absolute profile URL. Default `<origin>/profile`. */
  profileUrl: string
  /** Absolute logout URL. Default `<origin>/api/platform/logout`. */
  logoutUrl: string
  /** The origin every URL above resolves against. */
  origin: string
}

export interface SessionUser {
  userId: string
  username: string
  email: string
  name: string
  roles: string[]
  permissions: string[]
}

export interface PlatformSession {
  ready: true
  authenticated: boolean
  /** Lower-case `user_type` from the profile: `registered`, `privileged`, `anonymous`, ... */
  userType: string
  sessionId: string
  user: SessionUser | null
  /** The profile document as answered, for fields this contract does not name. */
  raw: Record<string, unknown> | null
}

export interface SessionChangedDetail {
  ready: boolean
  authenticated: boolean
  reason: string
  user?: SessionUser | null
}

export interface SignOutResult {
  /** The logout request reached the server and answered ok. */
  ended: boolean
  /** The identity provider's sign-out URL, when the platform lane offers one. */
  upstreamLogoutUrl: string
  raw: Record<string, unknown> | null
}

export interface SessionLocation {
  origin: string
  href: string
  pathname: string
  search: string
  hash: string
  assign(url: string): void
}

export interface SessionFrame {
  contentWindow: { postMessage(message: unknown, targetOrigin: string): void } | null
}

/** What a session client needs from its environment; defaults to the browser globals. */
export interface SessionEnvironment {
  fetch?: typeof fetch
  location?: SessionLocation
  /** Event target for `kdcube-auth-changed` and the parent's `message` relay. */
  window?: EventTarget
  /** Frames to relay a session change into. */
  childFrames?: () => SessionFrame[]
}

export const SESSION_CHANGED_EVENT = 'kdcube-auth-changed'
export const DEFAULT_CONFIG_URL = '/api/cp-frontend-config'
export const DEFAULT_PROFILE_PATH = '/profile'
export const DEFAULT_LOGOUT_PATH = '/api/platform/logout'
