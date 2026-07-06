/**
 * Versatile scene — a config-driven scene host with the same look and
 * behavior as the website scene host.
 *
 * Every component is an iframe-mounted served widget from its owning app
 * (pin board = this bundle's `pinboard` widget, chat = `versatile_chat`,
 * memories = the user-memories app, usage = `usage_card`, tasks = the
 * configured external panel; more via `scene_surface_config`). The scene
 * itself only composes: right-edge summon rail, floating windows, the
 * CONFIG_REQUEST/CONFIG_RESPONSE handshake, surface-command routing,
 * scene event relay, and the cross-surface context drag overlay.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  SCENE_SURFACE_COMMAND,
  SCENE_SUBSCRIBE_MESSAGE,
  SCENE_UNSUBSCRIBE_MESSAGE,
  createContextDragBroker,
  createSceneEventBus,
  createSceneRuntime,
  providerSurfaceCommandFromOpen,
  normalizeSceneContext,
  type SceneContextItem,
  type SceneDropTarget,
  type SceneEventBus,
  type SceneEventSubscriptionClaim,
  type SceneRecord,
  type SceneSurfaceRegistration,
} from '@kdcube/components-core/scene'
import {
  asRecord,
  asString,
  chatWidgetParams,
  componentWidgetUrl,
  contextFromConfig,
  externalWidgetUrl,
  fetchProfileIdentity,
  loadSceneConfig,
  postOperation,
  requestRuntimeConfig,
  resolveComponentSpecs,
  routeContext,
  CHAT_WIDGET_ALIAS,
  type ProfileIdentity,
  type RouteContext,
  type SceneComponentSpec,
  type SceneConfig,
  type SceneExternalPanelConfig,
} from './sceneConfig'
import { dataBusSocketFor, ensureSocketConnected } from './dataBus'
import { FloatingWindow, Rail, useWindowManager, type RailEntry, type WindowSizing } from './windows'
import { componentIcon, TasksIcon } from './icons'
import './styles.css'

const EXTERNAL_ALIAS_PREFIX = 'external:'
const INGRESS_MESSAGE_TYPE = 'kdcube.canvas.ingress'
const INGRESS_DRAG_START = 'kdcube-canvas-ingress-drag-start'
const INGRESS_DRAG_END = 'kdcube-canvas-ingress-drag-end'

interface ActiveDragView {
  sourceAlias: string
  sourceSurfaceRef: string
  context: SceneContextItem
}

function sceneSubscriptionChannels(subscriptions: Record<string, SceneEventSubscriptionClaim[]>): string[] {
  const seen = new Set<string>()
  Object.values(subscriptions).forEach((claims) => {
    claims.forEach((claim) => {
      ;(claim.channels ?? []).forEach((channel) => {
        const value = String(channel || '').trim()
        if (value) seen.add(value)
      })
    })
  })
  return Array.from(seen)
}

function firstExternalTargetSurface(panel: SceneExternalPanelConfig | null): string {
  return Object.keys(panel?.surfaces || {})[0] || ''
}

function windowSizing(spec: SceneComponentSpec): WindowSizing {
  return { size: { w: spec.size.w, h: spec.size.h }, full: spec.full }
}

function externalSizing(): WindowSizing {
  return { size: { w: 410, h: 540 }, full: { w: 860, h: 700 } }
}

function hintFor(bundleId: string, widgetAlias: string, sceneBundle: string): string {
  return `app ${bundleId || sceneBundle} · ${widgetAlias} component`
}

function App() {
  const fallback = useMemo(() => routeContext(), [])
  const [ctx, setCtx] = useState<RouteContext>(fallback)
  const [ready, setReady] = useState(false)
  const [sceneConfig, setSceneConfig] = useState<SceneConfig>({
    components: [],
    external_panels: [],
    namespaceStyles: {},
  })
  const [configLoaded, setConfigLoaded] = useState(false)
  const [identity, setIdentity] = useState<ProfileIdentity>({ userType: null, userId: null })
  const [notice, setNotice] = useState('')
  const [memoryCount, setMemoryCount] = useState<number | null>(null)
  const [activeDrag, setActiveDrag] = useState<ActiveDragView | null>(null)
  const manager = useWindowManager()
  const managerRef = useRef(manager)
  managerRef.current = manager
  const frameRefs = useRef<Record<string, HTMLIFrameElement | null>>({})
  /** Window/tile section elements — used to promote a docked tile in place. */
  const tileRefs = useRef<Record<string, HTMLElement | null>>({})
  /** Per-component readiness reported via kdcube-memory-widget-status. */
  const statusReadyRef = useRef<Record<string, boolean>>({})
  const sceneConfigRef = useRef(sceneConfig)
  sceneConfigRef.current = sceneConfig
  const ctxRef = useRef(ctx)
  ctxRef.current = ctx
  const [sceneSubscriptions, setSceneSubscriptions] = useState<Record<string, SceneEventSubscriptionClaim[]>>({})

  const isRegistered = identity.userType != null && identity.userType !== 'anonymous'
  const externalPanel = sceneConfig.external_panels[0] ?? null
  const externalAlias = externalPanel ? `${EXTERNAL_ALIAS_PREFIX}${externalPanel.id}` : ''
  const components = configLoaded ? sceneConfig.components : resolveComponentSpecs(undefined)
  const specByAlias = useMemo(() => new Map(components.map((spec) => [spec.alias, spec])), [components])

  const sceneRuntime = useMemo(() => createSceneRuntime({ logger: console }), [])

  const aliasForSource = useCallback((source: MessageEventSource | null): string => {
    for (const [alias, frame] of Object.entries(frameRefs.current)) {
      if (frame && frame.contentWindow === source) return alias
    }
    return ''
  }, [])

  const postToFrame = useCallback((alias: string, message: Record<string, unknown>): boolean => {
    const target = frameRefs.current[alias]?.contentWindow
    if (!target) return false
    target.postMessage(message, '*')
    return true
  }, [])

  /** Component alias for a widget alias named in widget-level messages. */
  const aliasForWidget = useCallback((widget: string): string => {
    if (!widget) return ''
    if (externalPanel && widget === externalPanel.widget_alias) return externalAlias
    for (const spec of components) {
      if (spec.widgetAlias === widget || spec.alias === widget) return spec.alias
    }
    return ''
  }, [components, externalAlias, externalPanel])

  const syncWidgetView = useCallback((alias: string, view: 'compact' | 'expanded') => {
    if (alias === 'chat') {
      postToFrame(alias, { type: 'kdcube-set-view', view })
      return
    }
    if (externalPanel && alias === externalAlias) {
      postToFrame(alias, { type: 'kdcube-set-view', widget: externalPanel.widget_alias, view })
      return
    }
    const spec = specByAlias.get(alias)
    postToFrame(alias, { type: 'kdcube-set-view', widget: spec?.widgetAlias || alias, view })
  }, [externalAlias, externalPanel, postToFrame, specByAlias])

  /** Promote a docked tile into a floating window from its own rect. */
  const promoteComponent = useCallback((alias: string) => {
    const tile = tileRefs.current[alias]
    const rect = tile?.getBoundingClientRect()
    managerRef.current.promote(alias, rect ? { x: rect.left, y: rect.top, w: rect.width, h: rect.height } : undefined)
  }, [])

  const dockComponent = useCallback((alias: string) => {
    managerRef.current.dock(alias)
    const spec = specByAlias.get(alias)
    if (spec?.views) window.setTimeout(() => syncWidgetView(alias, 'compact'), 0)
  }, [specByAlias, syncWidgetView])

  const openComponent = useCallback((alias: string, options: { expanded?: boolean } = {}) => {
    const mgr = managerRef.current
    if (alias === externalAlias && externalPanel) {
      mgr.open(alias, externalSizing())
      if (options.expanded !== undefined) {
        mgr.setExpanded(alias, externalSizing(), options.expanded)
        window.setTimeout(() => syncWidgetView(alias, options.expanded ? 'expanded' : 'compact'), 0)
      }
      return
    }
    const spec = specByAlias.get(alias)
    if (!spec) return
    if (spec.placement === 'docked') {
      // A docked component is already present on the stage. An expand request
      // promotes it (website: expand floats + maximizes the tile); a plain
      // open just makes sure it is visible and in front.
      mgr.ensureDocked(alias)
      if (options.expanded) {
        promoteComponent(alias)
        mgr.maximize(alias)
        if (spec.views) window.setTimeout(() => syncWidgetView(alias, 'expanded'), 0)
      } else if (options.expanded === false) {
        dockComponent(alias)
      } else {
        mgr.front(alias)
      }
      return
    }
    mgr.open(alias, windowSizing(spec))
    if (options.expanded !== undefined && spec.views) {
      mgr.setExpanded(alias, windowSizing(spec), options.expanded)
      window.setTimeout(() => syncWidgetView(alias, options.expanded ? 'expanded' : 'compact'), 0)
    }
  }, [dockComponent, externalAlias, externalPanel, promoteComponent, specByAlias, syncWidgetView])

  // ---------------------------------------------------------------- boot
  useEffect(() => {
    requestRuntimeConfig()
      .then((config) => {
        setCtx(contextFromConfig(config, fallback))
        setReady(true)
      })
      .catch(() => setReady(true))
  }, [fallback])

  useEffect(() => {
    if (!ready) return
    let cancelled = false
    void loadSceneConfig(ctx).then((config) => {
      if (cancelled) return
      setSceneConfig(config)
      setConfigLoaded(true)
    })
    return () => {
      cancelled = true
    }
  }, [ctx, ready])

  // ---------------------------------------------------------------- auth
  // Event-driven: probe /profile at boot AND re-probe whenever the host
  // broadcasts an auth change (DOM event when the scene is the top window,
  // postMessage when it is embedded). Never a one-time mount snapshot.
  const probeIdentity = useCallback((reason: string) => {
    void fetchProfileIdentity(ctxRef.current).then((next) => {
      console.info('[versatile-scene] auth probe', { reason, userType: next.userType })
      setIdentity(next)
    })
  }, [])

  useEffect(() => {
    if (!ready) return
    probeIdentity('boot')
  }, [probeIdentity, ready])

  useEffect(() => {
    const onAuthChanged = () => probeIdentity('kdcube-auth-changed')
    window.addEventListener('kdcube-auth-changed', onAuthChanged)
    return () => window.removeEventListener('kdcube-auth-changed', onAuthChanged)
  }, [probeIdentity])

  // Component presence: docked components live on the stage whenever their
  // gate allows (the website's docked tiles); floating `default_open`
  // components are summoned once their gate allows. Signing out closes every
  // gated window (they stay mounted but hidden).
  const autoOpenedRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!ready || !configLoaded) return
    components.forEach((spec) => {
      if (spec.gated && !isRegistered) return
      if (spec.placement === 'docked') {
        managerRef.current.ensureDocked(spec.alias)
      } else if (spec.defaultOpen && !autoOpenedRef.current.has(spec.alias)) {
        autoOpenedRef.current.add(spec.alias)
        managerRef.current.open(spec.alias, windowSizing(spec))
      }
    })
    if (!isRegistered) {
      components.filter((spec) => spec.gated).forEach((spec) => managerRef.current.close(spec.alias))
      if (externalAlias) managerRef.current.close(externalAlias)
    }
  }, [components, configLoaded, externalAlias, isRegistered, ready])

  // -------------------------------------------------------- scene runtime
  const surfaceRegistry = useMemo<Record<string, SceneSurfaceRegistration>>(() => {
    const registry: Record<string, SceneSurfaceRegistration> = {}
    components.forEach((spec) => {
      spec.targetSurfaces.forEach((targetSurface) => {
        registry[targetSurface] = {
          label: spec.title,
          ensureOpen: () => openComponent(spec.alias),
          isReady: () => {
            if (targetSurface.startsWith('sdk.memory.')) return Boolean(statusReadyRef.current[spec.alias])
            return Boolean(frameRefs.current[spec.alias]?.contentWindow)
          },
          postCommand: (command) => postToFrame(spec.alias, command as Record<string, unknown>),
          commandFromOpen: providerSurfaceCommandFromOpen,
        }
      })
    })
    if (externalPanel) {
      Object.entries(externalPanel.surfaces || {}).forEach(([targetSurface, surface]) => {
        registry[targetSurface] = {
          label: surface.label || externalPanel.label,
          ensureOpen: () => openComponent(externalAlias, { expanded: Boolean(surface.expanded) }),
          isReady: () => Boolean(frameRefs.current[externalAlias]?.contentWindow),
          postCommand: (command) => postToFrame(externalAlias, command as Record<string, unknown>),
          commandFromOpen: (request) => {
            const providerCommand = providerSurfaceCommandFromOpen(request)
            if (providerCommand) return { ...(surface.command || {}), ...providerCommand, target_surface: targetSurface }
            if (surface.command) return { ...surface.command }
            return null
          },
        }
      })
    }
    return registry
  }, [components, externalAlias, externalPanel, openComponent, postToFrame])

  useEffect(() => {
    const unregister = Object.entries(surfaceRegistry).map(([targetSurface, registration]) =>
      sceneRuntime.registerSurface(targetSurface, registration),
    )
    return () => unregister.forEach((dispose) => dispose())
  }, [sceneRuntime, surfaceRegistry])

  // ------------------------------------------------------ scene event bus
  const sceneEventBus = useMemo<SceneEventBus>(() => createSceneEventBus({
    getAliases: () => [],
    isReady: (alias) => Boolean(frameRefs.current[alias]?.contentWindow),
    post: (alias, message, event, subscription) => {
      if (!postToFrame(alias, message as Record<string, unknown>)) {
        console.info('[kdc-scene] scene event target not mounted', {
          alias,
          subscription: subscription.id,
          type: event.type,
          channel: event.channel,
        })
      }
    },
    queue: (alias, _message, event, subscription) => {
      console.info('[kdc-scene] scene event queued target unavailable', {
        alias,
        subscription: subscription.id,
        type: event.type,
        channel: event.channel,
      })
    },
    logger: console,
  }), [postToFrame])

  // Scene-level event relay: configured subscription claims decide which
  // Data Bus channels the scene consumes on behalf of its widgets.
  useEffect(() => {
    if (!isRegistered) return undefined
    const channels = sceneSubscriptionChannels(sceneSubscriptions)
    if (!channels.length) return undefined
    let cancelled = false
    const detach: Array<() => void> = []
    void (async () => {
      try {
        console.info('[kdc-scene] scene event relay subscribing', { tenant: ctx.tenant, project: ctx.project, channels })
        const socket = await dataBusSocketFor(ctx)
        await ensureSocketConnected(socket)
        if (cancelled) return
        channels.forEach((channel) => {
          const onEvent = (payload: unknown) => {
            const event = sceneEventBus.normalizeEvent('sse', { type: channel }, payload)
            sceneEventBus.publish(event)
          }
          socket.on(channel, onEvent)
          detach.push(() => socket.off(channel, onEvent))
        })
      } catch (err) {
        console.warn('[kdc-scene] scene event relay subscribe failed', err)
      }
    })()
    return () => {
      cancelled = true
      detach.forEach((release) => release())
    }
  }, [ctx, isRegistered, sceneEventBus, sceneSubscriptions])

  // External panels may claim one project service event; the scene forwards
  // only that configured type to the mounted widget.
  const externalOpen = externalAlias ? manager.isOpen(externalAlias) : false
  useEffect(() => {
    if (!isRegistered || !externalOpen || !externalPanel?.service_event_type || !externalPanel?.service_forward_message_type) return undefined
    let cancelled = false
    let detach: (() => void) | undefined
    void (async () => {
      try {
        const socket = await dataBusSocketFor(ctx)
        await ensureSocketConnected(socket)
        if (cancelled) return
        const onService = (payload: unknown) => {
          const env = (payload ?? {}) as { type?: string; data?: Record<string, unknown> }
          if (env.type !== externalPanel.service_event_type) return
          postToFrame(externalAlias, {
            type: externalPanel.service_forward_message_type as string,
            data: env.data ?? {},
          })
        }
        socket.on('chat_service', onService)
        detach = () => socket.off('chat_service', onService)
      } catch {
        // The configured widget can still refresh manually or when opened.
      }
    })()
    return () => {
      cancelled = true
      if (detach) detach()
    }
  }, [ctx, externalAlias, externalOpen, externalPanel, isRegistered, postToFrame])

  // ------------------------------------------------------ context drag
  const contextDragBroker = useMemo(() => createContextDragBroker({
    logger: console,
    objectAction: async (request) => {
      const response = await postOperation<Record<string, unknown>, SceneRecord>(ctxRef.current, 'canvas_object_action', {
        action: 'open',
        object_ref: request.object_ref,
        mime: (request.context as SceneContextItem | undefined)?.mime,
        ...(request.target_surface ? { target_surface: request.target_surface } : {}),
      })
      if (request.target_surface) {
        response.ui_event = {
          ...asRecord(response.ui_event),
          target_surface: request.target_surface,
        }
      }
      return response
    },
    dispatchOpenResponse: (response, source) => sceneRuntime.dispatchSurfaceOpen(response, source),
  }), [sceneRuntime])

  const beginContextDrag = useCallback((message: Record<string, unknown>, sourceAlias: string) => {
    const active = contextDragBroker.handleDragStart({
      type: 'kdcube-context-drag-start',
      source_surface_ref: asString(message.source_surface_ref) || asString(message.sourceSurfaceRef) || asString(message.source),
      contexts: Array.isArray(message.contexts) ? message.contexts : message.context ? [message.context] : [],
    })
    const context = active?.contexts[0] ?? null
    if (context) {
      // The drag SOURCE iframe must keep pointer events while every other
      // iframe is suppressed (`body.kdc-ctxdrag`): an in-board card
      // reposition is a native drag that has to drop back INSIDE the source
      // frame — without this tag hit-testing skips the iframe and the drop
      // lands on the scene, so cards can never be moved. Same contract as
      // the website host's iframe.kdc-drag-source.
      const sourceFrame = frameRefs.current[sourceAlias]
      sourceFrame?.classList.add('kdc-drag-source')
      console.info('[kdc-scene:drag] context drag start', {
        sourceAlias,
        ref: context.ref || '',
        source_tagged: Boolean(sourceFrame),
      })
      setActiveDrag({ sourceAlias, sourceSurfaceRef: active?.sourceSurfaceRef || '', context })
    }
  }, [contextDragBroker])

  const endContextDrag = useCallback(() => {
    contextDragBroker.handleDragEnd()
    document.querySelectorAll('iframe.kdc-drag-source').forEach((frame) => frame.classList.remove('kdc-drag-source'))
    console.info('[kdc-scene:drag] context drag end')
    setActiveDrag(null)
  }, [contextDragBroker])

  useEffect(() => {
    document.body.classList.toggle('kdc-ctxdrag', Boolean(activeDrag))
    return () => document.body.classList.remove('kdc-ctxdrag')
  }, [activeDrag])

  const dropTargetForSpec = useCallback((spec: SceneComponentSpec): SceneDropTarget | null => {
    if (!spec.drop) return null
    // An `open` drop may resolve toward a surface owned by ANOTHER component
    // (memories list → memory-item editor), configured on the drop itself —
    // same routing as the website's contextDropTargets.
    const targetSurface = spec.drop.targetSurface ||
      (spec.drop.effect === 'open'
        ? spec.targetSurfaces.find((surface) => surface.endsWith('.viewer')) || spec.targetSurfaces[0] || ''
        : spec.targetSurfaces[0] || '')
    return {
      surfaceRef: `versatile.${spec.alias}`,
      targetSurface,
      dropEffect: spec.drop.effect,
      accepts: { [spec.drop.effect]: spec.drop.patterns },
      label: spec.title,
    }
  }, [])

  const externalDropTarget = useMemo<SceneDropTarget | null>(() => {
    const targetSurface = firstExternalTargetSurface(externalPanel)
    if (!targetSurface) return null
    return {
      surfaceRef: externalPanel?.id || 'versatile.external',
      targetSurface,
      dropEffect: 'open',
      label: externalPanel?.label || 'external surface',
    }
  }, [externalPanel])

  const deliverDrop = useCallback((alias: string, event: React.DragEvent<HTMLElement>) => {
    const drag = activeDrag
    if (!drag) return
    event.preventDefault()
    event.stopPropagation()
    const finish = () => endContextDrag()
    if (alias === externalAlias && externalDropTarget) {
      void contextDragBroker.dropOnTarget(externalDropTarget).then((result) => setNotice(result.message)).finally(finish)
      return
    }
    const spec = specByAlias.get(alias)
    const target = spec ? dropTargetForSpec(spec) : null
    if (!spec || !target) {
      finish()
      return
    }
    if (spec.drop?.effect === 'attach') {
      const result = sceneRuntime.queueSurfaceCommand(target.targetSurface ?? '', {
        action: 'attach',
        context: drag.context,
      })
      setNotice(result.message)
      finish()
      return
    }
    if (spec.drop?.effect === 'pin') {
      const frame = frameRefs.current[alias]
      const rect = frame?.getBoundingClientRect()
      const result = sceneRuntime.queueSurfaceCommand(target.targetSurface ?? '', {
        action: 'pin',
        context: drag.context,
        x: rect ? Math.max(16, event.clientX - rect.left) : 64,
        y: rect ? Math.max(16, event.clientY - rect.top) : 64,
      })
      setNotice(result.message)
      finish()
      return
    }
    void contextDragBroker.dropOnTarget(target).then((result) => setNotice(result.message)).finally(finish)
  }, [activeDrag, contextDragBroker, dropTargetForSpec, endContextDrag, externalAlias, externalDropTarget, sceneRuntime, specByAlias])

  const dropOverlayFor = useCallback((alias: string): React.ReactNode => {
    if (!activeDrag || activeDrag.sourceAlias === alias) return null
    let label = ''
    if (alias === externalAlias) {
      if (!externalDropTarget || !contextDragBroker.accepts(externalDropTarget, activeDrag.context)) return null
      label = `Open in ${externalPanel?.label || 'tasks'}`
    } else {
      const spec = specByAlias.get(alias)
      const target = spec ? dropTargetForSpec(spec) : null
      if (!spec || !target || !contextDragBroker.accepts(target, activeDrag.context)) return null
      label = spec.drop?.effect === 'attach'
        ? 'Attach to chat'
        : spec.drop?.effect === 'pin'
          ? 'Pin to board'
          : `Open in ${spec.title}`
    }
    return (
      <div
        className="kdc-drop"
        onDragOver={(event) => {
          event.preventDefault()
          event.stopPropagation()
          if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
          event.currentTarget.classList.add('is-over')
        }}
        onDragLeave={(event) => event.currentTarget.classList.remove('is-over')}
        onDrop={(event) => deliverDrop(alias, event)}
      >
        <span className="lbl">{label}</span>
      </div>
    )
  }, [activeDrag, contextDragBroker, deliverDrop, dropTargetForSpec, externalAlias, externalDropTarget, externalPanel, specByAlias])

  // ------------------------------------------------------ message broker
  useEffect(() => {
    function respondConfig(sourceAlias: string, frame: HTMLIFrameElement, data: Record<string, unknown>): void {
      const identityValue = asString(asRecord(data.data).identity) || asString(data.identity)
      if (!identityValue) return
      const scene = sceneConfigRef.current
      const spec = specByAlias.get(sourceAlias)
      const active = ctxRef.current
      const config: Record<string, unknown> = {
        configSource: 'scene',
        hostedByScene: true,
        baseUrl: active.baseUrl,
        defaultTenant: active.tenant,
        defaultProject: active.project,
        defaultApp: spec?.bundleId || active.bundleId,
        scene: {
          embedded: true,
          configSource: 'host',
          surface_ref: `versatile.${sourceAlias}`,
          target_surfaces: spec?.targetSurfaces ?? [],
          alias: sourceAlias,
        },
      }
      if (Object.keys(scene.namespaceStyles).length) {
        config.namespace_styles = scene.namespaceStyles
        config.namespaceStyles = scene.namespaceStyles
      }
      frame.contentWindow?.postMessage({ type: 'CONFIG_RESPONSE', identity: identityValue, config }, '*')
    }

    function onMessage(event: MessageEvent): void {
      const data = asRecord(event.data)
      const type = asString(data.type)
      if (!type) return

      // Auth changes may also arrive as messages (from the embedding host or
      // from a child that hit a 401): both trigger a fresh /profile probe.
      if (type === 'kdcube-auth-changed') {
        probeIdentity('kdcube-auth-changed-message')
        return
      }

      const sourceAlias = aliasForSource(event.source)

      if (!sourceAlias) {
        // Responses relayed from the outer host (when the scene is embedded)
        // fan out to every child frame.
        if (['CONFIG_RESPONSE', 'CONN_RESPONSE'].includes(type) && event.source !== window) {
          Object.values(frameRefs.current).forEach((frame) => {
            frame?.contentWindow?.postMessage(data, '*')
          })
        }
        return
      }

      const sourceFrame = frameRefs.current[sourceAlias] as HTMLIFrameElement

      if (type === 'kdcube.surface.command.ack') {
        console.info('[kdc-scene] surface command ack', {
          target_surface: data.target_surface || '',
          action: data.action || '',
          reason: data.reason || '',
          ts: data.ts || '',
        })
        return
      }

      if (type === SCENE_SUBSCRIBE_MESSAGE) {
        const nested = asRecord(data.data)
        const alias = asString(data.alias) || asString(data.widget) || sourceAlias
        const subscriptions = Array.isArray(data.subscriptions)
          ? data.subscriptions as SceneEventSubscriptionClaim[]
          : Array.isArray(nested.subscriptions)
            ? nested.subscriptions as SceneEventSubscriptionClaim[]
            : []
        const frameAlias = aliasForWidget(alias) || sourceAlias
        sceneEventBus.register(frameAlias, subscriptions)
        setSceneSubscriptions((prev) => ({ ...prev, [frameAlias]: subscriptions }))
        console.info('[kdc-scene] scene subscription request', {
          alias: frameAlias,
          subscriptions: subscriptions.map((claim) => String(claim?.id || '')),
        })
        return
      }

      if (type === SCENE_UNSUBSCRIBE_MESSAGE) {
        const alias = aliasForWidget(asString(data.alias) || asString(data.widget)) || sourceAlias
        sceneEventBus.unregister(alias)
        setSceneSubscriptions((prev) => {
          const next = { ...prev }
          delete next[alias]
          return next
        })
        return
      }

      if (type === 'CONFIG_REQUEST') {
        if (window.parent !== window) {
          // Embedded: the outer host owns runtime config; relay the request
          // up and its response back down (handled above).
          window.parent.postMessage(data, '*')
        } else {
          respondConfig(sourceAlias, sourceFrame, data)
        }
        return
      }

      if (type === 'kdcube-auth-required') {
        if (window.parent !== window) window.parent.postMessage(data, '*')
        return
      }

      if (type === 'kdcube-resize') {
        if (sourceAlias === 'memories') {
          const height = Number(data.height)
          if (Number.isFinite(height) && height > 0) {
            managerRef.current.fitHeight('memories', Math.ceil(height))
          }
        }
        if (window.parent !== window) window.parent.postMessage(data, '*')
        return
      }

      if (type === 'kdcube-memory-resize') {
        if (data.compact) managerRef.current.fitHeight('memories', Number(data.height) || 0)
        return
      }

      if (type === 'kdcube-context-drag-start') {
        beginContextDrag(data, sourceAlias)
        return
      }
      if (type === 'kdcube-context-drag-end' || type === INGRESS_DRAG_END) {
        endContextDrag()
        return
      }
      if (type === INGRESS_DRAG_START) {
        // Ingress payload drags (chat artifacts) deliver through the board's
        // own overlay; nothing to broker at the scene level for iframes.
        return
      }

      if (type === 'kdcube-widget-view') {
        const alias = aliasForWidget(asString(data.widget)) || sourceAlias
        // For docked components openComponent maps expanded → promote +
        // maximize and compact → dock back (website KDCSceneWidgetView).
        openComponent(alias, { expanded: data.view === 'expanded' })
        return
      }

      if (type === 'kdcube-widget-focus') {
        const alias = aliasForWidget(asString(data.widget)) || sourceAlias
        managerRef.current.front(alias)
        return
      }

      if (type === 'kdcube-set-view') {
        const alias = aliasForWidget(asString(data.widget)) || sourceAlias
        const sizing = alias === externalAlias
          ? externalSizing()
          : specByAlias.get(alias)
            ? windowSizing(specByAlias.get(alias) as SceneComponentSpec)
            : null
        if (sizing) managerRef.current.setExpanded(alias, sizing, data.view === 'expanded')
        return
      }

      if (type === SCENE_SURFACE_COMMAND) {
        const targetSurface = asString(data.target_surface) || asString(data.targetSurface)
        const result = sceneRuntime.queueSurfaceCommand(targetSurface, data)
        console.info('[versatile:scene] surface command request', {
          target_surface: targetSurface,
          action: asString(data.action),
          object_ref: asString(data.object_ref),
          ok: result.ok,
          code: result.code,
        })
        const commandId = asString(data.command_id)
        if (commandId && event.source) {
          try {
            (event.source as Window).postMessage({
              type: 'kdcube.surface.command.ack',
              command_id: commandId,
              target_surface: targetSurface,
              ok: result.ok,
              code: result.code || '',
            }, '*')
          } catch {
            /* the requester falls back on its ack timeout */
          }
        }
        if (!result.ok) setNotice(result.message)
        return
      }

      if (type === 'kdcube-pinboard-close') {
        // A docked board goes home to its slot; a floating one closes.
        const spec = specByAlias.get('pinboard')
        if (spec?.placement === 'docked') dockComponent('pinboard')
        else managerRef.current.close('pinboard')
        return
      }

      if (type === 'kdcube-memory-widget-status') {
        statusReadyRef.current[sourceAlias] = true
        if (sourceAlias === 'memories') {
          const count = Number(data.count)
          setMemoryCount(Number.isFinite(count) ? count : null)
        }
        specByAlias.get(sourceAlias)?.targetSurfaces.forEach((surface) => sceneRuntime.flushSurface(surface))
        return
      }

      if (
        externalPanel &&
        asString(data.widget) === externalPanel.widget_alias &&
        externalPanel.open_message_types?.includes(type)
      ) {
        openComponent(externalAlias, { expanded: true })
        return
      }

      if (['kdcube-context-attach', 'kdcube-context-focus', 'kdcube-context-remove'].includes(type)) {
        postToFrame('chat', data)
        return
      }

      if (type === INGRESS_MESSAGE_TYPE) {
        // Chat artifacts headed to the board: the pin board widget owns the
        // ingress semantics; the scene just routes the message.
        openComponent('pinboard')
        sceneRuntime.flushSurface('sdk.canvas.pinboard')
        if (!postToFrame('pinboard', data)) {
          window.setTimeout(() => postToFrame('pinboard', data), 600)
        }
        return
      }

      if (type === 'kdcube-pin-conversation') {
        const context = normalizeSceneContext(asRecord(data.context)) ||
          normalizeSceneContext(asRecord((Array.isArray(data.contexts) ? data.contexts : [])[0]))
        if (!context) {
          setNotice('Conversation pin request did not include a canonical context.')
          return
        }
        const result = sceneRuntime.queueSurfaceCommand('sdk.canvas.pinboard', {
          action: 'pin',
          context,
          x: 48,
          y: 48,
        })
        setNotice(result.ok ? 'Pinned conversation to board.' : result.message)
        return
      }
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [
    aliasForSource,
    aliasForWidget,
    beginContextDrag,
    dockComponent,
    endContextDrag,
    externalAlias,
    externalPanel,
    openComponent,
    postToFrame,
    probeIdentity,
    sceneEventBus,
    sceneRuntime,
    specByAlias,
  ])

  // ------------------------------------------------------------- render
  if (!ready) {
    return <div className="boot">Loading versatile scene...</div>
  }

  const scope = `${ctx.tenant} / ${ctx.project}`

  const railEntries: RailEntry[] = []
  components.forEach((spec) => {
    if (!spec.rail) return
    if (spec.gated && !isRegistered) return
    const state = manager.get(spec.alias)
    const docked = spec.placement === 'docked'
    const compatible = Boolean(
      activeDrag &&
      activeDrag.sourceAlias !== spec.alias &&
      spec.drop &&
      (() => {
        const target = dropTargetForSpec(spec)
        return target ? contextDragBroker.accepts(target, activeDrag.context) : false
      })(),
    )
    railEntries.push({
      id: spec.alias,
      label: spec.title,
      title: spec.title,
      accent: spec.accent,
      icon: componentIcon(spec.alias),
      // A docked component's rail button reflects (and toggles) its
      // floating promotion — the website's docked-tile rail semantics.
      open: docked ? Boolean(state?.floating) : manager.isOpen(spec.alias),
      pulse: compatible && !docked && !manager.isOpen(spec.alias),
      onToggle: () => {
        if (docked) {
          if (state?.floating) dockComponent(spec.alias)
          else promoteComponent(spec.alias)
          return
        }
        if (manager.isOpen(spec.alias)) {
          managerRef.current.close(spec.alias)
        } else {
          managerRef.current.open(spec.alias, windowSizing(spec))
        }
      },
    })
  })
  if (externalPanel && isRegistered) {
    // External panels sit between the summonable widgets and usage — same
    // slot the website host gives its task list.
    const usageIndex = railEntries.findIndex((entry) => entry.id === 'usage')
    const entry: RailEntry = {
      id: externalAlias,
      label: externalPanel.label,
      title: externalPanel.title || externalPanel.label,
      accent: 'green',
      icon: <TasksIcon />,
      open: manager.isOpen(externalAlias),
      onToggle: () => {
        if (manager.isOpen(externalAlias)) managerRef.current.close(externalAlias)
        else managerRef.current.open(externalAlias, externalSizing())
      },
    }
    if (usageIndex >= 0) railEntries.splice(usageIndex, 0, entry)
    else railEntries.push(entry)
  }

  return (
    <main className="scene">
      <header className="scene-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <span className="eyebrow">KDCube</span>
            <span className="title">Versatile Scene</span>
          </div>
        </div>
        <div className="status" title={scope}>
          <span className="dot" aria-hidden="true" />
          <span>{scope}</span>
        </div>
      </header>

      {notice ? (
        <div className="scene-notice" role="status">
          <span>{notice}</span>
          <button type="button" aria-label="Dismiss notice" onClick={() => setNotice('')}>×</button>
        </div>
      ) : null}

      <Rail entries={railEntries} />

      {(() => {
        const renderComponentWindow = (spec: SceneComponentSpec) => {
          const state = manager.get(spec.alias)
          if (!state?.everOpened) return null
          const docked = spec.placement === 'docked'
          const params = spec.alias === 'chat' && spec.widgetAlias === CHAT_WIDGET_ALIAS
            ? { ...chatWidgetParams(ctx), ...(spec.params ?? {}) }
            : spec.params
          const src = componentWidgetUrl(ctx, { ...spec, params })
          const title = spec.alias === 'memories' && memoryCount !== null
            ? `${spec.title} · ${memoryCount} in scope`
            : spec.title
          return (
            <FloatingWindow
              key={spec.alias}
              id={spec.alias}
              title={title}
              accent={spec.accent}
              icon={componentIcon(spec.alias)}
              hint={hintFor(spec.bundleId, spec.widgetAlias, ctx.bundleId)}
              state={state}
              hasViews={spec.views}
              manager={manager}
              sizing={windowSizing(spec)}
              dockable={docked}
              onUnpin={docked ? () => promoteComponent(spec.alias) : undefined}
              onDockBack={docked ? () => dockComponent(spec.alias) : undefined}
              sectionRef={(el) => { tileRefs.current[spec.alias] = el }}
              onViewChange={(view) => syncWidgetView(spec.alias, view)}
              actions={spec.alias === 'memories' ? (
                <button
                  type="button"
                  className="kdc-wbar-accent"
                  onClick={() => {
                    const result = sceneRuntime.queueSurfaceCommand('sdk.memory.viewer', { action: 'create' })
                    setNotice(result.message)
                  }}
                  title="Add memory"
                  aria-label="Add memory"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
                </button>
              ) : undefined}
              dropOverlay={dropOverlayFor(spec.alias)}
            >
              <iframe
                ref={(el) => { frameRefs.current[spec.alias] = el }}
                className="kdc-frame"
                title={spec.title}
                src={src}
                allow="clipboard-write"
                onLoad={() => {
                  statusReadyRef.current[spec.alias] = false
                  const win = managerRef.current.get(spec.alias)
                  if (spec.views) syncWidgetView(spec.alias, win?.expanded ? 'expanded' : 'compact')
                  spec.targetSurfaces.forEach((surface) => sceneRuntime.flushSurface(surface))
                  // Status-gated widgets normally report readiness themselves
                  // (kdcube-memory-widget-status); the load-timeout fallback
                  // keeps queued surface commands from stalling if that
                  // message is missed (website `ready: load-timeout` model).
                  window.setTimeout(() => {
                    if (statusReadyRef.current[spec.alias]) return
                    statusReadyRef.current[spec.alias] = true
                    spec.targetSurfaces.forEach((surface) => sceneRuntime.flushSurface(surface))
                  }, 1800)
                }}
              />
            </FloatingWindow>
          )
        }

        const dockedSpecs = components.filter((spec) =>
          spec.placement === 'docked' && (!spec.gated || isRegistered))
        const floatingSpecs = components.filter((spec) => spec.placement !== 'docked')

        return (
          <>
            {/* Docked tiles: the static stage layer (website docked-tile
                model). Each slot keeps hosting its window element even while
                it floats (position:fixed escapes the slot), so the iframe is
                never reparented and keeps its state; the emptied slot shows a
                dashed placeholder. */}
            {dockedSpecs.length ? (
              <div
                className="scene-stage"
                style={{
                  gridTemplateColumns: dockedSpecs
                    .map((spec) => (spec.alias === 'chat' ? 'minmax(320px, 440px)' : 'minmax(0, 1fr)'))
                    .join(' '),
                }}
              >
                {dockedSpecs.map((spec) => {
                  const floated = Boolean(manager.get(spec.alias)?.floating)
                  return (
                    <div
                      key={spec.alias}
                      className={`kdc-dock-slot${floated ? ' floated' : ''}`}
                      data-slot={spec.alias}
                    >
                      {renderComponentWindow(spec)}
                    </div>
                  )
                })}
              </div>
            ) : null}
            {floatingSpecs.map(renderComponentWindow)}
          </>
        )
      })()}

      {externalPanel && manager.get(externalAlias)?.everOpened ? (
        <FloatingWindow
          id={externalAlias}
          title={externalPanel.title || externalPanel.label}
          accent="green"
          icon={<TasksIcon />}
          hint={hintFor(externalPanel.bundle_id, externalPanel.widget_alias, ctx.bundleId)}
          state={manager.get(externalAlias)!}
          hasViews
          manager={manager}
          sizing={externalSizing()}
          onViewChange={(view) => syncWidgetView(externalAlias, view)}
          dropOverlay={dropOverlayFor(externalAlias)}
        >
          <iframe
            ref={(el) => { frameRefs.current[externalAlias] = el }}
            className="kdc-frame"
            title={externalPanel.title || externalPanel.label}
            src={externalWidgetUrl(ctx, externalPanel, false)}
            allow="clipboard-write"
            onLoad={() => {
              const win = managerRef.current.get(externalAlias)
              syncWidgetView(externalAlias, win?.expanded ? 'expanded' : 'compact')
              Object.keys(externalPanel.surfaces || {}).forEach((surface) => sceneRuntime.flushSurface(surface))
            }}
          />
        </FloatingWindow>
      ) : null}
    </main>
  )
}

const rootNode = document.getElementById('app')
if (!rootNode) {
  throw new Error('Missing #app root')
}

createRoot(rootNode).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
