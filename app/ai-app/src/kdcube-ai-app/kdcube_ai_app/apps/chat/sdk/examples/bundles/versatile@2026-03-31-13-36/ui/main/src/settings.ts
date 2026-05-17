export interface AppSettings {
  baseUrl: string
  accessToken: string | null
  idToken: string | null
  idTokenHeader: string
  tenant: string
  project: string
  defaultBundleId: string | null
}

const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}'
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}'
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}'
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}'
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}'
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}'
const PLACEHOLDER_BUNDLE = '{{DEFAULT_APP_BUNDLE_ID}}'

export const BUILT_BUNDLE_ID = import.meta.env.VITE_BUNDLE_ID || 'versatile@2026-03-31-13-36'

interface RouteContext {
  tenant: string
  project: string
  bundleId: string
}

interface RuntimeConfigPayload {
  baseUrl?: string
  accessToken?: string | null
  idToken?: string | null
  idTokenHeader?: string
  idTokenHeaderName?: string
  defaultTenant?: string
  defaultProject?: string
  defaultAppBundleId?: string | null
  tenant?: string
  project?: string
  tenant_id?: string
  project_id?: string
  auth?: {
    idTokenHeaderName?: string
  }
}

function isPlaceholder(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.includes('{{') && value.includes('}}')
}

function decodePathPart(value: string | undefined): string {
  if (!value) return ''
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

function routeContextFromLocation(): RouteContext {
  const path = window.location.pathname
  const markers = [
    '/api/integrations/static/',
    '/api/integrations/bundles/',
  ]

  for (const marker of markers) {
    const index = path.indexOf(marker)
    if (index < 0) continue
    const parts = path.slice(index + marker.length).split('/').map(decodePathPart)
    return {
      tenant: parts[0] || '',
      project: parts[1] || '',
      bundleId: parts[2] || BUILT_BUNDLE_ID,
    }
  }

  const params = new URLSearchParams(window.location.search)
  return {
    tenant: params.get('tenant') || '',
    project: params.get('project') || '',
    bundleId: params.get('bundle_id') || params.get('bundleId') || BUILT_BUNDLE_ID,
  }
}

const ROUTE_CONTEXT = routeContextFromLocation()

class SettingsManager {
  private settings: AppSettings = {
    baseUrl: PLACEHOLDER_BASE_URL,
    accessToken: PLACEHOLDER_ACCESS_TOKEN,
    idToken: PLACEHOLDER_ID_TOKEN,
    idTokenHeader: PLACEHOLDER_ID_TOKEN_HEADER,
    tenant: PLACEHOLDER_TENANT,
    project: PLACEHOLDER_PROJECT,
    defaultBundleId: PLACEHOLDER_BUNDLE,
  }

  private configReceivedCallback: (() => void) | null = null

  getBaseUrl(): string {
    const raw = isPlaceholder(this.settings.baseUrl) ? window.location.origin : this.settings.baseUrl
    return raw.replace(/\/$/, '')
  }

  getAccessToken(): string | null {
    return isPlaceholder(this.settings.accessToken) ? null : this.settings.accessToken
  }

  getIdToken(): string | null {
    return isPlaceholder(this.settings.idToken) ? null : this.settings.idToken
  }

  getIdTokenHeader(): string {
    return isPlaceholder(this.settings.idTokenHeader)
      ? 'X-ID-Token'
      : this.settings.idTokenHeader
  }

  getTenant(): string {
    return isPlaceholder(this.settings.tenant) ? ROUTE_CONTEXT.tenant : this.settings.tenant
  }

  getProject(): string {
    return isPlaceholder(this.settings.project) ? ROUTE_CONTEXT.project : this.settings.project
  }

  getBundleId(): string {
    if (!this.settings.defaultBundleId || isPlaceholder(this.settings.defaultBundleId)) {
      return ROUTE_CONTEXT.bundleId || BUILT_BUNDLE_ID
    }
    return this.settings.defaultBundleId
  }

  needsRuntimeConfig(): boolean {
    return (
      isPlaceholder(this.settings.baseUrl) ||
      isPlaceholder(this.settings.tenant) ||
      isPlaceholder(this.settings.project) ||
      !this.settings.defaultBundleId ||
      isPlaceholder(this.settings.defaultBundleId)
    )
  }

  update(partial: Partial<AppSettings>): void {
    this.settings = { ...this.settings, ...partial }
  }

  updateFromRuntimeConfig(config: RuntimeConfigPayload, options: { notify?: boolean } = {}): boolean {
    const updates: Partial<AppSettings> = {}
    if (config.baseUrl) updates.baseUrl = config.baseUrl
    if (config.accessToken !== undefined) updates.accessToken = config.accessToken
    if (config.idToken !== undefined) updates.idToken = config.idToken
    if (config.idTokenHeader) updates.idTokenHeader = config.idTokenHeader
    if (config.idTokenHeaderName) updates.idTokenHeader = config.idTokenHeaderName
    if (config.auth?.idTokenHeaderName) updates.idTokenHeader = config.auth.idTokenHeaderName

    const tenant = config.defaultTenant || config.tenant || config.tenant_id
    const project = config.defaultProject || config.project || config.project_id
    if (tenant) updates.tenant = tenant
    if (project) updates.project = project
    if (config.defaultAppBundleId !== undefined) updates.defaultBundleId = config.defaultAppBundleId

    if (Object.keys(updates).length === 0) {
      return false
    }
    this.update(updates)
    if (options.notify !== false) {
      this.configReceivedCallback?.()
    }
    return true
  }

  async loadFrontendConfig(): Promise<boolean> {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 1000)
    try {
      const response = await fetch(`${this.getBaseUrl()}/api/cp-frontend-config`, {
        method: 'GET',
        credentials: 'include',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      })
      if (!response.ok) return false
      const config = (await response.json()) as RuntimeConfigPayload | null
      if (!config || typeof config !== 'object') return false
      return this.updateFromRuntimeConfig(config, { notify: false })
    } catch {
      return false
    } finally {
      window.clearTimeout(timeout)
    }
  }

  onConfigReceived(cb: () => void): void {
    this.configReceivedCallback = cb
  }

  setupParentListener(): Promise<boolean> {
    const identity = 'BUNDLE_VERSATILE_MAIN_VIEW'

    window.addEventListener('message', (event: MessageEvent) => {
      if (event.data?.type !== 'CONN_RESPONSE' && event.data?.type !== 'CONFIG_RESPONSE') return
      if (event.data.identity !== identity) return

      const config = event.data.config
      if (!config) return

      this.updateFromRuntimeConfig(config)
    })

    if (this.needsRuntimeConfig()) {
      return new Promise<boolean>((resolve) => {
        let resolved = false
        const finish = (ready: boolean) => {
          if (resolved) return
          resolved = true
          resolve(ready)
        }
        const requestParentConfig = () => {
          window.parent.postMessage(
            {
              type: 'CONFIG_REQUEST',
              data: {
                requestedFields: [
                  'baseUrl',
                  'accessToken',
                  'idToken',
                  'idTokenHeader',
                  'defaultTenant',
                  'defaultProject',
                  'defaultAppBundleId',
                ],
                identity,
              },
            },
            '*',
          )
          const timeout = window.setTimeout(() => finish(Boolean(this.getTenant() && this.getProject())), 3000)
          const previous = this.configReceivedCallback
          this.onConfigReceived(() => {
            window.clearTimeout(timeout)
            previous?.()
            finish(true)
          })
        }
        void this.loadFrontendConfig().then((loaded) => {
          if (loaded) {
            finish(true)
          } else {
            requestParentConfig()
          }
        })
      })
    }

    return Promise.resolve(true)
  }
}

export const settings = new SettingsManager()

export function makeAuthHeaders(base?: HeadersInit): Headers {
  const headers = new Headers(base)
  const accessToken = settings.getAccessToken()
  const idToken = settings.getIdToken()
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  if (idToken) headers.set(settings.getIdTokenHeader(), idToken)
  return headers
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

export function createLocalId(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2, 10)
  return `${prefix}_${random}`
}
