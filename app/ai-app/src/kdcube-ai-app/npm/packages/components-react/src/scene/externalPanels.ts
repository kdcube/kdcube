/**
 * External-panel surface routing — how a scene host executes the
 * `surfaces` block of an external panel config.
 *
 * Every open resolved toward a panel surface (a provider `object.action(open)`
 * response carrying `ui_event.target_surface`, or a widget-posted
 * `kdcube.surface.command`) flows through a surface registration built here:
 *
 *   - `ensureOpen` summons the panel window and applies the surface's
 *     `expanded` state;
 *   - `commandFromOpen` derives the widget command from the surface
 *     descriptor: `command_from_open: 'provider_surface_open'` forwards the
 *     provider's open payload (merged over the static `command` base), a
 *     bare `command` posts that static command, and a surface with neither
 *     forwards the provider payload;
 *   - `postCommand` delivers the command in the PANEL WIDGET's own message
 *     vocabulary: when the panel declares `widget_message_type`, the command
 *     is posted with that `type` (plus `widget: <widget_alias>`), so the
 *     widget recognizes it as a host command. Without it the scene-level
 *     `kdcube.surface.command` type is kept.
 */

import {
  SCENE_SURFACE_COMMAND,
  providerSurfaceCommandFromOpen,
  type SceneRecord,
  type SceneSurfaceOpenRequest,
  type SceneSurfaceRegistration,
} from '@kdcube/components-core/scene'
import {
  asString,
  type SceneExternalPanelConfig,
  type SceneExternalPanelSurfaceConfig,
} from './registry'

/** How the host materializes panel-surface effects (window + frame I/O). */
export interface SceneExternalPanelHostHooks {
  /** Summon the panel window; `expanded` comes from the surface descriptor. */
  ensureOpen: (
    targetSurface: string,
    surface: SceneExternalPanelSurfaceConfig,
    request?: SceneSurfaceOpenRequest,
  ) => void
  /** Post a message into the panel's widget frame. False = not mounted yet. */
  postToPanel: (message: SceneRecord) => boolean
  /** Whether the panel frame can receive commands (default: post and see). */
  isReady?: () => boolean
}

/**
 * Translate a queued surface command into the panel widget's message
 * vocabulary. The scene runtime stamps commands with the scene-level
 * `kdcube.surface.command` type; a panel that declares `widget_message_type`
 * expects host commands under THAT type — without this translation the
 * widget ignores the command and the open lands on its default view.
 */
export function externalPanelWidgetMessage(
  panel: SceneExternalPanelConfig,
  command: SceneRecord,
): SceneRecord {
  const message: SceneRecord = { ...command }
  message.type = asString(panel.widget_message_type) || asString(command.type) || SCENE_SURFACE_COMMAND
  if (message.widget === undefined && panel.widget_alias) message.widget = panel.widget_alias
  return message
}

/**
 * Derive the command a provider-open resolves to for one panel surface,
 * honoring the surface descriptor (`command_from_open` / `command`).
 */
export function externalPanelCommandFromOpen(
  panel: SceneExternalPanelConfig,
  targetSurface: string,
  surface: SceneExternalPanelSurfaceConfig,
  request: SceneSurfaceOpenRequest,
): SceneRecord | null {
  const mode = asString(surface.command_from_open)
  if (mode === 'provider_surface_open' || (!mode && !surface.command)) {
    const providerCommand = providerSurfaceCommandFromOpen(request)
    if (providerCommand) {
      return { ...(surface.command || {}), ...providerCommand, target_surface: targetSurface }
    }
    return surface.command ? { ...surface.command, target_surface: targetSurface } : null
  }
  if (surface.command) return { ...surface.command, target_surface: targetSurface }
  return null
}

/**
 * Build the surface registrations for an external panel — one per entry in
 * its `surfaces` config — ready for `SceneRuntime.registerSurface`. Both
 * provider-open dispatches and direct widget-posted surface commands then
 * summon the panel, honor `expanded`, and deliver the command through
 * `externalPanelWidgetMessage`.
 */
export function externalPanelSurfaceRegistrations(
  panel: SceneExternalPanelConfig,
  hooks: SceneExternalPanelHostHooks,
): Record<string, SceneSurfaceRegistration> {
  const registrations: Record<string, SceneSurfaceRegistration> = {}
  Object.entries(panel.surfaces || {}).forEach(([targetSurface, surface]) => {
    registrations[targetSurface] = {
      label: surface.label || panel.label,
      ensureOpen: (request) => hooks.ensureOpen(targetSurface, surface, request),
      ...(hooks.isReady ? { isReady: () => hooks.isReady!() } : {}),
      postCommand: (command) => hooks.postToPanel(externalPanelWidgetMessage(panel, command)),
      commandFromOpen: (request) => externalPanelCommandFromOpen(panel, targetSurface, surface, request),
    }
  })
  return registrations
}
