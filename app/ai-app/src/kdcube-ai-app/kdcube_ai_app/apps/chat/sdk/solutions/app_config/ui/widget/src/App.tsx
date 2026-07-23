/**
 * The app-config viewer as a served widget. It resolves runtime config + auth
 * via the standard handshake (`settings`), builds an `AppsConfigTransport` from
 * it, and mounts the reusable <AppConfigPanel/> from
 * `@kdcube/components-react/apps-config`. On a host `kdcube-auth-changed`
 * broadcast it re-probes config and remounts so the view reloads with fresh auth.
 */
import { useEffect, useState } from 'react'
import { AppsConfigProvider, AppConfigPanel } from '@kdcube/components-react/apps-config'
import type { AppScope, AppsConfigTransport } from '@kdcube/components-core/apps-config'
import { settings } from './settings.ts'

const transport: AppsConfigTransport = {
  baseUrl: () => settings.getBaseUrl(),
  authHeaders: (extra) => settings.authHeaders(extra),
}

export default function App() {
  const [ready, setReady] = useState(false)
  const [authNonce, setAuthNonce] = useState(0)

  useEffect(() => {
    let alive = true
    void settings.setupParentListener().then(() => {
      if (alive) setReady(true)
    })

    const onAuthChanged = () => {
      void settings.requestConfig().then(() => {
        if (alive) setAuthNonce((n) => n + 1)
      })
    }
    window.addEventListener('kdcube-auth-changed', onAuthChanged as EventListener)
    return () => {
      alive = false
      window.removeEventListener('kdcube-auth-changed', onAuthChanged as EventListener)
    }
  }, [])

  if (!ready) {
    return <div className="ac-boot">Loading…</div>
  }

  const scope: AppScope = { tenant: settings.getTenant(), project: settings.getProject() }

  return (
    <AppsConfigProvider key={authNonce} scope={scope} transport={transport}>
      <AppConfigPanel title="App Config" />
    </AppsConfigProvider>
  )
}
