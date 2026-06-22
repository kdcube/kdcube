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
  cardFromChatArtifact,
  cardFromChatAssistantText,
  cardFromSearchResult,
  cardFromSelectedText,
  normalizeContext,
  uploadAndPinFiles,
  emptyCanvasDefinition,
  normalizeCanvasPatchEvent,
  canvasFromPatchEvent,
  upsertCanvasDefinition,
  type CanvasCard,
  type CanvasContextItem,
  type CanvasDefinition,
  type CanvasIngressPayload,
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
const DROP_INGRESS_MESSAGE = 'kdcube-pinboard-drop-ingress'
const CONTEXT_DRAG_START_MESSAGE = 'kdcube-context-drag-start'
const CONTEXT_DRAG_END_MESSAGE = 'kdcube-context-drag-end'

function canonicalCanvasId(value: unknown): string | undefined {
  const canvasId = String(value || '').trim()
  if (!canvasId || canvasId.startsWith('canvas:')) return undefined
  return canvasId
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

// Local card builders for the two ingress paths the scene also handles
// inline (these are not exported by the component package).
const CANVAS_CONTEXT_CARD_KINDS = new Set([
  'user.text',
  'user.attachment',
  'agent.text',
  'file',
  'memory',
  'source',
  'search.result',
  'note',
  'object.ref',
  'conversation',
])

function cardKindFromContext(context: CanvasContextItem, ref: string): CanvasCard['kind'] | undefined {
  const kind = String(context.kind || '').trim()
  if (CANVAS_CONTEXT_CARD_KINDS.has(kind)) return kind as CanvasCard['kind']
  void ref
  return undefined
}

function cardFromContext(context: CanvasContextItem, rect: CanvasCard['rect']) {
  const ref = String(context.object_ref ?? context.logical_path ?? context.ref ?? context.id ?? '').trim()
  return cardFromSearchResult(
    {
      ref,
      title: context.label ? context.label : ref,
      mime: context.mime,
      summary: context.summary,
      kind: cardKindFromContext(context, ref),
      object_kind: context.object_kind,
    },
    { placement: 'placed', rect },
  )
}

function cardFromIngress(payload: CanvasIngressPayload, rect: CanvasCard['rect']) {
  if (payload.kind === 'chat.artifact') {
    return cardFromChatArtifact(
      { ref: payload.ref, filename: payload.filename, mime: payload.mime, preview: payload.preview },
      { placement: 'placed', rect },
    )
  }
  if (payload.kind === 'chat.assistant.text') {
    return cardFromChatAssistantText(payload.text, { title: payload.title, placement: 'placed', rect })
  }
  throw new Error(`Unsupported canvas ingress kind: ${(payload as { kind?: string }).kind}`)
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
    const event = normalizeCanvasPatchEvent(response.ui_event ?? response)
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

  const canvasTarget = useCallback((rect?: CanvasCard['rect']) => ({
    canvasId: canonicalCanvasId(activeCanvas.id),
    canvasName: activeCanvas.name,
    baseRevision: activeCanvas.revision,
    rect,
  }), [activeCanvas, host])

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

  const onDropIngress = useCallback((payload: CanvasIngressPayload, rect: CanvasCard['rect']) => {
    void applyCanvasCards([cardFromIngress(payload, rect)], canvasTarget(rect), canvasIngressClient)
      .then(applyPatchResponse)
      .catch(failNotice)
  }, [applyPatchResponse, canvasIngressClient, canvasTarget, failNotice])

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const data = event.data as Record<string, unknown> | null
      if (!data || typeof data !== 'object') return
      if (data.type === SURFACE_COMMAND_MESSAGE_TYPE && data.action === 'pin') {
        const target = String(data.target_surface || '').trim().toLowerCase()
        if (target && target !== 'sdk.canvas.pinboard') return
        const context = normalizeContext(data.context)
        if (!context) {
          setNotice('Dropped context is not valid.')
          return
        }
        onDropContext(context, rectFromDropMessage(data))
        return
      }
      if (data.type === DROP_INGRESS_MESSAGE) {
        const payload = data.payload as CanvasIngressPayload | undefined
        if (!payload || typeof payload !== 'object') {
          setNotice('Dropped canvas ingress is not valid.')
          return
        }
        onDropIngress(payload, rectFromDropMessage(data))
        return
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [onDropContext, onDropIngress])

  // Attaching / focusing a pin in chat is not something the standalone board
  // can do — forward the intent to the host broker.
  const onAttachCard = useCallback((input: CanvasContextItem | CanvasContextItem[]) => {
    const items = Array.isArray(input) ? input : [input]
    if (!items.length) return
    const conversation = items.find((item) => item.kind === 'conversation')
    postToHost({
      type: SURFACE_COMMAND_MESSAGE_TYPE,
      target_surface: conversation ? 'sdk.chat.conversation' : 'sdk.chat.context',
      action: conversation ? 'open' : 'attach',
      object_ref: String((conversation || items[0]).object_ref || (conversation || items[0]).ref || '').trim(),
      context: conversation || items[0],
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

  // Switching boards records the user's last-active board server-side, so an
  // omitted-canvas pin (UI drop or agent canvas.pin) lands on it, not "main".
  const handleCanvasChange = useCallback((name: string) => {
    setActiveCanvasName(name)
    if (host) void host.setActiveCanvas(name).catch(() => undefined)
  }, [host])

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
        namespaceStyles={settings.getNamespaceStyles() as Record<string, CanvasNamespaceStyle | string>}
        infoHtml={host?.getBoardInfoHtml() || undefined}
        /* When floated by the scene's window chrome (embedded), the host owns the
         * close/dock control — hide the board's own ✕ so there aren't two. */
        hideCloseControl={typeof window !== 'undefined' && window.parent !== window}
      />
    </div>
  )
}
