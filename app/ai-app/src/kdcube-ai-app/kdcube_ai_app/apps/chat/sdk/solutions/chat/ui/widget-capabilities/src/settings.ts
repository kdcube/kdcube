/**
 * Runtime config + auth resolver for the capability-picker widget — the
 * standard served-widget handshake (same contract as the usage-card widget):
 * build-time `{{...}}` placeholders, `CONFIG_REQUEST`/`CONFIG_RESPONSE`
 * parent handshake when embedded, `/api/cp-frontend-config` fallback for
 * standalone direct-load. Route context supplies tenant/project/bundle when
 * the widget is served from its bundle URL.
 */

const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}'
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}'
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}'
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}'
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}'
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}'
const PLACEHOLDER_BUNDLE_ID = '{{DEFAULT_APP_BUNDLE_ID}}'

type RuntimeConfigPayload = {
  baseUrl?: string
  accessToken?: string | null
  idToken?: string | null
  idTokenHeader?: string
  idTokenHeaderName?: string
  auth?: { idTokenHeaderName?: string }
  configSource?: string
  hostedByScene?: boolean
  scene?: { embedded?: boolean; configSource?: string }
  defaultTenant?: string
  defaultProject?: string
  defaultAppBundleId?: string
  tenant?: string
  tenant_id?: string
  project?: string
  project_id?: string
  agentId?: string
}

function isPlaceholder(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.includes('{{') && value.includes('}}')
}

function routeContext() {
  const markers = ['/api/integrations/bundles/', '/api/integrations/static/']
  const path = window.location.pathname
  const marker = markers.find((candidate) => path.includes(candidate))
  const index = marker ? path.indexOf(marker) : -1
  const query = new URLSearchParams(window.location.search)
  if (!marker || index < 0) {
    return {
      tenant: query.get('tenant') || '',
      project: query.get('project') || '',
      bundleId: query.get('bundle_id') || query.get('bundleId') || '',
      agentId: query.get('agent') || '',
    }
  }
  const parts = path.slice(index + marker.length).split('/').map((part) => decodeURIComponent(part))
  return {
    tenant: parts[0] || query.get('tenant') || '',
    project: parts[1] || query.get('project') || '',
    bundleId: parts[2] || query.get('bundle_id') || query.get('bundleId') || '',
    agentId: query.get('agent') || '',
  }
}

const context = routeContext()

class Settings {
  private values = {
    baseUrl: PLACEHOLDER_BASE_URL,
    accessToken: PLACEHOLDER_ACCESS_TOKEN,
    idToken: PLACEHOLDER_ID_TOKEN,
    idTokenHeader: PLACEHOLDER_ID_TOKEN_HEADER,
    tenant: PLACEHOLDER_TENANT,
    project: PLACEHOLDER_PROJECT,
    bundleId: PLACEHOLDER_BUNDLE_ID,
    agentId: '',
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

  getBundleId(): string {
    return isPlaceholder(this.values.bundleId) ? context.bundleId : this.values.bundleId
  }

  /** Widget-config agent: `?agent=` (scene component params) over the
   *  handshake's agentId, defaulting to the bundle's default agent ('main'). */
  getAgentId(): string {
    return context.agentId || this.values.agentId || 'main'
  }

  authHeaders(base?: HeadersInit): Headers {
    const headers = new Headers(base)
    if (this.values.accessToken && !isPlaceholder(this.values.accessToken)) {
      headers.set('Authorization', `Bearer ${this.values.accessToken}`)
    }
    if (this.values.idToken && !isPlaceholder(this.values.idToken)) {
      headers.set(
        isPlaceholder(this.values.idTokenHeader) ? 'X-ID-Token' : this.values.idTokenHeader,
        this.values.idToken,
      )
    }
    return headers
  }

  private needsRuntimeConfig(): boolean {
    return (
      isPlaceholder(this.values.baseUrl) ||
      isPlaceholder(this.values.tenant) ||
      isPlaceholder(this.values.project) ||
      isPlaceholder(this.values.bundleId)
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
      bundleId: config.defaultAppBundleId || this.values.bundleId,
      agentId: config.agentId || this.values.agentId,
    }
    return Boolean(
      tenant || project || config.baseUrl ||
      config.accessToken !== undefined ||
      config.idToken !== undefined ||
      idTokenHeader || config.defaultAppBundleId,
    )
  }

  private async loadFrontendConfig(): Promise<boolean> {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 1000)
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

  setupParentListener(): Promise<boolean> {
    const needsRuntimeConfig = this.needsRuntimeConfig()
    const embedded = this.isEmbedded()
    if (!needsRuntimeConfig && !embedded) {
      return Promise.resolve(true)
    }

    let resolveReady: ((value: boolean) => void) | null = null
    let resolved = false
    const finish = (ready: boolean) => {
      if (resolved) return
      resolved = true
      resolveReady?.(ready)
    }

    const onMessage = (event: MessageEvent) => {
      const data = event.data
      if (!data || typeof data !== 'object') return
      if ((data as { type?: string }).type !== 'CONFIG_RESPONSE') return
      const payload = (data as { config?: RuntimeConfigPayload }).config
      if (payload && this.applyRuntimeConfig(payload)) finish(true)
    }
    window.addEventListener('message', onMessage)

    return new Promise<boolean>((resolve) => {
      resolveReady = resolve
      if (embedded) {
        window.parent.postMessage({ type: 'CONFIG_REQUEST', widget: 'capabilities' }, '*')
      }
      void this.loadFrontendConfig().then((ok) => {
        if (ok && !this.needsRuntimeConfig()) finish(true)
      })
      window.setTimeout(() => finish(!this.needsRuntimeConfig()), 4000)
    })
  }
}

export const settings = new Settings()
