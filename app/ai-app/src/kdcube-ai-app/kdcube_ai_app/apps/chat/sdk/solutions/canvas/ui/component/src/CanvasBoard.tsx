import {
  ClipboardPenLine,
  Download,
  Eye,
  FileText,
  Grip,
  Maximize2,
  MessageSquarePlus,
  Minimize2,
  Paperclip,
  PenLine,
  Pin,
  RotateCcw,
  Search,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type DragEvent, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from 'react'
import type { CanvasObjectActionName, CanvasObjectActionResponse, CanvasPatchInput, CanvasPatchOp, CanvasPatchResponse, CanvasReadInput, CanvasReadResponse } from './canvasTypes'
import { normalizeContext, normalizeContextMessage, type CanvasContextItem } from './contextTypes'
import { parseIngressMessage, type CanvasIngressPayload } from './ingressBridge'
import {
  canvasContext,
  cardsFromProjection,
  cardsFromPatchEvent,
  cardContext,
  canvasFromReadResponse,
  findCanvas,
  normalizeCanvasPatchEvent,
  type CanvasCard,
  type CanvasDefinition,
  type CanvasPatchUiEvent,
} from './canvasModel'

export interface CanvasBoardProps {
  activeCanvasName: string
  canvases: CanvasDefinition[]
  canvasPatchEvent?: CanvasPatchUiEvent | null
  patchCanvas: (input: CanvasPatchInput) => Promise<CanvasPatchResponse>
  readCanvas: (input: CanvasReadInput) => Promise<CanvasReadResponse>
  onCanvasChange: (canvasName: string) => void
  onAttachCanvas: (context: CanvasContextItem) => void
  onAttachCard: (context: CanvasContextItem | CanvasContextItem[]) => void
  onDragCard: (context: CanvasContextItem | CanvasContextItem[] | null) => void
  onCloseCanvas: () => void
  onDropFiles: (files: File[], rect: CanvasCard['rect']) => void
  onDropText: (text: string, rect: CanvasCard['rect']) => void
  onDropContext: (context: CanvasContextItem, rect: CanvasCard['rect']) => void
  onDropIngress: (payload: CanvasIngressPayload, rect: CanvasCard['rect']) => void
  onObjectAction?: (card: CanvasCard, action: CanvasObjectActionName) => Promise<CanvasObjectActionResponse>
}

interface DragState {
  cardIds: string[]
  offsetX: number
  offsetY: number
  cardOffsets: Record<string, { x: number; y: number }>
}

interface MarqueeState {
  startX: number
  startY: number
  x: number
  y: number
  w: number
  h: number
}

interface ResizeState {
  cardId: string
  startX: number
  startY: number
  startW: number
  startH: number
}

interface CanvasRevisionConflict {
  label: string
  operations: CanvasPatchOp[]
  expectedRevision?: number
  currentRevision?: number
}

type CanvasCardFilter = 'all' | 'suggestions' | 'board'

function iconForKind(kind: string) {
  if (kind.includes('attachment')) return Paperclip
  if (kind === 'memory') return Search
  if (kind === 'agent.text') return Sparkles
  if (kind === 'file') return FileText
  if (kind === 'issue.ref') return ClipboardPenLine
  return Pin
}

function cloneCards(cards: CanvasCard[]): CanvasCard[] {
  return cards.map((card) => ({
    ...card,
    rect: { ...card.rect },
  }))
}

function splitCardsByTrash(inputCards: CanvasCard[]): { active: CanvasCard[]; trashed: CanvasCard[] } {
  const active: CanvasCard[] = []
  const trashed: CanvasCard[] = []
  inputCards.forEach((card) => {
    if (card.trashed || card.placement === 'trashed') trashed.push(card)
    else active.push(card)
  })
  return { active, trashed }
}

function canvasPatchFailureMessage(response: CanvasPatchResponse, fallback: string): string {
  const base = response.error || response.detail || response.message || fallback
  const details: string[] = []
  if (response.status) details.push(`status ${response.status}`)
  if (response.expected_revision !== undefined && response.current_revision !== undefined) {
    details.push(`expected rev ${response.expected_revision}, current rev ${response.current_revision}`)
  } else if (response.current_revision !== undefined) {
    details.push(`current rev ${response.current_revision}`)
  }
  return details.length ? `${base} (${details.join('; ')})` : base
}

function isCanvasCardNotFoundMessage(message: string): boolean {
  return /canvas card not found:/i.test(message)
}

function isCanvasCardNotFoundResponse(response: CanvasPatchResponse): boolean {
  return isCanvasCardNotFoundMessage(response.error || response.detail || response.message || '')
}

function isCanvasRevisionConflict(response: CanvasPatchResponse): boolean {
  return response.error === 'canvas_revision_conflict' ||
    String(response.status) === 'conflict' ||
    (response.expected_revision !== undefined && response.current_revision !== undefined)
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function clampRect(
  rect: CanvasCard['rect'],
  bounds: { width: number; height: number },
): CanvasCard['rect'] {
  return {
    x: Math.round(clamp(rect.x, 8, Math.max(8, bounds.width - rect.w - 8))),
    y: Math.round(clamp(rect.y, 8, Math.max(8, bounds.height - rect.h - 8))),
    w: rect.w,
    h: rect.h,
  }
}

function rectsCollide(
  a: CanvasCard['rect'],
  b: CanvasCard['rect'],
  gap = 12,
): boolean {
  return !(
    a.x + a.w + gap <= b.x ||
    b.x + b.w + gap <= a.x ||
    a.y + a.h + gap <= b.y ||
    b.y + b.h + gap <= a.y
  )
}

function rectsIntersect(
  a: CanvasCard['rect'],
  b: CanvasCard['rect'],
): boolean {
  return !(
    a.x + a.w < b.x ||
    b.x + b.w < a.x ||
    a.y + a.h < b.y ||
    b.y + b.h < a.y
  )
}

function cardsBounds(cards: CanvasCard[]): CanvasCard['rect'] | null {
  if (!cards.length) return null
  const left = Math.min(...cards.map((card) => card.rect.x))
  const top = Math.min(...cards.map((card) => card.rect.y))
  const right = Math.max(...cards.map((card) => card.rect.x + card.rect.w))
  const bottom = Math.max(...cards.map((card) => card.rect.y + card.rect.h))
  return {
    x: left,
    y: top,
    w: right - left,
    h: bottom - top,
  }
}

function setFromIds(ids: string[]): Set<string> {
  return new Set(ids.filter(Boolean))
}

function hasExternalDropData(event: DragEvent<HTMLElement>): boolean {
  const types = Array.from(event.dataTransfer.types || [])
  return types.includes('Files') || types.includes('application/json') || types.includes('text/plain')
}

function isFullBoardProjection(projection: unknown, projectedCardCount: number): boolean {
  if (!projection || typeof projection !== 'object') return false
  const raw = projection as { schema?: unknown; cards_count?: unknown; cardsCount?: unknown }
  const schema = typeof raw.schema === 'string' ? raw.schema : ''
  const cardsCountRaw = raw.cards_count ?? raw.cardsCount
  const cardsCount = typeof cardsCountRaw === 'number'
    ? cardsCountRaw
    : (typeof cardsCountRaw === 'string' && cardsCountRaw.trim() ? Number(cardsCountRaw) : NaN)
  return schema === 'kdcube.canvas.projection.v1' &&
    Number.isFinite(cardsCount) &&
    cardsCount === projectedCardCount
}

function parseCardTime(value?: string): number | null {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

function formatCardTime(ts: number | null): string {
  if (!ts) return 'n/a'
  const date = new Date(ts)
  const now = new Date()
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

export function CanvasBoard({
  activeCanvasName,
  canvases,
  canvasPatchEvent,
  patchCanvas,
  readCanvas,
  onCanvasChange,
  onAttachCanvas,
  onAttachCard,
  onDragCard,
  onCloseCanvas,
  onDropFiles,
  onDropText,
  onDropContext,
  onDropIngress,
  onObjectAction,
}: CanvasBoardProps) {
  const boardRef = useRef<HTMLDivElement | null>(null)
  const attachmentInputRef = useRef<HTMLInputElement | null>(null)
  const activeCanvas = useMemo(() => findCanvas(canvases, activeCanvasName), [activeCanvasName, canvases])
  const [cards, setCards] = useState<CanvasCard[]>(() => cloneCards(activeCanvas.cards))
  const [canvasId, setCanvasId] = useState<string>(activeCanvas.id)
  const [canvasRevision, setCanvasRevision] = useState<number>(activeCanvas.revision)
  const [canvasRef, setCanvasRef] = useState<string>(activeCanvas.ref)
  const [trashedCards, setTrashedCards] = useState<CanvasCard[]>([])
  const [dragState, setDragState] = useState<DragState | null>(null)
  const [resizeState, setResizeState] = useState<ResizeState | null>(null)
  const [marqueeState, setMarqueeState] = useState<MarqueeState | null>(null)
  const [selectedCardIds, setSelectedCardIds] = useState<Set<string>>(() => (
    setFromIds(activeCanvas.cards.filter((card) => card.selected).map((card) => card.id))
  ))
  const [trashOpen, setTrashOpen] = useState(false)
  const [externalDropReady, setExternalDropReady] = useState(false)
  const [expandedCardId, setExpandedCardId] = useState<string>('')
  const [resolverStateByCard, setResolverStateByCard] = useState<Record<string, CanvasObjectActionResponse>>({})
  const [resolverLoadingByCard, setResolverLoadingByCard] = useState<Record<string, boolean>>({})
  const [resolverNoticeByCard, setResolverNoticeByCard] = useState<Record<string, string>>({})
  const [cardFilter, setCardFilter] = useState<CanvasCardFilter>('all')
  const [patchError, setPatchError] = useState<string>('')
  const [revisionConflict, setRevisionConflict] = useState<CanvasRevisionConflict | null>(null)
  const [refreshingCanvas, setRefreshingCanvas] = useState(false)
  const lastPatchKeyRef = useRef<string>('')
  const cardsRef = useRef<CanvasCard[]>(cards)
  const canvasIdRef = useRef<string>(canvasId)
  const canvasNameRef = useRef<string>(activeCanvas.name)
  const canvasRevisionRef = useRef<number>(canvasRevision)
  const pendingResizeRectRef = useRef<CanvasCard['rect'] | null>(null)
  const descriptionHoldTimerRef = useRef<number | null>(null)
  const descriptionHoldStartRef = useRef<{ cardId: string; x: number; y: number } | null>(null)
  const suppressCardClickRef = useRef(false)

  useEffect(() => {
    cardsRef.current = cards
  }, [cards])

  useEffect(() => {
    canvasIdRef.current = canvasId
  }, [canvasId])

  useEffect(() => {
    canvasNameRef.current = activeCanvas.name
  }, [activeCanvas.name])

  useEffect(() => {
    canvasRevisionRef.current = canvasRevision
  }, [canvasRevision])

  function clearDescriptionHold() {
    if (descriptionHoldTimerRef.current != null) {
      window.clearTimeout(descriptionHoldTimerRef.current)
      descriptionHoldTimerRef.current = null
    }
    descriptionHoldStartRef.current = null
  }

  useEffect(() => () => clearDescriptionHold(), [])

  useEffect(() => {
    if (!expandedCardId || !onObjectAction) return
    const card = cardsRef.current.find((item) => item.id === expandedCardId)
    if (!card?.ref || resolverStateByCard[card.id] || resolverLoadingByCard[card.id]) return
    setResolverLoadingByCard((current) => ({ ...current, [card.id]: true }))
    onObjectAction(card, 'capabilities')
      .then((result) => {
        setResolverStateByCard((current) => ({ ...current, [card.id]: result }))
        if (!result.ok) {
          setResolverNoticeByCard((current) => ({
            ...current,
            [card.id]: result.error || result.message || 'Resolver is not available for this object.',
          }))
        }
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : String(error)
        setResolverNoticeByCard((current) => ({ ...current, [card.id]: message }))
      })
      .finally(() => {
        setResolverLoadingByCard((current) => ({ ...current, [card.id]: false }))
      })
  }, [expandedCardId, onObjectAction, resolverLoadingByCard, resolverStateByCard])

  function boardBounds(): { width: number; height: number } {
    const bounds = boardRef.current?.getBoundingClientRect()
    return {
      width: Math.max(360, bounds?.width || 1200),
      height: Math.max(260, bounds?.height || 520),
    }
  }

  function findOpenRect(
    width = 238,
    height = 112,
    existingCards: CanvasCard[] = cards,
  ): CanvasCard['rect'] {
    const bounds = boardBounds()
    const maxX = Math.max(8, bounds.width - width - 8)
    const maxY = Math.max(8, bounds.height - height - 8)
    const occupied = existingCards.map((card) => clampRect(card.rect, bounds))
    for (let y = 28; y <= maxY; y += 28) {
      for (let x = 28; x <= maxX; x += 28) {
        const rect = { x, y, w: width, h: height }
        if (!occupied.some((candidate) => rectsCollide(rect, candidate, 18))) {
          return rect
        }
      }
    }
    const fallbackOffset = (existingCards.length % 8) * 18
    return {
      x: Math.round(clamp(28 + fallbackOffset, 8, maxX)),
      y: Math.round(clamp(28 + fallbackOffset, 8, maxY)),
      w: width,
      h: height,
    }
  }

  function deconflictCards(inputCards: CanvasCard[]): CanvasCard[] {
    const placed: CanvasCard[] = []
    const bounds = boardBounds()
    for (const rawCard of inputCards) {
      const card = { ...rawCard, rect: clampRect(rawCard.rect, bounds) }
      if (placed.some((candidate) => rectsCollide(card.rect, candidate.rect, 10))) {
        card.rect = findOpenRect(card.rect.w, card.rect.h, placed)
      }
      placed.push(card)
    }
    return placed
  }

  function mergeCanvasEvent(
    event: CanvasPatchUiEvent,
    options: { allowProjection?: boolean } = {},
  ) {
    const allowProjection = options.allowProjection ?? true
    const belongsToActiveCanvas = (
      event.canvas_name === activeCanvas.name ||
      event.canvas_id === activeCanvas.id ||
      event.canvas_id === canvasId
    )
    if (!belongsToActiveCanvas) return
    if (typeof event.revision === 'number' && Number.isFinite(event.revision)) {
      canvasRevisionRef.current = event.revision
      setCanvasRevision(event.revision)
    }
    if (event.canvas_id) {
      canvasIdRef.current = event.canvas_id
      setCanvasId(event.canvas_id)
    }
    if (event.latest_ref || event.canvas_ref) {
      const nextRef = event.latest_ref || event.canvas_ref || activeCanvas.ref
      setCanvasRef(nextRef)
    }
    const changedCards = cardsFromPatchEvent(event)
    if (changedCards.length) {
      const { active: activeChangedCards, trashed: trashedChangedCards } = splitCardsByTrash(changedCards)
      setSelectedCardIds(setFromIds(activeChangedCards.map((card) => card.id)))
      if (trashedChangedCards.length) {
        setTrashedCards((current) => {
          const next = new Map<string, CanvasCard>(current.map((card) => [card.id, card]))
          trashedChangedCards.forEach((card) => next.set(card.id, card))
          return Array.from(next.values())
        })
      }
      setCards((current) => {
        const next = new Map<string, CanvasCard>(current.map((card) => [card.id, card]))
        trashedChangedCards.forEach((card) => next.delete(card.id))
        activeChangedCards.forEach((card) => {
          const existing = next.get(card.id)
          const nextCard = {
            ...existing,
            ...card,
            rect: { ...(existing?.rect ?? card.rect), ...card.rect },
            selected: true,
            suggested: card.suggested ?? existing?.suggested,
          }
          if (!existing) {
            const occupied = Array.from(next.values())
            if (occupied.some((candidate) => rectsCollide(nextCard.rect, candidate.rect, 18))) {
              nextCard.rect = findOpenRect(nextCard.rect.w, nextCard.rect.h, occupied)
            }
          }
          next.set(card.id, {
            ...nextCard,
          })
        })
        return Array.from(next.values())
      })
      setTrashOpen(false)
      return
    }
    if (!allowProjection) return
    const projectionCards = cardsFromProjection(event.projection, event.changed_cards)
    if (projectionCards.length) {
      const { active, trashed } = splitCardsByTrash(projectionCards)
      setCards((current) => {
        const projectionIsExplicitFullBoard = isFullBoardProjection(
          event.projection,
          projectionCards.length,
        )
        if (projectionIsExplicitFullBoard) return cloneCards(active)
        const next = new Map(current.map((card) => [card.id, card]))
        trashed.forEach((card) => next.delete(card.id))
        active.forEach((card) => {
          const existing = next.get(card.id)
          next.set(card.id, {
            ...existing,
            ...card,
            rect: { ...(existing?.rect ?? card.rect), ...card.rect },
          })
        })
        return Array.from(next.values())
      })
      if (trashed.length) {
        setTrashedCards((current) => {
          const next = new Map<string, CanvasCard>(current.map((card) => [card.id, card]))
          trashed.forEach((card) => next.set(card.id, card))
          return Array.from(next.values())
        })
      }
      setTrashOpen(false)
    }
  }

  useEffect(() => {
    const split = splitCardsByTrash(cloneCards(activeCanvas.cards))
    const nextCards = split.active
    cardsRef.current = nextCards
    canvasIdRef.current = activeCanvas.id
    canvasNameRef.current = activeCanvas.name
    canvasRevisionRef.current = activeCanvas.revision
    setCards(nextCards)
    setCanvasId(activeCanvas.id)
    setCanvasRevision(activeCanvas.revision)
    setCanvasRef(activeCanvas.ref)
    setSelectedCardIds(setFromIds(activeCanvas.cards.filter((card) => card.selected).map((card) => card.id)))
    setTrashedCards(split.trashed)
    setTrashOpen(false)
    setExpandedCardId('')
    setPatchError('')
    setRevisionConflict(null)
  }, [activeCanvas])

  useEffect(() => {
    const liveIds = new Set(cards.map((card) => card.id))
    setSelectedCardIds((current) => setFromIds(Array.from(current).filter((id) => liveIds.has(id))))
  }, [cards])

  useEffect(() => {
    if (!canvasPatchEvent) return
    const belongsToActiveCanvas = (
      canvasPatchEvent.canvas_name === activeCanvas.name ||
      canvasPatchEvent.canvas_id === activeCanvas.id
    )
    if (!belongsToActiveCanvas) return
    const patchKey = [
      canvasPatchEvent.canvas_id || activeCanvas.id,
      canvasPatchEvent.revision ?? '',
      canvasPatchEvent.canvas_ref || '',
      JSON.stringify(canvasPatchEvent.changed || []),
    ].join('|')
    if (patchKey === lastPatchKeyRef.current) return
    lastPatchKeyRef.current = patchKey
    mergeCanvasEvent(canvasPatchEvent)
  }, [activeCanvas, canvasPatchEvent])

  useEffect(() => {
    if (!resizeState) return
    const start = resizeState

    function onPointerMove(event: PointerEvent) {
      const board = boardRef.current
      if (!board) return
      const bounds = board.getBoundingClientRect()
      setCards((current) => current.map((card) => {
        if (card.id !== start.cardId) return card
        const nextW = clamp(
          start.startW + event.clientX - start.startX,
          130,
          Math.max(130, bounds.width - card.rect.x - 8),
        )
        const nextH = clamp(
          start.startH + event.clientY - start.startY,
          78,
          Math.max(78, bounds.height - card.rect.y - 8),
        )
        pendingResizeRectRef.current = {
          ...card.rect,
          w: Math.round(nextW),
          h: Math.round(nextH),
        }
        return {
          ...card,
          rect: {
            ...card.rect,
            w: Math.round(nextW),
            h: Math.round(nextH),
          },
        }
      }))
    }

    function onPointerUp() {
      const resizedCard = cardsRef.current.find((card) => card.id === start.cardId)
      const resizedRect = pendingResizeRectRef.current
      pendingResizeRectRef.current = null
      setResizeState(null)
      if (!resizedCard || !resizedRect) return
      void applyCardOperations([
        {
          op: 'resize_card',
          card_id: resizedCard.id,
          w: Math.round(resizedRect.w),
          h: Math.round(resizedRect.h),
        },
      ], 'Resize card')
    }

    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
    return () => {
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
    }
  }, [resizeState])

  const liveCanvas = useMemo(() => ({
    ...activeCanvas,
    id: canvasId,
    revision: canvasRevision,
    ref: canvasRef,
    cards,
  }), [activeCanvas, canvasId, canvasRef, canvasRevision, cards])
  const canvasStats = useMemo(() => {
    const created = cards
      .map((card) => parseCardTime(card.createdAt || card.updatedAt))
      .filter((value): value is number => value != null)
      .sort((a, b) => a - b)
    const changed = cards
      .map((card) => parseCardTime(card.updatedAt || card.createdAt))
      .filter((value): value is number => value != null)
      .sort((a, b) => a - b)
    const pendingSuggestions = cards.filter((card) => card.suggested && card.placement !== 'placed').length
    return {
      pins: cards.length,
      pendingSuggestions,
      oldest: created[0] ?? null,
      newest: changed[changed.length - 1] ?? null,
    }
  }, [cards])
  const visibleCards = useMemo(() => {
    if (cardFilter === 'suggestions') {
      return cards.filter((card) => card.suggested && card.placement !== 'placed')
    }
    if (cardFilter === 'board') {
      return cards.filter((card) => !(card.suggested && card.placement !== 'placed'))
    }
    return cards
  }, [cardFilter, cards])
  const selectedVisibleCards = useMemo(() => (
    visibleCards.filter((card) => selectedCardIds.has(card.id))
  ), [selectedCardIds, visibleCards])
  const selectedBounds = useMemo(() => cardsBounds(selectedVisibleCards), [selectedVisibleCards])

  useEffect(() => {
    if (!marqueeState) return
    const start = marqueeState

    function onPointerMove(event: PointerEvent) {
      const point = boardPoint(event)
      if (!point) return
      setMarqueeState(marqueeFromPoints(start.startX, start.startY, point.x, point.y))
    }

    function onPointerUp(event: PointerEvent) {
      const point = boardPoint(event)
      const finalRect = point
        ? marqueeFromPoints(start.startX, start.startY, point.x, point.y)
        : start
      setMarqueeState(null)
      if (finalRect.w < 6 && finalRect.h < 6) {
        setSelectedCardIds(new Set())
        return
      }
      const selected = visibleCards
        .filter((card) => rectsIntersect(card.rect, finalRect))
        .map((card) => card.id)
      setSelectedCardIds(setFromIds(selected))
    }

    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
    return () => {
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
    }
  }, [marqueeState, visibleCards])

  function contextsForCards(inputCards: CanvasCard[]): CanvasContextItem[] {
    return inputCards.map((card) => cardContext(liveCanvas, card))
  }

  function dragPayloadForCards(inputCards: CanvasCard[]): CanvasContextItem | CanvasContextItem[] | null {
    const contexts = contextsForCards(inputCards)
    if (!contexts.length) return null
    return contexts.length === 1 ? contexts[0] : contexts
  }

  function dragLabelForCards(inputCards: CanvasCard[]): string {
    if (inputCards.length === 1) return inputCards[0].title
    return `${inputCards.length} selected pins`
  }

  function dragDataForCards(inputCards: CanvasCard[]): string {
    const contexts = contextsForCards(inputCards)
    if (contexts.length === 1) return JSON.stringify(contexts[0])
    return JSON.stringify({
      type: 'kdcube-canvas-context-focus',
      source: 'sdk-canvas',
      contexts,
    })
  }

  function moveCards(
    cardIds: string[],
    x: number,
    y: number,
    cardOffsets?: Record<string, { x: number; y: number }>,
  ): CanvasPatchOp[] {
    const board = boardRef.current
    if (!board) return []
    const movingCards = cards.filter((candidate) => cardIds.includes(candidate.id))
    if (!movingCards.length) return []
    const bounds = board.getBoundingClientRect()
    const groupBounds = cardsBounds(movingCards)
    if (!groupBounds) return []
    const nextOriginX = clamp(x, 8, Math.max(8, bounds.width - groupBounds.w - 8))
    const nextOriginY = clamp(y, 8, Math.max(8, bounds.height - groupBounds.h - 8))
    const offsets = cardOffsets || Object.fromEntries(movingCards.map((card) => [
      card.id,
      { x: card.rect.x - groupBounds.x, y: card.rect.y - groupBounds.y },
    ]))
    const movingIds = new Set(cardIds)
    const operations = movingCards.map((card) => ({
      op: 'move_card' as const,
      card_id: card.id,
      x: Math.round(nextOriginX + (offsets[card.id]?.x || 0)),
      y: Math.round(nextOriginY + (offsets[card.id]?.y || 0)),
    }))
    setCards((current) => current.map((candidate) => (
      movingIds.has(candidate.id)
        ? {
            ...candidate,
            rect: {
              ...candidate.rect,
              x: operations.find((op) => op.card_id === candidate.id)?.x ?? candidate.rect.x,
              y: operations.find((op) => op.card_id === candidate.id)?.y ?? candidate.rect.y,
            },
            placement: 'placed',
          }
        : candidate
    )))
    return operations
  }

  function trashCards(cardIds: string[]) {
    const trashIds = new Set(cardIds)
    const removedCards = cards.filter((candidate) => trashIds.has(candidate.id))
    if (!removedCards.length) return
    setCards((current) => current.filter((candidate) => !trashIds.has(candidate.id)))
    setTrashedCards((current) => [
      ...removedCards.map((card) => ({
        ...card,
        trashed: true,
        placement: 'trashed' as const,
        trashState: {
          previous_placement: card.placement || 'placed',
          previous_rect: card.rect,
        },
      })),
      ...current.filter((candidate) => !trashIds.has(candidate.id)),
    ])
    setSelectedCardIds((current) => setFromIds(Array.from(current).filter((id) => !trashIds.has(id))))
    setDragState(null)
    onDragCard(null)
    setTrashOpen(true)
    void applyCardOperations(
      removedCards.map((card) => ({
        op: 'update_card',
        card_id: card.id,
        set: {
          trashed: true,
          placement: 'trashed',
          trash_state: {
            previous_placement: card.placement || 'placed',
            previous_rect: card.rect,
          },
        },
      })),
      'Move pins to bin',
    )
  }

  function restoreCard(cardId: string) {
    const card = trashedCards.find((candidate) => candidate.id === cardId)
    if (!card) return
    const previousRect = card.trashState?.previous_rect && typeof card.trashState.previous_rect === 'object'
      ? card.trashState.previous_rect as CanvasCard['rect']
      : card.rect
    const previousPlacement = typeof card.trashState?.previous_placement === 'string'
      ? card.trashState.previous_placement as CanvasCard['placement']
      : 'placed'
    setTrashedCards((current) => current.filter((candidate) => candidate.id !== cardId))
    setCards((current) => {
      if (current.some((candidate) => candidate.id === cardId)) return current
      return [...current, {
        ...card,
        trashed: false,
        placement: previousPlacement,
        rect: previousRect,
      }]
    })
    void applyCardOperations([
      {
        op: 'update_card',
        card_id: card.id,
        set: {
          trashed: false,
          placement: previousPlacement,
          rect: previousRect,
        },
      },
    ], 'Restore pin')
  }

  function cleanTrash() {
    const deletedCards = trashedCards.slice()
    if (!deletedCards.length) return
    setTrashedCards([])
    void applyCardOperations(
      deletedCards.map((card) => ({ op: 'delete_card', card_id: card.id })),
      'Clean bin',
    )
  }

  function dropRect(event: DragEvent<HTMLElement>, width = 246, height = 104): CanvasCard['rect'] {
    const board = boardRef.current
    if (!board) return { x: 42, y: 42, w: width, h: height }
    const bounds = board.getBoundingClientRect()
    return {
      x: Math.round(clamp(event.clientX - bounds.left - width / 2, 8, Math.max(8, bounds.width - width - 8))),
      y: Math.round(clamp(event.clientY - bounds.top - height / 2, 8, Math.max(8, bounds.height - height - 8))),
      w: width,
      h: height,
    }
  }

  function boardPoint(event: PointerEvent | ReactPointerEvent<HTMLElement>): { x: number; y: number } | null {
    const board = boardRef.current
    if (!board) return null
    const bounds = board.getBoundingClientRect()
    return {
      x: Math.round(clamp(event.clientX - bounds.left, 0, bounds.width)),
      y: Math.round(clamp(event.clientY - bounds.top, 0, bounds.height)),
    }
  }

  function marqueeFromPoints(startX: number, startY: number, endX: number, endY: number): MarqueeState {
    const x = Math.min(startX, endX)
    const y = Math.min(startY, endY)
    return {
      startX,
      startY,
      x,
      y,
      w: Math.abs(endX - startX),
      h: Math.abs(endY - startY),
    }
  }

  function startMarqueeSelection(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    if (event.target !== event.currentTarget && !(event.target instanceof HTMLElement && event.target.classList.contains('canvas-grid'))) {
      return
    }
    const point = boardPoint(event)
    if (!point) return
    event.preventDefault()
    setMarqueeState({
      startX: point.x,
      startY: point.y,
      x: point.x,
      y: point.y,
      w: 0,
      h: 0,
    })
  }

  function newCardRect(width = 238, height = 112): CanvasCard['rect'] {
    return findOpenRect(width, height, cards)
  }

  function createUserTextCard() {
    const text = window.prompt('Text for the new canvas card')
    if (!text || !text.trim()) return
    onDropText(text.trim(), newCardRect(238, 112))
  }

  function createUserAttachmentCard(files: FileList | null) {
    const selected = Array.from(files || []).filter(Boolean)
    if (!selected.length) return
    onDropFiles(selected, newCardRect(260, 120))
    if (attachmentInputRef.current) {
      attachmentInputRef.current.value = ''
    }
  }

  function handleExternalDrop(event: DragEvent<HTMLElement>) {
    const droppedFiles = Array.from(event.dataTransfer.files || []).filter(Boolean)
    if (droppedFiles.length) {
      onDropFiles(droppedFiles, dropRect(event, 260, 120))
      return
    }

    const rawJson = event.dataTransfer.getData('application/json')
    if (rawJson) {
      try {
        const parsed = JSON.parse(rawJson)
        const ingressMessage = parseIngressMessage(parsed)
        if (ingressMessage) {
          onDropIngress(ingressMessage.payload, dropRect(event, 246, 112))
          return
        }
        const contextMessage = normalizeContextMessage(parsed)
        if (contextMessage?.contexts?.length) {
          contextMessage.contexts.forEach((context) => onDropContext(context, dropRect(event, 224, 104)))
          return
        }
        const context = normalizeContext(parsed)
        if (context) {
          onDropContext(context, dropRect(event, 224, 104))
          return
        }
      } catch {
        // Fall through to text/plain handling.
      }
    }

    const text = event.dataTransfer.getData('text/plain').trim()
    if (text) {
      onDropText(text, dropRect(event, 238, 112))
    }
  }

  function handleTrashDragOver(event: DragEvent<HTMLElement>) {
    if (!dragState) return
    event.preventDefault()
    event.stopPropagation()
    event.dataTransfer.dropEffect = 'move'
  }

  function handleTrashDrop(event: DragEvent<HTMLElement>) {
    if (!dragState) return
    event.preventDefault()
    event.stopPropagation()
    trashCards(dragState.cardIds)
  }

  function startCardsDrag(inputCards: CanvasCard[], event: DragEvent<HTMLElement>) {
    clearDescriptionHold()
    if (resizeState) {
      event.preventDefault()
      return
    }
    const board = boardRef.current
    if (!board || !inputCards.length) {
      event.preventDefault()
      return
    }
    const bounds = board.getBoundingClientRect()
    const groupBounds = cardsBounds(inputCards)
    if (!groupBounds) {
      event.preventDefault()
      return
    }
    const cardIds = inputCards.map((card) => card.id)
    const cardOffsets = Object.fromEntries(inputCards.map((card) => [
      card.id,
      { x: card.rect.x - groupBounds.x, y: card.rect.y - groupBounds.y },
    ]))
    setSelectedCardIds(setFromIds(cardIds))
    setDragState({
      cardIds,
      offsetX: event.clientX - bounds.left - groupBounds.x,
      offsetY: event.clientY - bounds.top - groupBounds.y,
      cardOffsets,
    })
    event.dataTransfer.effectAllowed = 'copyMove'
    event.dataTransfer.setData('application/json', dragDataForCards(inputCards))
    event.dataTransfer.setData('text/plain', dragLabelForCards(inputCards))
    onDragCard(dragPayloadForCards(inputCards))
  }

  function startCardResize(card: CanvasCard, event: ReactPointerEvent<HTMLButtonElement>) {
    event.preventDefault()
    event.stopPropagation()
    clearDescriptionHold()
    setDragState(null)
    onDragCard(null)
    setResizeState({
      cardId: card.id,
      startX: event.clientX,
      startY: event.clientY,
      startW: card.rect.w,
      startH: card.rect.h,
    })
    pendingResizeRectRef.current = { ...card.rect }
  }

  async function refreshLatestCanvas(): Promise<boolean> {
    setRefreshingCanvas(true)
    try {
      const response = await readCanvas({
        canvas_id: canvasIdRef.current,
        canvas_name: canvasNameRef.current,
      })
      if (!response.ok) {
        setPatchError(response.error || 'Could not refresh canvas')
        return false
      }
      const nextCanvas = canvasFromReadResponse(response, {
        ...activeCanvas,
        id: canvasIdRef.current,
        name: canvasNameRef.current,
        revision: canvasRevisionRef.current,
        ref: canvasRef,
        cards: cardsRef.current,
      })
      const split = splitCardsByTrash(cloneCards(nextCanvas.cards))
      canvasIdRef.current = nextCanvas.id
      canvasRevisionRef.current = nextCanvas.revision
      setCanvasId(nextCanvas.id)
      setCanvasRevision(nextCanvas.revision)
      setCanvasRef(nextCanvas.ref)
      setCards(split.active)
      setTrashedCards(split.trashed)
      setSelectedCardIds(new Set())
      setPatchError('')
      setRevisionConflict(null)
      return true
    } catch (error) {
      setPatchError(error instanceof Error ? error.message : String(error))
      return false
    } finally {
      setRefreshingCanvas(false)
    }
  }

  async function applyCardOperations(
    operations: CanvasPatchOp[],
    label: string,
    options: { retryOnConflict?: boolean } = {},
  ) {
    if (!operations.length) return
    const retryOnConflict = options.retryOnConflict ?? true
    const targetCanvasId = canvasIdRef.current
    const targetCanvasName = canvasNameRef.current
    const baseRevision = canvasRevisionRef.current
    try {
      setPatchError('')
      setRevisionConflict(null)
      const response = await patchCanvas({
        canvas_id: targetCanvasId,
        canvas_name: targetCanvasName,
        base_revision: baseRevision,
        patch: {
          canvas_id: targetCanvasId,
          canvas_name: targetCanvasName,
          base_revision: baseRevision,
          actor: 'user',
          operations,
        },
      })
      if (!response.ok) {
        if (isCanvasRevisionConflict(response)) {
          if (typeof response.current_revision === 'number' && Number.isFinite(response.current_revision)) {
            canvasRevisionRef.current = response.current_revision
            setCanvasRevision(response.current_revision)
          }
          if (retryOnConflict && await refreshLatestCanvas()) {
            await applyCardOperations(operations, label, { retryOnConflict: false })
            return
          }
          setRevisionConflict({
            label,
            operations,
            expectedRevision: response.expected_revision,
            currentRevision: response.current_revision,
          })
          return
        }
        if (isCanvasCardNotFoundResponse(response)) {
          const message = canvasPatchFailureMessage(response, `${label} failed`)
          await refreshLatestCanvas()
          setPatchError(`${message}; refreshed canvas from server.`)
          return
        }
        setPatchError(canvasPatchFailureMessage(response, `${label} failed`))
        return
      }
    const event = normalizeCanvasPatchEvent(response.ui_event ?? {
      type: 'canvas.patch.applied',
      source: 'canvas.patch',
      story_id: response.story_id,
      canvas_name: response.canvas_name,
      canvas_id: response.canvas_id,
      revision: response.revision,
      canvas_ref: response.canvas_ref,
      latest_ref: response.latest_ref,
      changed: response.changed,
      changed_cards: response.changed_cards,
      projection: response.projection,
    })
      if (event) {
        mergeCanvasEvent(event)
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      if (isCanvasCardNotFoundMessage(message)) {
        await refreshLatestCanvas()
        setPatchError(`${message}; refreshed canvas from server.`)
        return
      }
      setPatchError(message)
    }
  }

  function retryRevisionConflict() {
    const conflict = revisionConflict
    if (!conflict) return
    void applyCardOperations(conflict.operations, conflict.label)
  }

  function editUserTextCard(card: CanvasCard) {
    if (card.kind !== 'user.text') return
    const nextText = window.prompt('Edit user text card', card.summary)
    if (nextText == null) return
    const text = nextText.trim()
    if (!text) return
    void applyCardOperations([
      {
        op: 'update_card',
        card_id: card.id,
        content: { text },
      },
    ], 'Edit text')
  }

  function editCardDescription(card: CanvasCard) {
    const nextDescription = window.prompt('Card description', card.description || '')
    if (nextDescription == null) return
    void applyCardOperations([
      {
        op: 'update_card',
        card_id: card.id,
        set: { description: nextDescription.trim() },
      },
    ], 'Edit description')
  }

  function addCardComment(card: CanvasCard) {
    const text = window.prompt('Comment on this card')
    if (!text || !text.trim()) return
    void applyCardOperations([
      {
        op: 'comment_card',
        card_id: card.id,
        text: text.trim(),
      },
    ], 'Add comment')
  }

  async function runObjectAction(card: CanvasCard, action: CanvasObjectActionName) {
    if (!onObjectAction) return
    setResolverNoticeByCard((current) => ({ ...current, [card.id]: '' }))
    setResolverLoadingByCard((current) => ({ ...current, [card.id]: true }))
    try {
      const result = await onObjectAction(card, action)
      setResolverStateByCard((current) => ({
        ...current,
        [card.id]: { ...(current[card.id] || {}), ...result },
      }))
      if (!result.ok) {
        setResolverNoticeByCard((current) => ({
          ...current,
          [card.id]: result.error || result.message || 'Object action failed.',
        }))
        return
      }
      if (action === 'preview') {
        const text = result.text ||
          result.summary ||
          result.title ||
          (result.json ? JSON.stringify(result.json, null, 2).slice(0, 1200) : '')
        setResolverNoticeByCard((current) => ({
          ...current,
          [card.id]: text || 'Preview resolved.',
        }))
      } else if (action === 'open') {
        setResolverNoticeByCard((current) => ({ ...current, [card.id]: 'Open request sent.' }))
      } else if (action === 'download') {
        setResolverNoticeByCard((current) => ({ ...current, [card.id]: 'Download prepared.' }))
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setResolverNoticeByCard((current) => ({ ...current, [card.id]: message }))
    } finally {
      setResolverLoadingByCard((current) => ({ ...current, [card.id]: false }))
    }
  }

  function startDescriptionHold(card: CanvasCard, event: ReactPointerEvent<HTMLElement>) {
    if (event.button !== 0) return
    const target = event.target instanceof HTMLElement ? event.target : null
    if (target?.closest('button, input, label, select, textarea, a')) return
    clearDescriptionHold()
    suppressCardClickRef.current = false
    descriptionHoldStartRef.current = { cardId: card.id, x: event.clientX, y: event.clientY }
    descriptionHoldTimerRef.current = window.setTimeout(() => {
      descriptionHoldTimerRef.current = null
      descriptionHoldStartRef.current = null
      suppressCardClickRef.current = true
      editCardDescription(card)
    }, 560)
  }

  function moveDescriptionHold(event: ReactPointerEvent<HTMLElement>) {
    const start = descriptionHoldStartRef.current
    if (!start) return
    const dx = Math.abs(event.clientX - start.x)
    const dy = Math.abs(event.clientY - start.y)
    if (dx > 7 || dy > 7) {
      clearDescriptionHold()
    }
  }

  function selectCard(card: CanvasCard, event: ReactMouseEvent<HTMLElement>) {
    if (event.defaultPrevented) return
    if (suppressCardClickRef.current) {
      suppressCardClickRef.current = false
      event.preventDefault()
      return
    }
    const target = event.target instanceof HTMLElement ? event.target : null
    if (target?.closest('button, input, label, select, textarea, a')) return
    if (event.metaKey || event.ctrlKey || event.shiftKey) {
      setSelectedCardIds((current) => {
        const next = new Set(current)
        if (next.has(card.id)) {
          next.delete(card.id)
        } else {
          next.add(card.id)
        }
        return next
      })
      return
    }
    setSelectedCardIds(setFromIds([card.id]))
  }

  return (
    <section className="canvas-panel">
      <div className="canvas-header">
        <div className="canvas-title">
          <p className="canvas-title-line">
            <span>Canvas</span>
            <strong>{activeCanvas.name}</strong>
            <em>{canvasStats.pins} pins</em>
            <em>{canvasStats.pendingSuggestions} pending</em>
            <em>oldest {formatCardTime(canvasStats.oldest)}</em>
            <em>newest {formatCardTime(canvasStats.newest)}</em>
            <em>rev {canvasRevision}</em>
          </p>
        </div>
        <div className="canvas-filter" aria-label="Canvas pin filter">
          <button
            type="button"
            className={cardFilter === 'all' ? 'active' : ''}
            aria-pressed={cardFilter === 'all'}
            onClick={() => setCardFilter('all')}
          >
            All
          </button>
          <button
            type="button"
            className={cardFilter === 'suggestions' ? 'active' : ''}
            aria-pressed={cardFilter === 'suggestions'}
            onClick={() => setCardFilter('suggestions')}
          >
            Suggestions
          </button>
          <button
            type="button"
            className={cardFilter === 'board' ? 'active' : ''}
            aria-pressed={cardFilter === 'board'}
            onClick={() => setCardFilter('board')}
          >
            Board
          </button>
        </div>
        <div className="canvas-actions">
          <label>
            <span className="sr-only">Board</span>
            <select
              className="canvas-select"
              value={activeCanvas.name}
              onChange={(event) => onCanvasChange(event.target.value)}
            >
              {canvases.map((canvas) => (
                <option key={canvas.id} value={canvas.name}>
                  {canvas.name}
                </option>
              ))}
            </select>
          </label>
          <button className="secondary" onClick={() => onAttachCanvas(canvasContext(liveCanvas))}>
            <MessageSquarePlus size={16} />
            Pin canvas to chat
          </button>
          <button className="secondary icon-only" title="Close canvas" onClick={onCloseCanvas}>
            <X size={16} />
          </button>
        </div>
      </div>

      <div className="canvas-work-surface">
        {revisionConflict ? (
          <div className="canvas-conflict-panel" role="alert">
            <div className="canvas-conflict-copy">
              <strong>Canvas changed while you were editing.</strong>
              <span>
                {revisionConflict.label} used rev {revisionConflict.expectedRevision ?? 'old'};
                current board is rev {revisionConflict.currentRevision ?? canvasRevision}.
              </span>
            </div>
            <div className="canvas-conflict-actions">
              <button type="button" className="secondary" onClick={retryRevisionConflict}>
                Retry on latest
              </button>
              <button
                type="button"
                className="secondary"
                disabled={refreshingCanvas}
                onClick={() => void refreshLatestCanvas()}
              >
                {refreshingCanvas ? 'Refreshing...' : 'Refresh board'}
              </button>
              <button
                type="button"
                className="secondary icon-only"
                title="Dismiss conflict"
                onClick={() => setRevisionConflict(null)}
              >
                <X size={14} />
              </button>
            </div>
          </div>
        ) : null}
        {patchError ? (
          <div className="canvas-patch-error" role="alert">
            {patchError}
          </div>
        ) : null}
        <div
          ref={boardRef}
          className={`canvas-board ${externalDropReady ? 'external-drop-ready' : ''}`}
          aria-label="Task tracker canvas board"
          onPointerDown={startMarqueeSelection}
          onDragOver={(event) => {
            if (dragState) {
              event.preventDefault()
              event.dataTransfer.dropEffect = 'move'
              return
            }
            if (!hasExternalDropData(event)) return
            event.preventDefault()
            event.dataTransfer.dropEffect = 'copy'
            setExternalDropReady(true)
          }}
          onDragLeave={(event) => {
            if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
            setExternalDropReady(false)
          }}
          onDrop={(event) => {
            setExternalDropReady(false)
            if (!dragState || !boardRef.current) {
              if (!hasExternalDropData(event)) return
              event.preventDefault()
              handleExternalDrop(event)
              return
            }
            event.preventDefault()
            const rect = boardRef.current.getBoundingClientRect()
            const operations = moveCards(
              dragState.cardIds,
              event.clientX - rect.left - dragState.offsetX,
              event.clientY - rect.top - dragState.offsetY,
              dragState.cardOffsets,
            )
            setDragState(null)
            onDragCard(null)
            void applyCardOperations(operations, 'Move selected cards')
          }}
        >
          <div className="canvas-grid" />
          {marqueeState ? (
            <div
              className="canvas-marquee"
              style={{
                left: marqueeState.x,
                top: marqueeState.y,
                width: marqueeState.w,
                height: marqueeState.h,
              }}
            />
          ) : null}
          {selectedBounds && selectedVisibleCards.length > 1 ? (
            <div
              className={`canvas-selection-area ${dragState ? 'moving' : ''}`}
              draggable
              style={{
                left: selectedBounds.x - 8,
                top: selectedBounds.y - 8,
                width: selectedBounds.w + 16,
                height: selectedBounds.h + 16,
              }}
              title={`Drag ${selectedVisibleCards.length} selected pins`}
              onDragStart={(event) => startCardsDrag(selectedVisibleCards, event)}
              onDragEnd={() => {
                setDragState(null)
                onDragCard(null)
              }}
            >
              <span>{selectedVisibleCards.length} selected</span>
            </div>
          ) : null}
          {visibleCards.map((card) => {
            const Icon = iconForKind(card.kind)
            const context = cardContext(liveCanvas, card)
            const expanded = expandedCardId === card.id
            const pendingSuggestion = card.suggested && card.placement !== 'placed'
            const locallySelected = selectedCardIds.has(card.id)
            const dragged = dragState?.cardIds.includes(card.id)
            return (
              <article
                key={card.id}
                className={`canvas-card ${expanded ? 'expanded' : ''} ${dragged ? 'moving' : ''} ${card.selected || locallySelected ? 'selected' : ''} ${locallySelected ? 'multi-selected' : ''} ${pendingSuggestion ? 'suggested' : ''} ${card.kind.replace('.', '-')}`}
                draggable
                onClick={(event) => selectCard(card, event)}
                onDragStart={(event) => {
                  const selectedDragCards = selectedCardIds.has(card.id) && selectedVisibleCards.length > 1
                    ? selectedVisibleCards
                    : [card]
                  startCardsDrag(selectedDragCards, event)
                }}
                onDragEnd={() => {
                  setDragState(null)
                  onDragCard(null)
                }}
                style={{
                  left: card.rect.x,
                  top: card.rect.y,
                  width: expanded ? Math.max(card.rect.w, 292) : card.rect.w,
                  height: expanded ? Math.max(card.rect.h, 202) : card.rect.h,
                }}
              >
                <div className="canvas-card-top">
                  <span className="canvas-card-id">{card.id}</span>
                  <span className="canvas-card-origin" title={card.kind}>
                    <Icon size={15} />
                    {card.kind}
                  </span>
                  <span className="canvas-card-buttons">
                    <button
                      type="button"
                      title={expanded ? 'Collapse card' : 'Expand card'}
                      aria-expanded={expanded}
                      onClick={() => setExpandedCardId(expanded ? '' : card.id)}
                      onMouseDown={(event) => event.stopPropagation()}
                    >
                      {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                    </button>
                    <button
                      type="button"
                      title="Attach to chat"
                      onClick={() => onAttachCard(context)}
                      onMouseDown={(event) => event.stopPropagation()}
                    >
                      <MessageSquarePlus size={14} />
                    </button>
                  </span>
                </div>
                <div
                  className="canvas-card-text-zone"
                  title="Hold to edit description"
                  onPointerDown={(event) => startDescriptionHold(card, event)}
                  onPointerMove={moveDescriptionHold}
                  onPointerUp={clearDescriptionHold}
                  onPointerCancel={clearDescriptionHold}
                >
                  <h3>{card.title}</h3>
                  <p>{card.summary}</p>
                  {card.description ? (
                    <p className="canvas-card-description">{card.description}</p>
                  ) : null}
                </div>
                {expanded ? (
                  <>
                    <div className="canvas-card-edit-actions">
                      {(() => {
                        const resolverState = resolverStateByCard[card.id]
                        const capabilities = resolverState?.capabilities || {}
                        const loading = Boolean(resolverLoadingByCard[card.id])
                        return (
                          <>
                            {loading ? <span className="canvas-card-resolver-state">resolving</span> : null}
                            {capabilities.preview ? (
                              <button
                                type="button"
                                title="Preview the object through its resolver"
                                onClick={() => void runObjectAction(card, 'preview')}
                                onMouseDown={(event) => event.stopPropagation()}
                              >
                                <Eye size={12} />
                                Preview
                              </button>
                            ) : null}
                            {capabilities.open ? (
                              <button
                                type="button"
                                title="Open the object in its owning surface"
                                onClick={() => void runObjectAction(card, 'open')}
                                onMouseDown={(event) => event.stopPropagation()}
                              >
                                <Maximize2 size={12} />
                                Open
                              </button>
                            ) : null}
                            {capabilities.download ? (
                              <button
                                type="button"
                                title="Download the object through its resolver"
                                onClick={() => void runObjectAction(card, 'download')}
                                onMouseDown={(event) => event.stopPropagation()}
                              >
                                <Download size={12} />
                                Download
                              </button>
                            ) : null}
                          </>
                        )
                      })()}
                      {card.kind === 'user.text' ? (
                        <button
                          type="button"
                          title="Edit the hosted user-authored text behind this card"
                          onClick={() => editUserTextCard(card)}
                          onMouseDown={(event) => event.stopPropagation()}
                        >
                          <PenLine size={12} />
                          Edit text
                        </button>
                      ) : null}
                      <button
                        type="button"
                        title="Edit this card's custom canvas description"
                        onClick={() => editCardDescription(card)}
                        onMouseDown={(event) => event.stopPropagation()}
                      >
                        <ClipboardPenLine size={12} />
                        Description
                      </button>
                      <button
                        type="button"
                        title="Add a canvas comment to this card"
                        onClick={() => addCardComment(card)}
                        onMouseDown={(event) => event.stopPropagation()}
                      >
                        <MessageSquarePlus size={12} />
                        Comment
                      </button>
                    </div>
                    <dl className="canvas-card-detail">
                      <div>
                        <dt>ref</dt>
                        <dd>{card.ref || 'inline/local'}</dd>
                      </div>
                      <div>
                        <dt>mime</dt>
                        <dd>{card.mime || 'application/octet-stream'}</dd>
                      </div>
                      <div>
                        <dt>rect</dt>
                        <dd>x:{card.rect.x} y:{card.rect.y} w:{card.rect.w} h:{card.rect.h}</dd>
                      </div>
                      <div>
                        <dt>notes</dt>
                        <dd>{card.commentsCount ? `${card.commentsCount} comment${card.commentsCount === 1 ? '' : 's'}` : 'none'}</dd>
                      </div>
                    </dl>
                    {resolverNoticeByCard[card.id] ? (
                      <div className="canvas-card-resolver-preview">
                        {resolverNoticeByCard[card.id]}
                      </div>
                    ) : null}
                  </>
                ) : null}
                <span className="canvas-card-kind">
                  <Grip size={12} />
                  {pendingSuggestion ? 'pending suggestion · ' : ''}{card.kind}
                </span>
                <button
                  type="button"
                  className="canvas-card-resize"
                  title="Resize pin"
                  aria-label={`Resize ${card.title}`}
                  onPointerDown={(event) => startCardResize(card, event)}
                  onMouseDown={(event) => event.stopPropagation()}
                />
              </article>
            )
          })}
        </div>

        <div className="canvas-create-controls" aria-label="Create canvas cards">
          <button
            type="button"
            className="canvas-round-action"
            title="New user text card"
            aria-label="New user text card"
            onClick={createUserTextCard}
          >
            <PenLine size={16} />
          </button>
          <label
            className="canvas-round-action"
            title="New user attachment card"
            aria-label="New user attachment card"
          >
            <Paperclip size={16} />
            <input
              ref={attachmentInputRef}
              className="sr-only"
              type="file"
              multiple
              onChange={(event) => createUserAttachmentCard(event.target.files)}
            />
          </label>
        </div>

        <div
          className={`canvas-trash ${trashOpen ? 'open' : ''} ${dragState ? 'ready' : ''}`}
          aria-label="Canvas trash bin"
          onDragOver={handleTrashDragOver}
          onDrop={handleTrashDrop}
        >
          <button
            type="button"
            className="canvas-trash-button"
            title="Open canvas bin"
            onClick={() => setTrashOpen((open) => !open)}
            onDragOver={handleTrashDragOver}
            onDrop={handleTrashDrop}
          >
            <span aria-hidden="true">
              <Trash2 size={15} />
            </span>
            {trashedCards.length ? <small>{trashedCards.length}</small> : null}
          </button>
          {trashOpen ? (
            <section
              className="canvas-trash-popover"
              aria-label="Trashed canvas pins"
              onDragOver={handleTrashDragOver}
              onDrop={handleTrashDrop}
            >
              <header className="canvas-trash-head">
                <span>Bin</span>
                <button
                  type="button"
                  disabled={!trashedCards.length}
                  onClick={cleanTrash}
                  title="Clean bin"
                >
                  Clean
                </button>
              </header>
              {trashedCards.length ? (
                <div className="canvas-trash-list">
                  {trashedCards.map((card) => (
                    <span className="trash-chip" key={card.id}>
                      <span>
                        <strong>{card.id} {card.title}</strong>
                        <em>{card.kind}</em>
                      </span>
                      <button
                        type="button"
                        title="Restore pin"
                        onClick={() => restoreCard(card.id)}
                      >
                        <RotateCcw size={13} />
                      </button>
                    </span>
                  ))}
                </div>
              ) : (
                <p>Drop pins on the bin icon.</p>
              )}
            </section>
          ) : null}
        </div>
      </div>
    </section>
  )
}
