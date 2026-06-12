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

function queryValue(name: string): string | null {
  if (typeof window === 'undefined') return null
  const params = new URLSearchParams(window.location.search)
  const value = params.get(name)
  return value && value.trim() ? value.trim() : null
}

function configValue(envName: string, queryName: string, fallback: string): string {
  const envValue = import.meta.env[envName]
  return (typeof envValue === 'string' && envValue.trim()) || queryValue(queryName) || fallback
}

export const CHAT_WIDGET_ID = configValue('VITE_CHAT_WIDGET_ID', 'chat_widget_id', 'chat_widget')
export const CHAT_CONFIG_IDENTITY = configValue('VITE_CHAT_CONFIG_IDENTITY', 'chat_config_identity', 'BUNDLE_CHAT_VIEW')
export const CHAT_BRAND_LABEL = configValue('VITE_CHAT_BRAND_LABEL', 'chat_brand_label', 'Chat')
export const CHAT_EVENT_PREFIX = configValue('VITE_CHAT_EVENT_PREFIX', 'chat_event_prefix', 'chat')
export const CHAT_SURFACE = configValue('VITE_CHAT_SURFACE', 'chat_surface', `${CHAT_EVENT_PREFIX}_chat`)
export const CHAT_CANVAS_SURFACE = configValue('VITE_CHAT_CANVAS_SURFACE', 'chat_canvas_surface', `${CHAT_EVENT_PREFIX}_canvas`)
export const CHAT_SNAPSHOT_SURFACE = configValue('VITE_CHAT_SNAPSHOT_SURFACE', 'chat_snapshot_surface', `${CHAT_EVENT_PREFIX}_wizard`)
export const CHAT_USER_EVENT_SOURCE_ID = configValue('VITE_CHAT_USER_EVENT_SOURCE_ID', 'chat_user_event_source_id', `${CHAT_EVENT_PREFIX}.main.chat.user`)
export const CHAT_ATTACHMENT_EVENT_SOURCE_ID = configValue('VITE_CHAT_ATTACHMENT_EVENT_SOURCE_ID', 'chat_attachment_event_source_id', `${CHAT_EVENT_PREFIX}.main.chat.attachment`)
export const CHAT_CONTEXT_EVENT_SOURCE_ID = configValue('VITE_CHAT_CONTEXT_EVENT_SOURCE_ID', 'chat_context_event_source_id', `${CHAT_EVENT_PREFIX}.context.focus`)
export const CHAT_CANVAS_STATE_EVENT_SOURCE_ID = configValue('VITE_CHAT_CANVAS_STATE_EVENT_SOURCE_ID', 'chat_canvas_state_event_source_id', `${CHAT_EVENT_PREFIX}.canvas.state`)
export const CHAT_CANVAS_FOCUS_EVENT_SOURCE_ID = configValue('VITE_CHAT_CANVAS_FOCUS_EVENT_SOURCE_ID', 'chat_canvas_focus_event_source_id', `${CHAT_EVENT_PREFIX}.canvas.focus`)
export const CHAT_SNAPSHOT_EVENT_SOURCE_ID = configValue('VITE_CHAT_SNAPSHOT_EVENT_SOURCE_ID', 'chat_snapshot_event_source_id', `${CHAT_EVENT_PREFIX}.snapshot`)
export const CHAT_CONTEXT_ATTACH_MESSAGE = configValue('VITE_CHAT_CONTEXT_ATTACH_MESSAGE', 'chat_context_attach_message', 'kdcube.context.attach')
export const CHAT_CONTEXT_FOCUS_MESSAGE = configValue('VITE_CHAT_CONTEXT_FOCUS_MESSAGE', 'chat_context_focus_message', 'kdcube.context.focus')
export const CHAT_CONTEXT_REMOVE_MESSAGE = configValue('VITE_CHAT_CONTEXT_REMOVE_MESSAGE', 'chat_context_remove_message', 'kdcube.context.remove')
export const CHAT_CONTEXT_REFRESH_SOURCE = configValue('VITE_CHAT_CONTEXT_REFRESH_SOURCE', 'chat_context_refresh_source', 'kdcube.context.refresh')
export const CHAT_CANVAS_PATCH_STEP = configValue('VITE_CHAT_CANVAS_PATCH_STEP', 'chat_canvas_patch_step', `${CHAT_EVENT_PREFIX}.canvas.patch`)
export const CHAT_CANVAS_PATCH_MESSAGE = configValue('VITE_CHAT_CANVAS_PATCH_MESSAGE', 'chat_canvas_patch_message', 'kdcube.canvas.patch')
export const CHAT_CANVAS_PATCH_SOURCE = configValue('VITE_CHAT_CANVAS_PATCH_SOURCE', 'chat_canvas_patch_source', 'chat-widget')
export const CHAT_CANVAS_INGRESS_MESSAGE = configValue('VITE_CHAT_CANVAS_INGRESS_MESSAGE', 'chat_canvas_ingress_message', 'kdcube.canvas.ingress')
export const BUILT_BUNDLE_ID = configValue('VITE_BUNDLE_ID', 'bundle_id', '')

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

export function isStandaloneDevChat(): boolean {
  if (!import.meta.env.DEV || typeof window === 'undefined') return false
  return !/\/api\/integrations\/(?:static|bundles)\//.test(window.location.pathname)
}

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
    const identity = CHAT_CONFIG_IDENTITY

    if (isStandaloneDevChat()) {
      this.update({
        baseUrl: window.location.origin,
        tenant: ROUTE_CONTEXT.tenant || 'demo-tenant',
        project: ROUTE_CONTEXT.project || 'demo-project',
        defaultBundleId: ROUTE_CONTEXT.bundleId || BUILT_BUNDLE_ID,
        accessToken: null,
        idToken: null,
      })
      return Promise.resolve(true)
    }

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
