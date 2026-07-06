/**
 * Scene composition config for the versatile scene host.
 *
 * The scene is a config-driven host: `scene_surface_config` (backed by the
 * bundle's `surfaces.as_consumer.ui.scene` section) names the components the
 * rail summons — each one an iframe-mounted served widget from its owning
 * app — plus the external panels and namespace styles. Built-in defaults
 * reproduce the standard versatile set (pin board, chat, memories, usage)
 * when the config section is absent; config entries override or extend them.
 */

export const BUNDLE_ID = 'versatile@2026-03-31-13-36'
export const CONFIG_IDENTITY = 'BUNDLE_VERSATILE_MAIN_VIEW'
export const CHAT_CONFIG_IDENTITY = 'BUNDLE_VERSATILE_CHAT_VIEW'
export const CHAT_WIDGET_ALIAS = 'versatile_chat'
export const MEMORY_WIDGET_BUNDLE_ID = 'user-memories@2026-06-26'

export interface RouteContext {
  tenant: string
  project: string
  bundleId: string
  publicStatic: boolean
  baseUrl: string
  accessToken?: string | null
  idToken?: string | null
}

export interface RuntimeConfig {
  baseUrl?: string
  defaultTenant?: string
  defaultProject?: string
  defaultAppBundleId?: string | null
  tenant?: string
  project?: string
  tenant_id?: string
  project_id?: string
  accessToken?: string | null
  idToken?: string | null
}

export interface SceneExternalPanelSurfaceConfig {
  label?: string
  expanded?: boolean
  command?: Record<string, unknown>
  command_from_open?: 'provider_surface_open' | string
}

export interface SceneExternalPanelConfig {
  id: string
  label: string
  title?: string
  bundle_id: string
  widget_alias: string
  widget_message_type?: string
  service_event_type?: string
  service_forward_message_type?: string
  open_message_types?: string[]
  surfaces?: Record<string, SceneExternalPanelSurfaceConfig>
}

export interface SceneDropAccepts {
  effect: 'attach' | 'pin' | 'open'
  patterns: string[]
  /**
   * Surface an `open` drop resolves toward (may belong to ANOTHER component,
   * e.g. the memories list accepts `mem:*` drops that open in the
   * memory-item editor surface — same routing as the website host).
   */
  targetSurface?: string
}

export interface SceneComponentSpec {
  alias: string
  /** Owning app package; '' means the scene's own bundle. */
  bundleId: string
  /** Served widget alias (used when `route` is absent → `widgets/<alias>`). */
  widgetAlias: string
  /** Explicit widget route relative to the app base (overrides widgetAlias). */
  route?: string
  params?: Record<string, string>
  title: string
  accent: string
  /** Hidden from the rail (and closed) unless the viewer is authenticated. */
  gated: boolean
  /** The widget supports compact/expanded views (kdcube-set-view). */
  views: boolean
  size: { w: number; h: number }
  full?: { w: number; h: number }
  targetSurfaces: string[]
  /** Cross-surface context drop acceptance for the host drag overlay. */
  drop?: SceneDropAccepts
  /**
   * `docked` components have a static home on the scene stage (the website's
   * docked-tile model): the rail (or the tile's unpin control) promotes the
   * SAME iframe into a floating window and docks it back. `floating`
   * components are summoned from the rail.
   */
  placement: 'docked' | 'floating'
  /** Rendered as a rail button. Surface-command-only components opt out. */
  rail: boolean
  defaultOpen: boolean
  enabled: boolean
  order: number
}

export interface SceneConfig {
  components: SceneComponentSpec[]
  external_panels: SceneExternalPanelConfig[]
  namespaceStyles: Record<string, unknown>
}

function decodePart(value: string | undefined): string {
  if (value === undefined || value === '') return ''
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

export function routeContext(): RouteContext {
  const path = window.location.pathname
  const publicMarker = '/api/integrations/bundles/'
  const staticMarker = '/api/integrations/static/'

  const publicIndex = path.indexOf(publicMarker)
  if (publicIndex >= 0) {
    const parts = path.slice(publicIndex + publicMarker.length).split('/').map(decodePart)
    return {
      tenant: parts[0] ?? '',
      project: parts[1] ?? '',
      bundleId: parts[2] ?? BUNDLE_ID,
      publicStatic: parts[3] === 'public' && parts[4] === 'static',
      baseUrl: window.location.origin,
    }
  }

  const staticIndex = path.indexOf(staticMarker)
  if (staticIndex >= 0) {
    const parts = path.slice(staticIndex + staticMarker.length).split('/').map(decodePart)
    return {
      tenant: parts[0] ?? '',
      project: parts[1] ?? '',
      bundleId: parts[2] ?? BUNDLE_ID,
      publicStatic: false,
      baseUrl: window.location.origin,
    }
  }

  const params = new URLSearchParams(window.location.search)
  return {
    tenant: params.get('tenant') ?? 'demo-tenant',
    project: params.get('project') ?? 'demo-project',
    bundleId: params.get('bundle_id') ?? params.get('bundleId') ?? BUNDLE_ID,
    publicStatic: params.get('public') === '1',
    baseUrl: window.location.origin,
  }
}

export function contextFromConfig(config: RuntimeConfig | null, fallback: RouteContext): RouteContext {
  if (!config) return fallback
  return {
    tenant: config.defaultTenant ?? config.tenant ?? config.tenant_id ?? fallback.tenant,
    project: config.defaultProject ?? config.project ?? config.project_id ?? fallback.project,
    bundleId: config.defaultAppBundleId ?? fallback.bundleId,
    publicStatic: fallback.publicStatic,
    baseUrl: config.baseUrl ?? fallback.baseUrl,
    accessToken: config.accessToken ?? fallback.accessToken,
    idToken: config.idToken ?? fallback.idToken,
  }
}

export function requestRuntimeConfig(): Promise<RuntimeConfig | null> {
  if (window.parent === window) return Promise.resolve(null)
  window.parent.postMessage({ type: 'CONFIG_REQUEST', identity: CONFIG_IDENTITY }, '*')
  return new Promise<RuntimeConfig | null>((resolve) => {
    const timer = window.setTimeout(() => {
      window.removeEventListener('message', onMessage)
      resolve(null)
    }, 1200)
    function onMessage(event: MessageEvent): void {
      if (event.data?.type !== 'CONFIG_RESPONSE' && event.data?.type !== 'CONN_RESPONSE') return
      if (event.data.identity !== CONFIG_IDENTITY) return
      window.clearTimeout(timer)
      window.removeEventListener('message', onMessage)
      resolve(event.data.config ?? null)
    }
    window.addEventListener('message', onMessage)
  })
}

export function appBaseUrl(ctx: RouteContext, bundleId?: string): string {
  const app = bundleId || ctx.bundleId
  return (
    `${ctx.baseUrl}/api/integrations/bundles/` +
    `${encodeURIComponent(ctx.tenant)}/${encodeURIComponent(ctx.project)}/${encodeURIComponent(app)}`
  )
}

export function widgetUrlForBundle(
  ctx: RouteContext,
  bundleId: string,
  alias: string,
  params?: Record<string, string>,
): string {
  const routePrefix = ctx.publicStatic ? 'public/widgets' : 'widgets'
  const url = new URL(`${appBaseUrl(ctx, bundleId)}/${routePrefix}/${alias}`)
  if (params) {
    Object.entries(params).forEach(([key, value]) => url.searchParams.set(key, value))
  }
  return url.toString()
}

export function componentWidgetUrl(ctx: RouteContext, spec: SceneComponentSpec): string {
  if (spec.route) {
    const route = spec.route.replace(/^\/+/, '')
    const url = new URL(`${appBaseUrl(ctx, spec.bundleId || ctx.bundleId)}/${route}`)
    Object.entries(spec.params ?? {}).forEach(([key, value]) => url.searchParams.set(key, value))
    return url.toString()
  }
  return widgetUrlForBundle(ctx, spec.bundleId || ctx.bundleId, spec.widgetAlias, spec.params)
}

export function chatWidgetParams(ctx: RouteContext): Record<string, string> {
  return {
    chat_embed_mode: 'host',
    chat_widget_id: CHAT_WIDGET_ALIAS,
    chat_config_identity: CHAT_CONFIG_IDENTITY,
    chat_brand_label: 'Versatile',
    chat_event_prefix: 'versatile',
    chat_surface: 'versatile_chat',
    chat_user_event_source_id: 'versatile.main.chat.user',
    chat_attachment_event_source_id: 'versatile.main.chat.attachment',
    chat_context_event_source_id: 'versatile.context.focus',
    chat_canvas_state_event_source_id: 'canvas.state',
    chat_canvas_focus_event_source_id: 'canvas.focus',
    chat_canvas_surface: 'canvas',
    chat_canvas_ingress_message: 'kdcube.canvas.ingress',
    chat_canvas_patch_step: 'canvas.patch',
    chat_context_attach_message: 'kdcube-context-attach',
    chat_context_focus_message: 'kdcube-context-focus',
    chat_context_remove_message: 'kdcube-context-remove',
    chat_context_refresh_source: 'kdcube-context-refresh',
    bundle_id: ctx.bundleId,
  }
}

export function externalWidgetUrl(ctx: RouteContext, panel: SceneExternalPanelConfig, expanded: boolean): string {
  return widgetUrlForBundle(ctx, panel.bundle_id, panel.widget_alias, {
    view: expanded ? 'expanded' : 'compact',
  })
}

export function operationsUrl(ctx: RouteContext, alias: string): string {
  return `${appBaseUrl(ctx)}/operations/${alias}`
}

export function unwrapOperationResponse<T>(alias: string, payload: unknown): T {
  if (payload && typeof payload === 'object' && alias in payload) {
    return (payload as Record<string, unknown>)[alias] as T
  }
  return payload as T
}

export function operationErrorMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== 'object') return fallback
  const direct = payload as Record<string, unknown>
  if (direct.error !== undefined) return String(direct.error)
  if (direct.detail !== undefined) return String(direct.detail)
  if (direct.message !== undefined) return String(direct.message)
  for (const value of Object.values(direct)) {
    if (!value || typeof value !== 'object') continue
    const nested = value as Record<string, unknown>
    if (nested.error !== undefined) return String(nested.error)
    if (nested.detail !== undefined) return String(nested.detail)
    if (nested.message !== undefined) return String(nested.message)
  }
  return fallback
}

export async function postOperation<TReq, TRes>(ctx: RouteContext, alias: string, body: TReq): Promise<TRes> {
  const response = await fetch(operationsUrl(ctx, alias), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ data: body }),
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(operationErrorMessage(payload, `HTTP ${response.status}`))
  }
  return unwrapOperationResponse<TRes>(alias, payload)
}

export interface ProfileIdentity {
  userType: string | null
  userId: string | null
}

/**
 * Lenient profile probe for auth gating. Tolerates 401/network errors by
 * returning nulls — gated rail entries then stay hidden, the safe fallback
 * for anything other than a confirmed non-anonymous response. The scene
 * re-probes on every `kdcube-auth-changed` broadcast; the boot probe is
 * never the only one.
 */
export async function fetchProfileIdentity(ctx: RouteContext): Promise<ProfileIdentity> {
  try {
    const response = await fetch(`${ctx.baseUrl}/profile`, {
      method: 'GET',
      credentials: 'include',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) return { userType: null, userId: null }
    const payload = (await response.json()) as { user_type?: string | null; user_id?: string | null }
    const userType = String(payload.user_type || '').trim().toLowerCase()
    const userId = String(payload.user_id || '').trim()
    return { userType: userType || null, userId: userId || null }
  } catch {
    return { userType: null, userId: null }
  }
}

export async function fetchProfileSessionId(ctx: RouteContext): Promise<string> {
  const response = await fetch(`${ctx.baseUrl}/profile`, {
    method: 'GET',
    credentials: 'include',
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`Unable to fetch profile for Data Bus (${response.status})`)
  }
  const payload = await response.json() as { session_id?: string | null }
  if (!payload.session_id) {
    throw new Error('Profile did not include a session id for Data Bus.')
  }
  return payload.session_id
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

export function asString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function asBool(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function asSize(value: unknown, fallback: { w: number; h: number }): { w: number; h: number } {
  const record = asRecord(value)
  const w = Number(record.w)
  const h = Number(record.h)
  if (Number.isFinite(w) && w > 0 && Number.isFinite(h) && h > 0) return { w, h }
  return fallback
}

export function normalizeExternalPanelConfig(value: unknown): SceneExternalPanelConfig | null {
  const record = asRecord(value)
  const id = asString(record.id)
  const bundleId = asString(record.bundle_id)
  const widgetAlias = asString(record.widget_alias)
  if (!id || !bundleId || !widgetAlias) return null
  const surfacesRaw = asRecord(record.surfaces)
  const surfaces: Record<string, SceneExternalPanelSurfaceConfig> = {}
  Object.entries(surfacesRaw).forEach(([surface, surfaceValue]) => {
    const surfaceRecord = asRecord(surfaceValue)
    const command = asRecord(surfaceRecord.command)
    surfaces[surface] = {
      label: asString(surfaceRecord.label) || undefined,
      expanded: typeof surfaceRecord.expanded === 'boolean' ? surfaceRecord.expanded : undefined,
      command: Object.keys(command).length ? command : undefined,
      command_from_open: asString(surfaceRecord.command_from_open) || undefined,
    }
  })
  return {
    id,
    label: asString(record.label) || id,
    title: asString(record.title) || asString(record.label) || id,
    bundle_id: bundleId,
    widget_alias: widgetAlias,
    widget_message_type: asString(record.widget_message_type) || undefined,
    service_event_type: asString(record.service_event_type) || undefined,
    service_forward_message_type: asString(record.service_forward_message_type) || undefined,
    open_message_types: Array.isArray(record.open_message_types)
      ? record.open_message_types.map(asString).filter(Boolean)
      : [],
    surfaces,
  }
}

/**
 * Built-in component defaults — the standard versatile scene. Sizes, accents
 * and drop acceptance mirror the website scene host's component config so
 * both hosts present the same surfaces the same way.
 */
export function defaultComponentSpecs(): SceneComponentSpec[] {
  return [
    {
      alias: 'pinboard',
      bundleId: '',
      widgetAlias: 'pinboard',
      title: 'Pin Board',
      accent: 'pink',
      gated: true,
      views: false,
      size: { w: 720, h: 560 },
      targetSurfaces: ['sdk.canvas.pinboard'],
      drop: { effect: 'pin', patterns: ['*'] },
      placement: 'docked',
      rail: true,
      defaultOpen: false,
      enabled: true,
      order: 10,
    },
    {
      alias: 'chat',
      bundleId: '',
      widgetAlias: CHAT_WIDGET_ALIAS,
      title: 'Chat',
      accent: 'teal',
      gated: false,
      views: true,
      size: { w: 460, h: 680 },
      targetSurfaces: ['sdk.chat.context', 'sdk.chat.conversation', 'sdk.chat.viewer'],
      drop: { effect: 'attach', patterns: ['*'] },
      placement: 'docked',
      rail: true,
      defaultOpen: false,
      enabled: true,
      order: 20,
    },
    {
      alias: 'memories',
      bundleId: MEMORY_WIDGET_BUNDLE_ID,
      widgetAlias: 'memories',
      params: { view: 'compact', compact: '1', host_controls: '1', limit: '2' },
      title: 'Memories',
      accent: 'lilac',
      gated: true,
      views: true,
      size: { w: 440, h: 520 },
      full: { w: 780, h: 680 },
      targetSurfaces: ['sdk.memory.list'],
      // A `mem:` record drop resolves through the provider and opens in the
      // memory-ITEM editor surface (owned by the memory_item component below)
      // — same routing as the website's memories drop target.
      drop: { effect: 'open', patterns: ['mem:*'], targetSurface: 'sdk.memory.viewer' },
      placement: 'floating',
      rail: true,
      defaultOpen: false,
      enabled: true,
      order: 30,
    },
    {
      alias: 'memory_item',
      bundleId: MEMORY_WIDGET_BUNDLE_ID,
      widgetAlias: 'memories',
      params: { single: '1', host_controls: '1' },
      title: 'Memory Record',
      accent: 'lilac',
      gated: true,
      views: false,
      size: { w: 460, h: 620 },
      targetSurfaces: ['sdk.memory.viewer'],
      placement: 'floating',
      rail: false,
      defaultOpen: false,
      enabled: true,
      order: 31,
    },
    {
      alias: 'usage',
      bundleId: '',
      widgetAlias: 'usage_card',
      title: 'Usage',
      accent: 'gold',
      gated: true,
      views: false,
      size: { w: 380, h: 520 },
      targetSurfaces: ['sdk.usage.card'],
      placement: 'floating',
      rail: true,
      defaultOpen: false,
      enabled: true,
      order: 60,
    },
  ]
}

function normalizeDropAccepts(value: unknown): SceneDropAccepts | undefined {
  const record = asRecord(value)
  const effect = asString(record.effect)
  if (effect !== 'attach' && effect !== 'pin' && effect !== 'open') return undefined
  const patterns = Array.isArray(record.patterns)
    ? record.patterns.map(asString).filter(Boolean)
    : []
  const targetSurface = asString(record.target_surface) || asString(record.targetSurface)
  return {
    effect,
    patterns: patterns.length ? patterns : ['*'],
    ...(targetSurface ? { targetSurface } : {}),
  }
}

function normalizePlacement(value: unknown, docked: unknown, fallback: 'docked' | 'floating'): 'docked' | 'floating' {
  const text = asString(value)
  if (text === 'docked' || text === 'floating') return text
  if (typeof docked === 'boolean') return docked ? 'docked' : 'floating'
  return fallback
}

function mergeComponentSpec(base: SceneComponentSpec | undefined, alias: string, value: unknown): SceneComponentSpec | null {
  const record = asRecord(value)
  const fallback: SceneComponentSpec = base ?? {
    alias,
    bundleId: '',
    widgetAlias: '',
    title: alias,
    accent: 'teal',
    gated: true,
    views: false,
    size: { w: 480, h: 560 },
    targetSurfaces: [],
    placement: 'floating',
    rail: true,
    defaultOpen: false,
    enabled: true,
    order: 100,
  }
  const spec: SceneComponentSpec = {
    ...fallback,
    alias,
    bundleId: asString(record.bundle_id) || asString(record.bundleId) || fallback.bundleId,
    widgetAlias: asString(record.widget_alias) || asString(record.widgetAlias) || fallback.widgetAlias,
    route: asString(record.route) || fallback.route,
    title: asString(record.title) || fallback.title,
    accent: asString(record.accent) || fallback.accent,
    gated: asBool(record.gated, fallback.gated),
    views: asBool(record.views, fallback.views),
    size: asSize(record.size, fallback.size),
    full: record.full !== undefined ? asSize(record.full, fallback.full ?? fallback.size) : fallback.full,
    targetSurfaces: Array.isArray(record.target_surfaces)
      ? record.target_surfaces.map(asString).filter(Boolean)
      : Array.isArray(record.targetSurfaces)
        ? record.targetSurfaces.map(asString).filter(Boolean)
        : fallback.targetSurfaces,
    drop: normalizeDropAccepts(record.drop) ?? fallback.drop,
    placement: normalizePlacement(record.placement, record.docked, fallback.placement),
    rail: asBool(record.rail, fallback.rail),
    defaultOpen: asBool(record.default_open ?? record.defaultOpen, fallback.defaultOpen),
    enabled: asBool(record.enabled, fallback.enabled),
    order: Number.isFinite(Number(record.order)) ? Number(record.order) : fallback.order,
  }
  if (record.params !== undefined) {
    const params: Record<string, string> = {}
    Object.entries(asRecord(record.params)).forEach(([key, paramValue]) => {
      params[key] = String(paramValue)
    })
    spec.params = params
  }
  if (!spec.widgetAlias && !spec.route) return null
  return spec
}

export function resolveComponentSpecs(configured: unknown): SceneComponentSpec[] {
  const defaults = defaultComponentSpecs()
  const byAlias = new Map(defaults.map((spec) => [spec.alias, spec]))
  Object.entries(asRecord(configured)).forEach(([alias, value]) => {
    const merged = mergeComponentSpec(byAlias.get(alias), alias, value)
    if (merged) byAlias.set(alias, merged)
    else byAlias.delete(alias)
  })
  return Array.from(byAlias.values())
    .filter((spec) => spec.enabled)
    .sort((a, b) => a.order - b.order)
}

export async function loadSceneConfig(ctx: RouteContext): Promise<SceneConfig> {
  try {
    const payload = await postOperation<Record<string, never>, {
      ok?: boolean
      components?: unknown
      external_panels?: unknown[]
      namespace_styles?: unknown
      namespaceStyles?: unknown
    }>(ctx, 'scene_surface_config', {})
    const externalPanels = Array.isArray(payload?.external_panels)
      ? payload.external_panels.map(normalizeExternalPanelConfig).filter((panel): panel is SceneExternalPanelConfig => Boolean(panel))
      : []
    const namespaceStyles = asRecord(payload?.namespace_styles ?? payload?.namespaceStyles)
    return {
      components: resolveComponentSpecs(payload?.components),
      external_panels: externalPanels,
      namespaceStyles,
    }
  } catch (error) {
    console.warn('[versatile-scene] scene surface config unavailable', error)
    return { components: resolveComponentSpecs(undefined), external_panels: [], namespaceStyles: {} }
  }
}
