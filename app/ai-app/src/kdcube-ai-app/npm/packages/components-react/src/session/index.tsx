/**
 * @kdcube/components-react/session: React bindings over
 * `@kdcube/components-core/session`.
 *
 *   usePlatformSession()  the session as state: probes on mount, re-probes
 *                         on `kdcube-auth-changed`, exposes signIn / signOut.
 *   <SessionGate>         renders its children for a signed-in browser, a
 *                         fallback (or an automatic sign-in) otherwise.
 *
 * Nothing here holds a token. The cookie is HttpOnly; the server decides.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  connectSession,
  type PlatformSession,
  type SessionClient,
  type SessionEnvironment,
  type SignOutResult,
} from '@kdcube/components-core/session'

export interface PlatformSessionState {
  /** The config was loaded and the first probe answered. */
  ready: boolean
  /** No frontend config could be read: the surface has no session contract. */
  unavailable: boolean
  session: PlatformSession | null
  authenticated: boolean
  client: SessionClient | null
  /** Send the browser to sign in, returning to `next` (default: here). False when there is no sign-in. */
  signIn: (next?: string) => boolean
  /** End the session; with `followUpstream`, continue to the identity provider's sign-out. */
  signOut: (options?: { next?: string; followUpstream?: boolean }) => Promise<SignOutResult | null>
  /** Probe again and announce the result. */
  refresh: (reason?: string) => Promise<PlatformSession | null>
}

export interface UsePlatformSessionOptions {
  configUrl?: string
  env?: SessionEnvironment
}

export function usePlatformSession(options: UsePlatformSessionOptions = {}): PlatformSessionState {
  const { configUrl, env } = options
  const [client, setClient] = useState<SessionClient | null>(null)
  const [session, setSession] = useState<PlatformSession | null>(null)
  const [ready, setReady] = useState(false)
  const [unavailable, setUnavailable] = useState(false)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    let off: (() => void) | null = null
    void (async () => {
      const connected = await connectSession(env, configUrl)
      if (!alive.current) return
      if (!connected) {
        setUnavailable(true)
        setReady(true)
        return
      }
      setClient(connected)
      const first = await connected.probe()
      if (!alive.current) return
      setSession(first)
      setReady(true)
      off = connected.onChanged(() => {
        void connected.probe().then((next) => {
          if (alive.current) setSession(next)
        })
      })
    })()
    return () => {
      alive.current = false
      off?.()
    }
  }, [configUrl, env])

  const signIn = useCallback((next?: string) => (client ? client.signIn(next) : false), [client])
  const signOut = useCallback(
    async (opts?: { next?: string; followUpstream?: boolean }) => {
      if (!client) return null
      const result = await client.signOut(opts)
      if (alive.current && !opts?.followUpstream) {
        const after = await client.refresh('logout')
        if (alive.current) setSession(after)
      }
      return result
    },
    [client],
  )
  const refresh = useCallback(
    async (reason?: string) => {
      if (!client) return null
      const next = await client.refresh(reason)
      if (alive.current) setSession(next)
      return next
    },
    [client],
  )

  return useMemo(
    () => ({
      ready,
      unavailable,
      session,
      authenticated: Boolean(session?.authenticated),
      client,
      signIn,
      signOut,
      refresh,
    }),
    [ready, unavailable, session, client, signIn, signOut, refresh],
  )
}

export interface SessionGateProps {
  children: ReactNode
  /** Shown while the first probe is pending. */
  pending?: ReactNode
  /** Shown to a signed-out browser. Receives the state so it can offer a sign-in. */
  fallback?: ReactNode | ((state: PlatformSessionState) => ReactNode)
  /** Send a signed-out browser to the sign-in at once instead of rendering `fallback`. */
  autoSignIn?: boolean
  options?: UsePlatformSessionOptions
}

export function SessionGate({ children, pending = null, fallback = null, autoSignIn = false, options }: SessionGateProps) {
  const state = usePlatformSession(options)
  const bounced = useRef(false)

  useEffect(() => {
    if (!autoSignIn || !state.ready || state.authenticated || bounced.current) return
    bounced.current = true
    state.signIn()
  }, [autoSignIn, state])

  if (!state.ready) return <>{pending}</>
  if (state.authenticated) return <>{children}</>
  return <>{typeof fallback === 'function' ? fallback(state) : fallback}</>
}
