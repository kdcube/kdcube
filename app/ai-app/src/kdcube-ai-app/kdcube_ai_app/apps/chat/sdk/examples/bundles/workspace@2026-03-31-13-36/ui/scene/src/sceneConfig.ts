/**
 * Workspace scene content: what THIS app composes.
 *
 * The reusable scene-host mechanics (component registry model, route/runtime
 * context, widget URLs, operations client, profile probes, CONFIG handshake)
 * come from `@kdcube/components-react/scene`; this module keeps only the
 * workspace-specific pieces — identities, the chat widget's host-embed
 * params, the built-in component defaults, and the `scene_surface_config`
 * loader — and re-exports the package plumbing under the names the scene
 * host uses.
 */

import {
  asRecord,
  fetchSceneProfileIdentity,
  fetchSceneProfileSessionId,
  normalizeExternalPanelConfig,
  postSceneOperation,
  requestSceneRuntimeConfig,
  resolveComponentSpecs as resolveComponentSpecsWithDefaults,
  sceneContextFromConfig,
  sceneRouteContext,
  widgetUrlForBundle,
  type SceneComponentSpec,
  type SceneExternalPanelConfig,
  type SceneProfileIdentity,
  type SceneRouteContext,
  type SceneRuntimeConfigPayload,
} from '@kdcube/components-react/scene'

export const BUNDLE_ID = 'workspace@2026-03-31-13-36'
export const CONFIG_IDENTITY = 'BUNDLE_WORKSPACE_MAIN_VIEW'
export const CHAT_CONFIG_IDENTITY = 'BUNDLE_WORKSPACE_CHAT_VIEW'
export const CHAT_WIDGET_ALIAS = 'workspace_chat'
export const MEMORY_WIDGET_BUNDLE_ID = 'user-memories@2026-06-26'
export const CONNECTION_HUB_BUNDLE_ID = 'connection-hub@1-0'

// The scene host's vocabulary stays stable; the shapes are the package's.
export type RouteContext = SceneRouteContext
export type RuntimeConfig = SceneRuntimeConfigPayload
export type ProfileIdentity = SceneProfileIdentity
export {
  appBaseUrl,
  asRecord,
  asString,
  buildChildConfigResponse,
  componentWidgetUrl,
  normalizeExternalPanelConfig,
  operationErrorMessage,
  operationsUrl,
  unwrapOperationResponse,
  widgetUrlForBundle,
  type SceneComponentSpec,
  type SceneDropAccepts,
  type SceneExternalPanelConfig,
  type SceneExternalPanelSurfaceConfig,
} from '@kdcube/components-react/scene'
export const postOperation = postSceneOperation
export const fetchProfileIdentity = fetchSceneProfileIdentity
export const fetchProfileSessionId = fetchSceneProfileSessionId

export function routeContext(): RouteContext {
  return sceneRouteContext(BUNDLE_ID, { tenant: 'demo-tenant', project: 'demo-project' })
}

export function contextFromConfig(config: RuntimeConfig | null, fallback: RouteContext): RouteContext {
  return sceneContextFromConfig(config, fallback)
}

export function requestRuntimeConfig(): Promise<RuntimeConfig | null> {
  return requestSceneRuntimeConfig(CONFIG_IDENTITY)
}

export interface SceneConfig {
  components: SceneComponentSpec[]
  external_panels: SceneExternalPanelConfig[]
  namespaceStyles: Record<string, unknown>
}

export function chatWidgetParams(ctx: RouteContext): Record<string, string> {
  return {
    chat_embed_mode: 'host',
    chat_widget_id: CHAT_WIDGET_ALIAS,
    chat_config_identity: CHAT_CONFIG_IDENTITY,
    chat_brand_label: 'Workspace',
    chat_event_prefix: 'workspace',
    chat_surface: 'workspace_chat',
    chat_user_event_source_id: 'workspace.main.chat.user',
    chat_attachment_event_source_id: 'workspace.main.chat.attachment',
    chat_context_event_source_id: 'workspace.context.focus',
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

/**
 * Built-in component defaults — the standard workspace scene. Sizes, accents
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
    {
      // Connection Hub settings widget — the `connections.hub.open` surface
      // contract lands here (declared via targetSurfaces, exactly like the
      // website scene's contract for the same widget). Chat consent cards
      // summon it with a `kdcube.surface.command` whose ui_event carries
      // tab/provider/tiers/account_id.
      alias: 'connection_hub',
      bundleId: CONNECTION_HUB_BUNDLE_ID,
      widgetAlias: 'connections_settings',
      params: { tab: 'delegated_to_kdcube' },
      title: 'Connection Hub',
      accent: 'purple',
      gated: true,
      views: false,
      size: { w: 760, h: 640 },
      full: { w: 1040, h: 760 },
      targetSurfaces: ['connection_hub.connections', 'connection_hub.settings'],
      placement: 'floating',
      rail: true,
      defaultOpen: false,
      enabled: true,
      order: 70,
    },
  ]
}

/** Configured `components` merged over the workspace defaults. */
export function resolveComponentSpecs(configured: unknown): SceneComponentSpec[] {
  return resolveComponentSpecsWithDefaults(configured, defaultComponentSpecs())
}

export async function loadSceneConfig(ctx: RouteContext): Promise<SceneConfig> {
  try {
    const payload = await postSceneOperation<Record<string, never>, {
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
    console.warn('[workspace-scene] scene surface config unavailable', error)
    return { components: resolveComponentSpecs(undefined), external_panels: [], namespaceStyles: {} }
  }
}
