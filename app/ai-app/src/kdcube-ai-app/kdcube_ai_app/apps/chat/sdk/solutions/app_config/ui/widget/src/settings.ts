/**
 * Runtime config + auth resolver for the app-config widget — the standard
 * served-widget handshake (same contract as the conversation-search widget):
 * build-time `{{...}}` placeholders, `CONFIG_REQUEST`/`CONFIG_RESPONSE` parent
 * handshake when embedded, `/api/cp-frontend-config` fallback for standalone
 * direct-load. Route context supplies tenant/project when served from a bundle
 * URL. `requestConfig()` re-probes on a host `kdcube-auth-changed` broadcast.
 */

const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}'
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}'
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}'
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}'
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}'
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}'

type RuntimeConfigPayload = {
  baseUrl?: string
  accessToken?: string | null
  idToken?: string | null
  idTokenHeader?: string
  idTokenHeaderName?: string
  auth?: { idTokenHeaderName?: string }
  defaultTenant?: string
  defaultProject?: string
  tenant?: string
  tenant_id?: string
  project?: string
  project_id?: string
}

function isPlaceholder(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.includes('{{') && value.includes('}}')
}

function routeContext() {
  const markers = ['/api/integrations/bundles/', '/api/integrations/static/']
  const path = window.location.pathname
  const marker = markers.find((candidate) => path.includes(candidate))
  const query = new URLSearchParams(window.location.search)
  if (!marker) {
    return { tenant: query.get('tenant') || '', project: query.get('project') || '' }
  }
  const parts = path
    .slice(path.indexOf(marker) + marker.length)
    .split('/')
    .map((part) => decodeURIComponent(part))
  return {
    tenant: parts[0] || query.get('tenant') || '',
    project: parts[1] || query.get('project') || '',
  }
}

const context = routeContext()

class Settings {
  private values = {
    baseUrl: PLACEHOLDER_BASE_URL,
    accessToken: PLACEHOLDER_ACCESS_TOKEN as string | null,
    idToken: PLACEHOLDER_ID_TOKEN as string | null,
    idTokenHeader: PLACEHOLDER_ID_TOKEN_HEADER,
    tenant: PLACEHOLDER_TENANT,
    project: PLACEHOLDER_PROJECT,
  }

  getBaseUrl(): string {
    if (isPlaceholder(this.values.baseUrl)) return window.location.origin
    const trimmed = this.values.baseUrl.replace(/\/+$/, '')
    return trimmed.endsWith('/api') ? trimmed.slice(0, -4) : trimmed
  }

  getTenant(): string {
    return isPlaceholder(this.values.tenant) ? context.tenant : this.values.tenant
  }

  getProject(): string {
    return isPlaceholder(this.values.project) ? context.project : this.values.project
  }

  authHeaders(base?: Record<string, string>): Record<string, string> {
    const headers: Record<string, string> = { ...(base || {}) }
    if (this.values.accessToken && !isPlaceholder(this.values.accessToken)) {
      headers.Authorization = `Bearer ${this.values.accessToken}`
    }
    if (this.values.idToken && !isPlaceholder(this.values.idToken)) {
      const name = isPlaceholder(this.values.idTokenHeader) ? 'X-ID-Token' : this.values.idTokenHeader
      headers[name] = this.values.idToken
    }
    return headers
  }

  private needsRuntimeConfig(): boolean {
    return (
      isPlaceholder(this.values.baseUrl) ||
      isPlaceholder(this.values.tenant) ||
      isPlaceholder(this.values.project)
    )
  }

  private isEmbedded(): boolean {
    return Boolean(window.parent && window.parent !== window)
  }

  private applyRuntimeConfig(config: RuntimeConfigPayload): boolean {
    const tenant = config.defaultTenant || config.tenant || config.tenant_id
    const project = config.defaultProject || config.project || config.project_id
    const idTokenHeader = config.idTokenHeader || config.idTokenHeaderName || config.auth?.idTokenHeaderName
    this.values = {
      ...this.values,
      baseUrl: config.baseUrl || this.values.baseUrl,
      accessToken: config.accessToken ?? this.values.accessToken,
      idToken: config.idToken ?? this.values.idToken,
      idTokenHeader: idTokenHeader || this.values.idTokenHeader,
      tenant: tenant || this.values.tenant,
      project: project || this.values.project,
    }
    return Boolean(
      tenant || project || config.baseUrl ||
      config.accessToken !== undefined || config.idToken !== undefined || idTokenHeader,
    )
  }

  private async loadFrontendConfig(): Promise<boolean> {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 1500)
    try {
      const response = await fetch(`${this.getBaseUrl()}/api/cp-frontend-config`, {
        credentials: 'include',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      })
      if (!response.ok) return false
      return this.applyRuntimeConfig(await response.json())
    } catch {
      return false
    } finally {
      window.clearTimeout(timeout)
    }
  }

  /** Re-request config from the host (or the frontend-config endpoint). Used on
   *  a `kdcube-auth-changed` broadcast so tokens refresh without a reload. */
  async requestConfig(): Promise<void> {
    if (this.isEmbedded()) {
      window.parent.postMessage({ type: 'CONFIG_REQUEST', widget: 'app_config' }, '*')
    }
    await this.loadFrontendConfig()
  }

  setupParentListener(): Promise<boolean> {
    const embedded = this.isEmbedded()
    if (!this.needsRuntimeConfig() && !embedded) {
      return Promise.resolve(true)
    }

    let resolveReady: ((value: boolean) => void) | null = null
    let resolved = false
    const finish = (ready: boolean) => {
      if (resolved) return
      resolved = true
      resolveReady?.(ready)
    }

    window.addEventListener('message', (event: MessageEvent) => {
      const data = event.data
      if (!data || typeof data !== 'object') return
      // Accept both handshake reply types — the platform uses each in different
      // host contexts (control plane / embedded scene). Never listen for only one.
      const type = (data as { type?: string }).type
      if (type !== 'CONFIG_RESPONSE' && type !== 'CONN_RESPONSE') return
      const payload = (data as { config?: RuntimeConfigPayload }).config
      if (payload && this.applyRuntimeConfig(payload)) finish(true)
    })

    return new Promise<boolean>((resolve) => {
      resolveReady = resolve
      if (embedded) {
        window.parent.postMessage({ type: 'CONFIG_REQUEST', widget: 'app_config' }, '*')
      }
      void this.loadFrontendConfig().then((ok) => {
        if (ok && !this.needsRuntimeConfig()) finish(true)
      })
      window.setTimeout(() => finish(!this.needsRuntimeConfig()), 4000)
    })
  }
}

export const settings = new Settings()
