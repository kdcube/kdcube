/**
 * Standalone Pin Board widget.
 *
 * Hosts the shared `CanvasBoard` component as its own iframe so a host page
 * (the Option B landing scene) can broker it alongside the chat / memory
 * widgets instead of embedding the whole multi-widget scene. Self-contained
 * canvas operations (pin a drop, patch, read, object actions) run here
 * against the bundle's canvas operations + Data Bus via `createCanvasHost`.
 * Cross-widget intents the board can't satisfy on its own — attaching a card
 * to chat, opening a conversation / memory in another surface — are posted
 * to the parent frame, which routes them to the right sibling widget.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import {
  CanvasBoard,
  applyCanvasCards,
  cardFromProviderObject,
  cardFromProvidedText,
  cardFromSearchResult,
  cardFromSelectedText,
  INGRESS_MESSAGE_TYPE,
  isCanvasIngressObjectRefPayload,
  isCanvasIngressTextPayload,
  parseIngressMessage,
  normalizeContext,
  uploadAndPinFiles,
  emptyCanvasDefinition,
  normalizeCanvasPatchEvent,
  canvasFromPatchEvent,
  upsertCanvasDefinition,
  type CanvasCard,
  type CanvasContextItem,
  type CanvasDefinition,
  type CanvasIngressMessage,
  type CanvasNamespaceStyle,
  type CanvasObjectActionName,
  type CanvasObjectActionResponse,
  type CanvasPatchInput,
  type CanvasPatchResponse,
  type CanvasPatchUiEvent,
  type CanvasSearchInput,
  type CanvasSearchResponse,
} from '@kdcube/components-react/canvas'
import { settings } from './api/settings'
import { createCanvasHost, type CanvasHost, type RouteContext } from './api/canvasHost'

// postMessage vocabulary for the host broker (Option B). Cross-surface
// object commands use the scene-wide generic command envelope.
const SURFACE_COMMAND_MESSAGE_TYPE = 'kdcube.surface.command'
const CLOSE_MESSAGE = 'kdcube-pinboard-close'
const CONTEXT_DRAG_START_MESSAGE = 'kdcube-context-drag-start'
const CONTEXT_DRAG_END_MESSAGE = 'kdcube-context-drag-end'

function canonicalCanvasId(value: unknown): string | undefined {
  const canvasId = String(value || '').trim()
  if (!canvasId || canvasId.startsWith('canvas:')) return undefined
  return canvasId
}

function canvasNameFromSurfaceCommand(data: Record<string, unknown>): string {
  const uiEvent = data.ui_event && typeof data.ui_event === 'object'
    ? data.ui_event as Record<string, unknown>
    : {}
  const direct = String(
    data.canvas_name ||
    uiEvent.canvas_name ||
    '',
  ).trim()
  if (direct) return direct
  const ref = String(
    data.object_ref ||
    uiEvent.object_ref ||
    data.ref ||
    '',
  ).trim()
  if (!ref.startsWith('cnv:')) return ''
  const key = ref.slice('cnv:'.length).trim()
  if (!key || key.includes('/')) return ''
  return key.split('@', 1)[0].trim()
}

function postToHost(message: Record<string, unknown>): void {
  if (window.parent && window.parent !== window) {
    window.parent.postMessage({ source: 'kdcube.pinboard', ...message }, '*')
  }
}

function dragEndPoint(event?: DragEvent<HTMLElement>): Record<string, number> {
  if (!event) return {}
  return {
    client_x: event.clientX,
    client_y: event.clientY,
    screen_x: event.screenX,
    screen_y: event.screenY,
  }
}

function cardFromContext(context: CanvasContextItem, rect: CanvasCard['rect']) {
  const ref = String(context.object_ref ?? context.logical_path ?? context.ref ?? context.id ?? '').trim()
  const kind = String(context.kind || context.object_kind || 'object.ref').trim() || 'object.ref'
  return cardFromSearchResult(
    {
      ref,
      title: context.label ? context.label : ref,
      mime: context.mime,
      summary: context.summary,
      kind,
      namespace: context.namespace,
      object_kind: context.object_kind,
    },
    { placement: 'placed', rect },
  )
}

function cardFromIngress(ingress: CanvasIngressMessage, rect: CanvasCard['rect']) {
  const payload = ingress.payload
  if (isCanvasIngressObjectRefPayload(payload)) {
    return cardFromProviderObject(
      {
        ref: payload.object_ref,
        filename: payload.filename || payload.title,
        mime: payload.mime || 'application/vnd.kdcube.object-ref+json',
        preview: payload.preview,
        namespace: payload.presentation?.namespace,
        object_kind: payload.presentation?.object_kind,
      },
      { title: payload.title, placement: 'placed', rect },
    )
  }
  if (isCanvasIngressTextPayload(payload)) {
    return cardFromProvidedText(payload.content.text, {
      title: payload.title,
      kind: payload.presentation?.label,
      object_kind: payload.presentation?.object_kind,
      placement: 'placed',
      rect,
    })
  }
  throw new Error('Unsupported canvas ingress payload shape')
}

function rectFromDropMessage(value: unknown): CanvasCard['rect'] {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const x = Number(raw.x)
  const y = Number(raw.y)
  return {
    x: Number.isFinite(x) ? Math.max(16, x) : 64,
    y: Number.isFinite(y) ? Math.max(16, y) : 64,
    w: 224,
    h: 104,
  }
}

export default function App() {
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [host, setHost] = useState<CanvasHost | null>(null)
  const [activeCanvasName, setActiveCanvasName] = useState('main')
  const [canvases, setCanvases] = useState<CanvasDefinition[]>([emptyCanvasDefinition('main')])
  const [canvasPatchEvent, setCanvasPatchEvent] = useState<CanvasPatchUiEvent | null>(null)
  // Namespace styles drive pin-card colors and can land late (slow bundle
  // fetch, background retry, host CONFIG_RESPONSE). Holding them in state —
  // fed by the settings subscription — re-colors already-rendered cards the
  // moment they arrive instead of leaving the board colorless until a reload.
  const [namespaceStyles, setNamespaceStyles] = useState<Record<string, CanvasNamespaceStyle | string>>(
    () => settings.getNamespaceStyles() as Record<string, CanvasNamespaceStyle | string>,
  )

  useEffect(() => settings.subscribeNamespaceStyles((styles) => {
    setNamespaceStyles(styles as Record<string, CanvasNamespaceStyle | string>)
  }), [])

  const activeCanvas = useMemo(
    () => canvases.find((canvas) => canvas.name === activeCanvasName) ?? emptyCanvasDefinition(activeCanvasName),
    [activeCanvasName, canvases],
  )
  const activeCanvasRef = useRef(activeCanvas)
  activeCanvasRef.current = activeCanvas

  // Boot: resolve runtime config, build the host, load the board.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        await settings.setupParentListener()
        const ctx: RouteContext = {
          tenant: settings.getTenant(),
          project: settings.getProject(),
          bundleId: settings.getBundleId(),
          baseUrl: settings.getBaseUrl(),
          accessToken: settings.getAccessToken(),
          idToken: settings.getIdToken(),
        }
        if (!ctx.tenant || !ctx.project || !ctx.bundleId) {
          throw new Error('Pin Board could not resolve tenant / project / bundle from its host.')
        }
        const nextHost = createCanvasHost({ ctx })
        const loaded = await nextHost.loadCanvas(activeCanvasName)
        if (cancelled) return
        setHost(nextHost)
        if (loaded.length) setCanvases(loaded)
        setReady(true)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
        setReady(true)
      }
    })()
    return () => {
      cancelled = true
    }
    // activeCanvasName is the initial 'main'; the board drives changes after boot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const applyPatchResponse = useCallback((response: CanvasPatchResponse) => {
    if (!response.ok) return
    const event = normalizeCanvasPatchEvent(response.ui_event ?? {
      type: 'canvas.patch.applied',
      source: 'canvas.patch',
      canvas_name: response.canvas_name,
      canvas_id: response.canvas_id,
      revision: response.revision,
      canvas_ref: response.canvas_ref,
      latest_ref: response.latest_ref,
      changed: response.changed,
      changed_cards: response.changed_cards,
      projection: response.projection,
    })
    if (!event) return
    setCanvasPatchEvent(event)
    setCanvases((current) => upsertCanvasDefinition(current, canvasFromPatchEvent(event, activeCanvasRef.current)))
  }, [])

  useEffect(() => {
    if (!host) return undefined
    return host.subscribeCanvasPatchEvents(
      applyPatchResponse,
      (err) => console.warn('[pinboard:data-bus] live canvas subscription failed', { message: err.message }),
    )
  }, [host, applyPatchResponse])

  const patchCanvas = useCallback(async (input: CanvasPatchInput): Promise<CanvasPatchResponse> => {
    if (!host) throw new Error('Pin Board is not ready yet.')
    const response = await host.patchCanvas(input)
    applyPatchResponse(response)
    return response
  }, [host, applyPatchResponse])

  const readCanvas = useCallback((input: Parameters<CanvasHost['readCanvas']>[0]) => {
    if (!host) return Promise.reject(new Error('Pin Board is not ready yet.'))
    return host.readCanvas(input)
  }, [host])

  const canvasIngressClient = useMemo(() => ({
    patchCanvas,
    uploadCanvasAttachments: (payload: Record<string, unknown>, files: File[]) => {
      if (!host) return Promise.reject(new Error('Pin Board is not ready yet.'))
      return host.uploadCanvasAttachments(payload, files)
    },
  }), [patchCanvas, host])

  const canvasTarget = useCallback((rect?: CanvasCard['rect']) => {
    const canvasId = canonicalCanvasId(activeCanvas.id)
    return {
      canvasId,
      canvasName: activeCanvas.name,
      baseRevision: canvasId ? activeCanvas.revision : undefined,
      rect,
    }
  }, [activeCanvas, host])

  const failNotice = useCallback((err: unknown) => {
    setNotice(err instanceof Error ? err.message : String(err))
  }, [])

  const onDropFiles = useCallback((files: File[], rect: CanvasCard['rect']) => {
    void uploadAndPinFiles(files, canvasTarget(rect), canvasIngressClient, { placement: 'placed', rect })
      .then(applyPatchResponse)
      .catch(failNotice)
  }, [applyPatchResponse, canvasIngressClient, canvasTarget, failNotice])

  const onDropText = useCallback((text: string, rect: CanvasCard['rect']) => {
    void applyCanvasCards([cardFromSelectedText(text, { placement: 'placed', rect })], canvasTarget(rect), canvasIngressClient)
      .then(applyPatchResponse)
      .catch(failNotice)
  }, [applyPatchResponse, canvasIngressClient, canvasTarget, failNotice])

  const onDropContext = useCallback((context: CanvasContextItem, rect: CanvasCard['rect']) => {
    void applyCanvasCards([cardFromContext(context, rect)], canvasTarget(rect), canvasIngressClient)
      .then(applyPatchResponse)
      .catch(failNotice)
  }, [applyPatchResponse, canvasIngressClient, canvasTarget, failNotice])

  const onDropIngress = useCallback((ingress: CanvasIngressMessage, rect: CanvasCard['rect']) => {
    void applyCanvasCards([cardFromIngress(ingress, rect)], canvasTarget(rect), canvasIngressClient)
      .then(applyPatchResponse)
      .catch(failNotice)
  }, [applyPatchResponse, canvasIngressClient, canvasTarget, failNotice])

  // Switching boards records the user's last-active board server-side, so an
  // omitted-canvas pin (UI drop or agent canvas.pin) lands on it, not "main".
  const handleCanvasChange = useCallback((name: string) => {
    setActiveCanvasName(name)
    // The startup styles fetch can fail outright (transient abort / network);
    // a board switch is a natural moment to recover — fetch-if-empty, deduped.
    settings.ensureNamespaceStyles()
    if (!host) return
    void host.setActiveCanvas(name).catch(() => undefined)
    // Boot only fetched the initial board, so other boards live in `canvases`
    // as metadata with no cards — selecting one rendered blank until the user
    // hit refresh. Load the picked board's pins now (same call boot/fallback use).
    void host.loadCanvas(name)
      .then((loaded) => { if (loaded.length) setCanvases(loaded) })
      .catch(() => undefined)
  }, [host])

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const data = event.data as Record<string, unknown> | null
      if (!data || typeof data !== 'object') return
      if (data.type === SURFACE_COMMAND_MESSAGE_TYPE) {
        const target = String(data.target_surface || '').trim().toLowerCase()
        if (target && target !== 'sdk.canvas.pinboard') return
        const action = String(data.action || '').trim().toLowerCase()
        if (action === 'open' || action === 'focus') {
          const name = canvasNameFromSurfaceCommand(data)
          if (!name) {
            setNotice('Canvas open command did not include a board name.')
            return
          }
          handleCanvasChange(name)
          return
        }
        if (action !== 'pin') return
        const context = normalizeContext(data.context)
        if (!context) {
          setNotice('Dropped context is not valid.')
          return
        }
        onDropContext(context, rectFromDropMessage(data))
        return
      }
      if (data.type === INGRESS_MESSAGE_TYPE) {
        const ingress = parseIngressMessage(data)
        if (!ingress) {
          setNotice('Dropped canvas ingress is not valid.')
          return
        }
        onDropIngress(ingress, rectFromDropMessage(data))
        return
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [handleCanvasChange, onDropContext, onDropIngress])

  // Attaching / focusing a pin in chat is not something the standalone board
  // can do — forward the intent to the host broker.
  const onAttachCard = useCallback((input: CanvasContextItem | CanvasContextItem[]) => {
    const items = Array.isArray(input) ? input : [input]
    if (!items.length) return
    const primary = items[0]
    postToHost({
      type: SURFACE_COMMAND_MESSAGE_TYPE,
      target_surface: 'sdk.chat.context',
      action: 'attach',
      object_ref: String(primary.object_ref || primary.ref || '').trim(),
      context: primary,
      contexts: items.length > 1 ? items : undefined,
    })
  }, [])

  const onAttachCanvas = useCallback((context: CanvasContextItem) => {
    postToHost({
      type: SURFACE_COMMAND_MESSAGE_TYPE,
      target_surface: 'sdk.chat.context',
      action: 'attach',
      object_ref: String(context.object_ref || context.ref || '').trim(),
      context,
    })
  }, [])

  const onDragCard = useCallback((input: CanvasContextItem | CanvasContextItem[] | null, event?: DragEvent<HTMLElement>) => {
    if (!input) {
      postToHost({ type: CONTEXT_DRAG_END_MESSAGE, ...dragEndPoint(event) })
      return
    }
    const contexts = Array.isArray(input) ? input.filter(Boolean) : [input]
    if (!contexts.length) {
      postToHost({ type: CONTEXT_DRAG_END_MESSAGE, ...dragEndPoint(event) })
      return
    }
    postToHost({
      type: CONTEXT_DRAG_START_MESSAGE,
      source_surface_ref: 'kdcube.pinboard',
      context: contexts[0],
      contexts,
      ...dragEndPoint(event),
    })
  }, [])

  const onObjectAction = useCallback(async (
    card: CanvasCard,
    action: CanvasObjectActionName,
  ): Promise<CanvasObjectActionResponse> => {
    if (!host) throw new Error('Pin Board is not ready yet.')
    const response = await host.objectAction(card, action, activeCanvasRef.current)
    // An `open` that resolves to another surface (memory / chat) is routed
    // by the host page; the board itself only previews/describes inline.
    const uiEvent = response.ui_event as Record<string, unknown> | undefined
    const targetSurface = uiEvent ? String(uiEvent.target_surface || '').trim() : ''
    if (action === 'open' && targetSurface) {
      postToHost({
        type: SURFACE_COMMAND_MESSAGE_TYPE,
        target_surface: targetSurface,
        action: String(uiEvent?.action || 'open'),
        ui_event: uiEvent,
        object_ref: String(uiEvent?.object_ref || card.ref || '').trim(),
        card_ref: card.ref,
      })
    }
    return response
  }, [host])

  const onSearchPins = useCallback(async (input: CanvasSearchInput): Promise<CanvasSearchResponse> => {
    if (!host) return { ok: false, items: [], error: 'Pin Board is not ready yet.' }
    const active = activeCanvasRef.current
    return host.searchPins({ ...input, canvasName: active?.name, canvasId: canonicalCanvasId(active?.id) })
  }, [host])

  const onCloseCanvas = useCallback(() => {
    postToHost({ type: CLOSE_MESSAGE })
  }, [])

  // Create a new board: add an empty canvas to local state, switch to it, and
  // make it the active board. It persists server-side on its first pin.
  const onCreateCanvas = useCallback((name: string) => {
    setCanvases((current) => upsertCanvasDefinition(current, emptyCanvasDefinition(name)))
    setActiveCanvasName(name)
    if (host) void host.setActiveCanvas(name).catch(() => undefined)
  }, [host])

  const onArchiveCanvas = useCallback((canvas: CanvasDefinition) => {
    if (!host) return
    const fallback = canvases.find((c) => c.name !== canvas.name)?.name || 'main'
    void host.archiveCanvas(canvas.name)
      .then(() => { setActiveCanvasName(fallback); return host.loadCanvas(fallback) })
      .then((loaded) => { if (loaded.length) setCanvases(loaded) })
      .catch(failNotice)
  }, [host, canvases, failNotice])

  const onDeleteCanvas = useCallback((canvas: CanvasDefinition) => {
    if (!host) return
    const fallback = canvases.find((c) => c.name !== canvas.name)?.name || 'main'
    void host.deleteCanvas(canvas.name)
      .then(() => { setActiveCanvasName(fallback); return host.loadCanvas(fallback) })
      .then((loaded) => { if (loaded.length) setCanvases(loaded) })
      .catch(failNotice)
  }, [host, canvases, failNotice])

  if (!ready) {
    return <div className="boot">Loading Pin Board…</div>
  }
  if (error) {
    return <div className="error">Pin Board failed to load: {error}</div>
  }

  return (
    <div className="pinboard-shell">
      {notice ? (
        <div className="notice" role="status">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss">×</button>
        </div>
      ) : null}
      <CanvasBoard
        activeCanvasName={activeCanvasName}
        canvases={canvases}
        canvasPatchEvent={canvasPatchEvent}
        patchCanvas={patchCanvas}
        readCanvas={readCanvas}
        onCanvasChange={handleCanvasChange}
        onAttachCanvas={onAttachCanvas}
        onAttachCard={onAttachCard}
        onDragCard={onDragCard}
        onCloseCanvas={onCloseCanvas}
        onCreateCanvas={onCreateCanvas}
        onArchiveCanvas={onArchiveCanvas}
        onDeleteCanvas={onDeleteCanvas}
        onDropFiles={onDropFiles}
        onDropText={onDropText}
        onDropContext={onDropContext}
        onDropIngress={onDropIngress}
        onObjectAction={onObjectAction}
        onSearchPins={onSearchPins}
        namespaceStyles={namespaceStyles}
        infoHtml={host?.getBoardInfoHtml() || undefined}
        /* When floated by the scene's window chrome (embedded), the host owns the
         * close/dock control — hide the board's own ✕ so there aren't two. */
        hideCloseControl={typeof window !== 'undefined' && window.parent !== window}
      />
    </div>
  )
}
