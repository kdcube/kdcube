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
    const raw = this.settings.baseUrl === PLACEHOLDER_BASE_URL ? window.location.origin : this.settings.baseUrl
    return raw.replace(/\/$/, '')
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

  getBundleId(): string {
    if (!this.settings.defaultBundleId || this.settings.defaultBundleId === PLACEHOLDER_BUNDLE) {
      return BUILT_BUNDLE_ID
    }
    return this.settings.defaultBundleId
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
    const identity = 'BUNDLE_VERSATILE_MAIN_VIEW'

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
      if (config.defaultAppBundleId !== undefined) updates.defaultBundleId = config.defaultAppBundleId

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

      return new Promise<boolean>((resolve) => {
        const timeout = window.setTimeout(() => {
          resolve(false)
        }, 3000)
        const previous = this.configReceivedCallback
        this.onConfigReceived(() => {
          window.clearTimeout(timeout)
          previous?.()
          resolve(true)
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
