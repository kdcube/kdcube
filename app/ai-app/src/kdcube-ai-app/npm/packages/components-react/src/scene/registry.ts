/**
 * Scene component registry — the config model a scene host is composed from.
 *
 * A component is an iframe-mounted served widget from its owning app:
 * `bundleId` + `widgetAlias` (or an explicit `route`) select what to mount;
 * title/accent/size/order drive the rail and window chrome; `placement`
 * selects docked-tile vs summonable-floating behavior; `drop` declares
 * context-drop acceptance for the host drag overlay.
 *
 * The registry is content-free: hosts pass their OWN defaults into
 * `resolveComponentSpecs(configured, defaults)` and a server- or
 * browser-delivered `components` map overrides them by alias
 * (`enabled: false` removes an entry; unknown aliases add new components).
 */

import {
  normalizeSceneContextDropOpenRoute,
  type SceneContextDropOpenRoute,
} from '@kdcube/components-core/scene'

export interface SceneDropAccepts {
  effect: 'attach' | 'pin' | 'open'
  patterns: string[]
  /**
   * Surface an `open` drop resolves toward. It may belong to ANOTHER
   * component (e.g. a list component accepts drops that open in a separate
   * editor component's surface).
   */
  targetSurface?: string
  /**
   * Kind-aware exception route (see `SceneContextDropOpenRoute`): refs that
   * match it resolve through the provider `open` pipeline toward its
   * `targetSurface` instead of this target's blanket `effect` — e.g. a
   * conversation pin dropped on chat opens that conversation while every
   * other kind keeps attach-as-context.
   */
  open?: SceneContextDropOpenRoute
}

export interface SceneComponentSpec {
  alias: string
  /** Owning app package; '' means the scene's own app. */
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
   * `docked` components have a static home on the scene stage and pin/unpin
   * into a floating window; `floating` components are summoned from the rail.
   */
  placement: 'docked' | 'floating'
  /** Rendered as a rail button. Surface-command-only components opt out. */
  rail: boolean
  defaultOpen: boolean
  enabled: boolean
  order: number
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

export function normalizeDropAccepts(value: unknown): SceneDropAccepts | undefined {
  const record = asRecord(value)
  const effect = asString(record.effect)
  if (effect !== 'attach' && effect !== 'pin' && effect !== 'open') return undefined
  const patterns = Array.isArray(record.patterns)
    ? record.patterns.map(asString).filter(Boolean)
    : []
  const targetSurface = asString(record.target_surface) || asString(record.targetSurface)
  const openRoute = normalizeSceneContextDropOpenRoute(record.open)
  return {
    effect,
    patterns: patterns.length ? patterns : ['*'],
    ...(targetSurface ? { targetSurface } : {}),
    ...(openRoute ? { open: openRoute } : {}),
  }
}

function normalizePlacement(value: unknown, docked: unknown, fallback: 'docked' | 'floating'): 'docked' | 'floating' {
  const text = asString(value)
  if (text === 'docked' || text === 'floating') return text
  if (typeof docked === 'boolean') return docked ? 'docked' : 'floating'
  return fallback
}

function mergeComponentSpec(
  base: SceneComponentSpec | undefined,
  alias: string,
  value: unknown,
): SceneComponentSpec | null {
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

/**
 * Merge a configured `components` map over the host's defaults: entries
 * override defaults by alias, `enabled: false` removes, new aliases add.
 * Returns the enabled specs sorted by `order`.
 */
export function resolveComponentSpecs(
  configured: unknown,
  defaults: SceneComponentSpec[] = [],
): SceneComponentSpec[] {
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
