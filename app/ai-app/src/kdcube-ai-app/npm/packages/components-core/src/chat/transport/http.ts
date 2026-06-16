/**
 * Low-level HTTP helpers for the chat transport.
 *
 * Ported from the widget's `api/transport.ts`, with the `settings` singleton
 * replaced by an explicit `EngineRuntime` (passed in by the controller) and the
 * iframe/standalone-dev shortcuts dropped — those are host concerns, not the
 * engine's. Auth header building is async (token-mode callbacks).
 */
import type { EngineRuntime } from '../runtime.ts'

export async function buildRequestHeaders(runtime: EngineRuntime, base?: HeadersInit): Promise<Headers> {
  const headers = await runtime.authHeaders(base)
  const tz = runtime.clientTimezone()
  if (tz.tz) headers.set('X-User-Timezone', tz.tz)
  headers.set('X-User-UTC-Offset', String(tz.utcOffsetMin))
  return headers
}

export function resolveAbsoluteUrl(runtime: EngineRuntime, path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  return `${runtime.baseUrl}${path.startsWith('/') ? path : `/${path}`}`
}

export function downloadBlobAsFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function requireScope(runtime: EngineRuntime): { tenant: string; project: string } {
  const { tenant, project } = runtime
  if (!tenant || !project) {
    throw new Error('Tenant/project is not configured for this chat engine.')
  }
  return { tenant, project }
}

/** Canonical URL the platform serves a bundle widget at:
 *  `GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{alias}`. */
export function bundleWidgetUrl(runtime: EngineRuntime, alias: string): string {
  const { tenant, project } = requireScope(runtime)
  return `${runtime.baseUrl}/api/integrations/bundles/${encodeURIComponent(tenant)}/${encodeURIComponent(project)}/${encodeURIComponent(runtime.bundleId)}/widgets/${encodeURIComponent(alias)}`
}

export async function fetchProfileSessionId(runtime: EngineRuntime, sessionId?: string | null): Promise<string> {
  if (sessionId) return sessionId
  return (await fetchProfile(runtime)).sessionId
}

export interface ProfileInfo {
  sessionId: string
  /** Server-authoritative identity. `'anonymous'` for unauthenticated visitors;
   *  an authenticated user type otherwise. May be null if the server omits it. */
  userType: string | null
  roles: string[]
}

/** Fetch the server profile. The server is the source of truth for whether the
 *  caller is anonymous — a host may render chat for everyone, but only an
 *  authenticated profile may open the stream and send. */
export async function fetchProfile(runtime: EngineRuntime): Promise<ProfileInfo> {
  const response = await fetch(`${runtime.baseUrl}/profile`, {
    method: 'GET',
    credentials: runtime.credentials,
    headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json' }),
  })
  if (!response.ok) {
    // A non-OK /profile (commonly a 429 throttle for an anonymous visitor) must
    // not surface as an error. The server still reports who the caller is even on
    // a throttle — `user_type` / `session_id`, often nested under `detail`. Treat
    // any failure as a best-effort anonymous profile.
    const body = (await response.json().catch(() => null)) as Record<string, unknown> | null
    const nested = body && typeof body.detail === 'object' && body.detail
      ? (body.detail as Record<string, unknown>)
      : body
    const userType = String((nested && nested.user_type) || 'anonymous').toLowerCase() || 'anonymous'
    const sessionId = String((nested && nested.session_id) || '')
    return { sessionId, userType, roles: [] }
  }

  const data = (await response.json()) as {
    session_id?: string | null
    user_type?: string | null
    roles?: unknown
  }
  if (!data.session_id) {
    throw new Error('Profile did not include a session id.')
  }
  return {
    sessionId: data.session_id,
    userType: data.user_type ?? null,
    roles: Array.isArray(data.roles) ? data.roles.filter((r): r is string => typeof r === 'string') : [],
  }
}
