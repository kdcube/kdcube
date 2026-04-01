import { useState, useEffect } from 'react'

// =============================================================================
// Placeholders are filled at runtime via postMessage from the parent frame
// (sharedConfigProvider.tsx in the chat-web-app).
// =============================================================================

interface AppSettings {
  baseUrl: string
  accessToken: string | null
  idToken: string | null
  idTokenHeader: string
  tenant: string
  project: string
}

const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}'
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}'
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}'
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}'
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}'
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}'

// This bundle always knows its own ID.
const BUNDLE_ID = 'echo.ui@2026-03-30'

class SettingsManager {
  private settings: AppSettings = {
    baseUrl: PLACEHOLDER_BASE_URL,
    accessToken: PLACEHOLDER_ACCESS_TOKEN,
    idToken: PLACEHOLDER_ID_TOKEN,
    idTokenHeader: PLACEHOLDER_ID_TOKEN_HEADER,
    tenant: PLACEHOLDER_TENANT,
    project: PLACEHOLDER_PROJECT,
  }

  private configReceivedCallback: (() => void) | null = null

  getBaseUrl(): string {
    if (this.settings.baseUrl === PLACEHOLDER_BASE_URL) return window.location.origin
    return this.settings.baseUrl
  }

  getAccessToken(): string | null {
    return this.settings.accessToken === PLACEHOLDER_ACCESS_TOKEN ? null : this.settings.accessToken
  }

  getIdToken(): string | null {
    return this.settings.idToken === PLACEHOLDER_ID_TOKEN ? null : this.settings.idToken
  }

  getIdTokenHeader(): string {
    return this.settings.idTokenHeader === PLACEHOLDER_ID_TOKEN_HEADER
      ? 'X-ID-Token'
      : this.settings.idTokenHeader
  }

  getTenant(): string {
    return this.settings.tenant === PLACEHOLDER_TENANT ? '' : this.settings.tenant
  }

  getProject(): string {
    return this.settings.project === PLACEHOLDER_PROJECT ? '' : this.settings.project
  }

  hasPlaceholders(): boolean {
    return this.settings.baseUrl === PLACEHOLDER_BASE_URL
  }

  update(partial: Partial<AppSettings>): void {
    this.settings = { ...this.settings, ...partial }
  }

  onConfigReceived(cb: () => void): void {
    this.configReceivedCallback = cb
  }

  setupParentListener(): Promise<boolean> {
    const identity = 'BUNDLE_ECHO_UI'

    window.addEventListener('message', (event: MessageEvent) => {
      if (event.data?.type !== 'CONN_RESPONSE' && event.data?.type !== 'CONFIG_RESPONSE') return
      if (event.data.identity !== identity) return

      const config = event.data.config
      if (!config) return

      const updates: Partial<AppSettings> = {}
      if (config.baseUrl) updates.baseUrl = config.baseUrl
      if (config.accessToken !== undefined) updates.accessToken = config.accessToken
      if (config.idToken !== undefined) updates.idToken = config.idToken
      if (config.idTokenHeader) updates.idTokenHeader = config.idTokenHeader
      if (config.defaultTenant) updates.tenant = config.defaultTenant
      if (config.defaultProject) updates.project = config.defaultProject

      if (Object.keys(updates).length > 0) {
        this.update(updates)
        this.configReceivedCallback?.()
      }
    })

    if (this.hasPlaceholders()) {
      window.parent.postMessage(
        {
          type: 'CONFIG_REQUEST',
          data: {
            requestedFields: [
              'baseUrl', 'accessToken', 'idToken', 'idTokenHeader',
              'defaultTenant', 'defaultProject',
            ],
            identity,
          },
        },
        '*',
      )

      return new Promise<boolean>((resolve) => {
        const timeout = setTimeout(() => {
          console.warn('[EchoUI] Config request timeout — proceeding with defaults')
          resolve(false)
        }, 3000)
        const prev = this.configReceivedCallback
        this.onConfigReceived(() => {
          clearTimeout(timeout)
          prev?.()
          resolve(true)
        })
      })
    }

    return Promise.resolve(true)
  }
}

const settings = new SettingsManager()

function makeAuthHeaders(base?: HeadersInit): Headers {
  const headers = new Headers(base)
  const accessToken = settings.getAccessToken()
  const idToken = settings.getIdToken()
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  if (idToken) headers.set(settings.getIdTokenHeader(), idToken)
  return headers
}

// =============================================================================
// API
// =============================================================================

async function callEcho(text: string): Promise<string> {
  const url = `${settings.getBaseUrl()}/api/integrations/bundles/${settings.getTenant()}/${settings.getProject()}/${BUNDLE_ID}/operations/echo`
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ data: { text } }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status}: ${detail}`)
  }
  const json = await res.json()
  return json.echo?.text ?? JSON.stringify(json.echo ?? json)
}

// =============================================================================
// App
// =============================================================================

export default function App() {
  const [ready, setReady] = useState(false)
  const [input, setInput] = useState('')
  const [echo, setEcho] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    settings.setupParentListener().then(() => setReady(true))
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!input.trim()) return
    setLoading(true)
    setError(null)
    try {
      setEcho(await callEcho(input.trim()))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  if (!ready) {
    return (
      <div className="max-w-xl mx-auto mt-20 px-4 font-sans text-gray-500">
        Loading…
      </div>
    )
  }

  return (
    <div className="max-w-xl mx-auto mt-20 px-4 font-sans">
      <h2 className="text-xl font-semibold mb-4 text-gray-800">Echo UI</h2>
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Type something…"
          disabled={loading}
          className="flex-1 px-3 py-2 text-base rounded border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={loading}
          className="px-4 py-2 text-base rounded bg-gray-900 text-white hover:bg-gray-700 disabled:opacity-50 cursor-pointer"
        >
          {loading ? '…' : 'Send'}
        </button>
      </form>
      {error !== null && (
        <p className="mt-6 px-4 py-3 bg-red-50 border border-red-200 text-red-700 rounded">
          {error}
        </p>
      )}
      {echo !== null && (
        <p className="mt-6 px-4 py-3 bg-gray-100 text-gray-800 rounded">
          {echo}
        </p>
      )}
    </div>
  )
}
