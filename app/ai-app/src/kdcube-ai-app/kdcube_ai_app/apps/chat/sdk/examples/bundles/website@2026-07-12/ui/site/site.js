const DEFAULT_PLATFORM_PREFIX = '/platform'

const frame = document.getElementById('workspace-scene')
const notice = document.getElementById('site-notice')
const brandLink = document.getElementById('brand-link')
const scopeLabel = document.getElementById('runtime-scope')
const identityLabel = document.getElementById('identity')
const authButton = document.getElementById('auth-button')
const platformLink = document.getElementById('platform-link')

let platformConfig = null
let siteConfig = null
let profile = null

function asObject(value) {
  return value && typeof value === 'object' ? value : {}
}

function unwrap(payload, key) {
  const record = asObject(payload)
  return asObject(record[key] ?? record)
}

function routeContext() {
  const match = String(document.baseURI || '').match(
    /\/api\/integrations\/bundles\/([^/]+)\/([^/]+)\/([^/]+)\/public\/static\//,
  )
  if (!match) return null
  return {
    tenant: decodeURIComponent(match[1]),
    project: decodeURIComponent(match[2]),
    applicationId: decodeURIComponent(match[3]),
  }
}

async function fetchJson(url, options = {}) {
  const { headers = {}, ...requestOptions } = options
  const response = await fetch(url, {
    credentials: 'include',
    cache: 'no-store',
    ...requestOptions,
    headers: { Accept: 'application/json', ...headers },
  })
  if (!response.ok) throw new Error(`${url} returned ${response.status}`)
  return response.json()
}

function setNotice(message, error = false) {
  notice.textContent = message
  notice.classList.toggle('error', error)
  notice.hidden = !message
}

function isAuthenticated(value) {
  const record = asObject(value)
  return Boolean(record.user_id) && String(record.user_type || '').toLowerCase() !== 'anonymous'
}

function platformPath() {
  const prefix = String(platformConfig?.routesPrefix || DEFAULT_PLATFORM_PREFIX).replace(/\/$/, '')
  return `${prefix}/chat`
}

function siteHomePath() {
  const alias = String(siteConfig?.site_alias || '').trim()
  return alias && window.location.pathname.startsWith('/sites/')
    ? `/sites/${encodeURIComponent(alias)}`
    : '/'
}

function returnPath() {
  return `${window.location.pathname}${window.location.search}` || siteHomePath()
}

function loginUrl() {
  const configured = String(platformConfig?.auth?.loginUrl || '').trim()
  const target = configured || platformPath()
  const url = new URL(target, window.location.origin)
  if (configured) url.searchParams.set('next', returnPath())
  return url.toString()
}

function runtimeConfig() {
  const tenant = String(siteConfig?.tenant || platformConfig?.tenant || '')
  const project = String(siteConfig?.project || platformConfig?.project || '')
  const applicationId = String(siteConfig?.scene_application_id || '')
  return {
    configSource: 'website',
    baseUrl: window.location.origin,
    tenant,
    project,
    defaultTenant: tenant,
    defaultProject: project,
    defaultApp: applicationId,
    defaultAppBundleId: applicationId,
    auth: asObject(platformConfig?.auth),
    scene: {
      embedded: true,
      configSource: 'host',
      surface_ref: 'website.workspace',
      liveEventsTransport: 'scene',
    },
    liveEventsTransport: 'scene',
  }
}

function notifyScene(reason) {
  const authenticated = isAuthenticated(profile)
  const detail = {
    ready: true,
    authenticated,
    reason,
    user: authenticated
      ? {
          sub: profile.user_id || '',
          email: profile.email || '',
          name: profile.username || profile.name || profile.email || profile.user_id || '',
        }
      : null,
  }
  frame.contentWindow?.postMessage({ type: 'kdcube-auth-changed', auth: detail }, window.location.origin)
}

function renderAuth() {
  const authenticated = isAuthenticated(profile)
  identityLabel.textContent = authenticated
    ? String(profile.email || profile.username || profile.user_id || '')
    : ''
  authButton.textContent = authenticated ? 'Sign out' : 'Sign in'
  platformLink.href = platformPath()
}

async function refreshProfile(reason = 'profile') {
  const profileUrl = String(siteConfig?.profile_url || platformConfig?.auth?.profileUrl || '/profile')
  try {
    profile = await fetchJson(profileUrl)
  } catch (_error) {
    profile = null
  }
  renderAuth()
  notifyScene(reason)
  return profile
}

function openLogin() {
  const target = loginUrl()
  const popup = window.open(target, 'kdcube_platform_login', 'width=620,height=760')
  if (!popup) {
    window.location.assign(target)
    return
  }
  const deadline = Date.now() + 5 * 60 * 1000
  const timer = window.setInterval(async () => {
    const current = await refreshProfile('login-poll')
    if (isAuthenticated(current)) {
      window.clearInterval(timer)
      try { popup.close() } catch (_error) {}
      return
    }
    if (popup.closed || Date.now() > deadline) window.clearInterval(timer)
  }, 1200)
}

async function signOut() {
  const logoutUrl = String(platformConfig?.auth?.logoutUrl || '/api/platform/logout')
  try {
    await fetchJson(logoutUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ next: siteHomePath() }).toString(),
    })
  } finally {
    await refreshProfile('logout')
  }
}

async function bootstrap() {
  try {
    const route = routeContext()
    if (!route) throw new Error('Application route context is unavailable')

    const configUrl = [
      '/api/integrations/bundles',
      encodeURIComponent(route.tenant),
      encodeURIComponent(route.project),
      encodeURIComponent(route.applicationId),
      'public/site_config',
    ].join('/')
    siteConfig = unwrap(await fetchJson(configUrl), 'site_config')
    platformConfig = await fetchJson(
      String(siteConfig.platform_config_url || '/api/cp-frontend-config'),
    )

    const tenant = String(siteConfig.tenant || platformConfig.tenant || route.tenant)
    const project = String(siteConfig.project || platformConfig.project || route.project)
    const sceneApplicationId = String(siteConfig.scene_application_id || '').trim()
    if (!sceneApplicationId) throw new Error('No scene application is configured for this site')

    document.title = String(siteConfig.title || 'KDCube Workspace')
    brandLink.href = siteHomePath()
    scopeLabel.textContent = `${tenant} / ${project}`
    platformLink.href = platformPath()
    frame.src = [
      '/api/integrations/bundles',
      encodeURIComponent(tenant),
      encodeURIComponent(project),
      encodeURIComponent(sceneApplicationId),
      'public/static',
    ].join('/')
    frame.addEventListener('load', () => {
      setNotice('')
      notifyScene('scene-load')
    }, { once: true })
    await refreshProfile('initial')
  } catch (error) {
    setNotice(error instanceof Error ? error.message : String(error), true)
    scopeLabel.textContent = 'Runtime unavailable'
  }
}

authButton.addEventListener('click', () => {
  if (isAuthenticated(profile)) void signOut()
  else openLogin()
})

window.addEventListener('message', (event) => {
  if (event.origin !== window.location.origin || event.source !== frame.contentWindow) return
  const message = asObject(event.data)
  if (message.type === 'kdcube-auth-required') {
    openLogin()
    return
  }
  if (message.type !== 'CONFIG_REQUEST') return
  const data = asObject(message.data)
  const identity = String(data.identity || message.identity || '').trim()
  if (!identity) return
  frame.contentWindow?.postMessage({
    type: 'CONFIG_RESPONSE',
    identity,
    config: runtimeConfig(),
  }, window.location.origin)
})

void bootstrap()
