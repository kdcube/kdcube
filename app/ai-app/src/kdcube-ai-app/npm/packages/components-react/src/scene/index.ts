/**
 * @kdcube/components-react/scene — the reusable scene-host shell.
 *
 * Build "a scene like ours with fully different content" from config +
 * content alone; the runtime/event-bus/drag-broker layer stays in
 * `@kdcube/components-core/scene`. Import `sceneHost.css` next to this
 * module for the skin.
 *
 * Public API index:
 *
 * Component registry (registry.ts — pure):
 *   SceneComponentSpec, SceneDropAccepts,
 *   SceneExternalPanelConfig, SceneExternalPanelSurfaceConfig,
 *   resolveComponentSpecs(configured, defaults) — merge a delivered
 *     `components` map over host-owned defaults,
 *   normalizeExternalPanelConfig, normalizeDropAccepts, asRecord, asString.
 *
 * External-panel surface routing (externalPanels.ts — pure):
 *   externalPanelSurfaceRegistrations(panel, hooks) — build the
 *     SceneRuntime registrations for a panel's `surfaces` config
 *     (summon + `expanded`, `command` / `command_from_open` semantics,
 *     `widget_message_type` delivery),
 *   externalPanelCommandFromOpen, externalPanelWidgetMessage,
 *   SceneExternalPanelHostHooks.
 *
 * Host plumbing (host.ts — pure):
 *   SceneRouteContext, sceneRouteContext, sceneContextFromConfig,
 *   requestSceneRuntimeConfig (ask an embedding host),
 *   buildChildConfigResponse (answer child widget frames when top),
 *   appBaseUrl, widgetUrlForBundle, componentWidgetUrl, operationsUrl,
 *   postSceneOperation, unwrapOperationResponse, operationErrorMessage,
 *   fetchSceneProfileIdentity / fetchSceneProfileSessionId (event-driven
 *     auth-gate probes — re-probe on every `kdcube-auth-changed`).
 *
 * Windows + rail (windows.tsx — React):
 *   useWindowManager, WindowManager, WindowState, WindowSizing,
 *   FloatingWindow, Rail, RailEntry, WINDOW_BAR_HEIGHT,
 *   setViewportBottomClip (feed from the visible-viewport probe),
 *   rectsIntersect, windowRectFromState, buriedAliases (raise-veil overlap).
 */
export * from './registry'
export * from './externalPanels'
export * from './host'
export * from './windows'
