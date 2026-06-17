import {
  Archive,
  Check,
  Copy,
  Download,
  ExternalLink,
  FileText,
  Frame,
  Grip,
  Info,
  Maximize2,
  MessageSquare,
  MessageSquarePlus,
  Minimize2,
  Paperclip,
  PenLine,
  Pin,
  Plus,
  RotateCcw,
  Search,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import type { CanvasObjectActionName, CanvasObjectActionResponse, CanvasPatchInput, CanvasPatchOp, CanvasPatchResponse, CanvasReadInput, CanvasReadResponse, CanvasSearchInput, CanvasSearchItem, CanvasSearchResponse } from '@kdcube/components-core/canvas'
import { normalizeContext, normalizeContextMessage, type CanvasContextItem } from '@kdcube/components-core/canvas'
import { parseIngressMessage, type CanvasIngressPayload } from '@kdcube/components-core/canvas'
import {
  canvasContext,
  cardsFromProjection,
  cardsFromPatchEvent,
  cardContext,
  canvasFromReadResponse,
  findCanvas,
  namespaceFromRef,
  ownerKeyFromRef,
  normalizeCanvasPatchEvent,
  type CanvasCard,
  type CanvasDefinition,
  type CanvasPatchUiEvent,
} from '@kdcube/components-core/canvas'

export interface CanvasNamespaceStyle {
  label?: string
  color?: string
  ink?: string
  border?: string
  focus?: string
  background?: string
}

export type CanvasBrokeredDrop =
  | { kind: 'context'; context: CanvasContextItem }
  | { kind: 'ingress'; payload: CanvasIngressPayload }

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
  onCreateCanvas?: (name: string) => void
  onArchiveCanvas?: (canvas: CanvasDefinition) => void
  onDeleteCanvas?: (canvas: CanvasDefinition) => void
  onDropFiles: (files: File[], rect: CanvasCard['rect']) => void
  onDropText: (text: string, rect: CanvasCard['rect']) => void
  onDropContext: (context: CanvasContextItem, rect: CanvasCard['rect']) => void
  onDropIngress: (payload: CanvasIngressPayload, rect: CanvasCard['rect']) => void
  getBrokeredDrop?: () => CanvasBrokeredDrop | null
  onBrokeredDropHandled?: () => void
  onObjectAction?: (card: CanvasCard, action: CanvasObjectActionName) => Promise<CanvasObjectActionResponse>
  /** Hybrid pin search. When absent the search control is hidden. */
  onSearchPins?: (input: CanvasSearchInput) => Promise<CanvasSearchResponse>
  namespaceStyles?: Record<string, CanvasNamespaceStyle | string>
  /** HTML help shown behind the ⓘ icon. Comes from bundle config; when absent a built-in default is used. */
  infoHtml?: string
  /** Hide the toolbar close (✕) when the host already provides a close/dock
   *  control (e.g. the scene's floating-window chrome), to avoid two closes. */
  hideCloseControl?: boolean
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
  if (kind === 'object.ref') return ExternalLink
  if (kind === 'conversation') return MessageSquare
  return Pin
}

const NAMESPACE_BY_KIND: Record<string, string> = {
  'user.text': 'ut',
  'user.attachment': 'ua',
  'agent.text': 'at',
  'file': 'fi',
  'memory': 'mem',
  'source': 'src',
  'search.result': 'sr',
  'note': 'note',
  'object.ref': 'obj',
  'conversation': 'conv',
}

function namespaceForCard(card: CanvasCard): string {
  return namespaceFromRef(card.ref) || card.namespace || NAMESPACE_BY_KIND[card.kind] || card.kind.replace(/\./g, '-').slice(0, 4)
}

function namespaceLabelForCard(card: CanvasCard, namespaceStyles?: Record<string, CanvasNamespaceStyle | string>): string {
  const namespace = namespaceForCard(card)
  const style = namespaceStyles?.[namespace]
  if (style && typeof style === 'object' && typeof style.label === 'string' && style.label.trim()) {
    return style.label.trim()
  }
  return namespace
}

function namespaceCssForCard(
  card: CanvasCard,
  namespaceStyles?: Record<string, CanvasNamespaceStyle | string>,
): CSSProperties {
  const namespace = namespaceForCard(card)
  const raw = namespaceStyles?.[namespace]
  if (!raw) return {}
  const style: CanvasNamespaceStyle = typeof raw === 'string' ? { color: raw } : raw
  const ink = style.ink || style.color
  const border = style.border || style.color
  const focus = style.focus
  const background = style.background
  const vars: CSSProperties & Record<string, string> = {}
  if (ink) vars['--pin-ink'] = ink
  if (border) vars['--pin-border'] = border
  if (focus) vars['--pin-focus'] = focus
  if (background) vars['--pin-bg'] = background
  return vars
}

// Card timestamps arrive as ISO strings, epoch milliseconds, OR epoch SECONDS
// (the canvas store writes created_at/updated_at as int(time.time())). Date.parse
// can't read a bare seconds value, so normalize everything to epoch ms first.
function toEpochMs(value?: string | number | null): number | null {
  if (value == null || value === '') return null
  if (typeof value === 'number') return value < 1e12 ? value * 1000 : value
  const s = String(value).trim()
  if (/^\d+$/.test(s)) {
    const n = Number(s)
    return n < 1e12 ? n * 1000 : n   // 10-digit = seconds, 13-digit = ms
  }
  const parsed = Date.parse(s)
  return Number.isFinite(parsed) ? parsed : null
}

function formatCardAdded(value?: string | null): string {
  const ms = toEpochMs(value)
  if (ms == null) return ''
  const t = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}`
}

// Compact "added" stamp for the card footer (minute precision, no year).
function formatCardAddedShort(value?: string | null): string {
  const ms = toEpochMs(value)
  if (ms == null) return ''
  const t = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}`
}

// Copy text to the clipboard, with a fallback for iframes/older browsers.
async function copyTextToClipboard(text: string): Promise<boolean> {
  if (!text) return false
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // ignore and fall through to the legacy path
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

// --- Lightweight markdown (self-contained, no dependency) -------------------
// Supports headings, bold/italic, inline code, code fences, links, blockquotes
// and ordered/unordered lists — enough for canvas text, descriptions, and
// comments without pulling a markdown library into the shared component.
function renderInlineMarkdown(text: string): ReactNode[] {
  const out: ReactNode[] = []
  let rest = text
  let key = 0
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*|_[^_\n]+_)|(\[[^\]]+\]\([^)\s]+\))/
  while (rest.length) {
    const m = re.exec(rest)
    if (!m) {
      out.push(<Fragment key={key++}>{rest}</Fragment>)
      break
    }
    if (m.index > 0) out.push(<Fragment key={key++}>{rest.slice(0, m.index)}</Fragment>)
    const tok = m[0]
    if (tok.startsWith('`')) out.push(<code key={key++}>{tok.slice(1, -1)}</code>)
    else if (tok.startsWith('**')) out.push(<strong key={key++}>{tok.slice(2, -2)}</strong>)
    else if (tok.startsWith('[')) {
      const lm = /\[([^\]]+)\]\(([^)\s]+)\)/.exec(tok)
      if (lm) out.push(<a key={key++} href={lm[2]} target="_blank" rel="noopener noreferrer">{lm[1]}</a>)
      else out.push(<Fragment key={key++}>{tok}</Fragment>)
    } else out.push(<em key={key++}>{tok.slice(1, -1)}</em>)
    rest = rest.slice(m.index + tok.length)
  }
  return out
}

function Markdown({ text }: { text: string }) {
  const src = (text || '').replace(/\r\n/g, '\n')
  if (!src.trim()) return null
  const lines = src.split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let key = 0
  const isBlockStart = (l: string) => /^(#{1,6}\s|>|[-*]\s|\d+\.\s|```)/.test(l)
  while (i < lines.length) {
    const line = lines[i]
    if (line.trim() === '') { i++; continue }
    if (/^```/.test(line)) {
      const buf: string[] = []
      i++
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++ }
      if (i < lines.length) i++
      blocks.push(<pre key={key++}><code>{buf.join('\n')}</code></pre>)
      continue
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) {
      const lvl = Math.min(6, h[1].length + 2)
      const Tag = (`h${lvl}` as unknown) as 'h4'
      blocks.push(<Tag key={key++}>{renderInlineMarkdown(h[2])}</Tag>)
      i++
      continue
    }
    if (/^>\s?/.test(line)) {
      const buf: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^>\s?/, '')); i++ }
      blocks.push(<blockquote key={key++}>{renderInlineMarkdown(buf.join(' '))}</blockquote>)
      continue
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) { items.push(lines[i].replace(/^[-*]\s+/, '')); i++ }
      blocks.push(<ul key={key++}>{items.map((it, j) => <li key={j}>{renderInlineMarkdown(it)}</li>)}</ul>)
      continue
    }
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) { items.push(lines[i].replace(/^\d+\.\s+/, '')); i++ }
      blocks.push(<ol key={key++}>{items.map((it, j) => <li key={j}>{renderInlineMarkdown(it)}</li>)}</ol>)
      continue
    }
    const buf: string[] = []
    while (i < lines.length && lines[i].trim() !== '' && !isBlockStart(lines[i])) { buf.push(lines[i]); i++ }
    blocks.push(<p key={key++}>{renderInlineMarkdown(buf.join(' '))}</p>)
  }
  return <div className="canvas-md">{blocks}</div>
}

// Reusable in-card editor: a raw textarea with a Raw/Rendered switch and
// tick (save) / x (cancel) controls. Used for new user text, descriptions,
// and comments so editing always happens inline, never in a browser dialog.
function InlineMarkdownEditor({
  value,
  onChange,
  onSave,
  onCancel,
  placeholder,
  saveLabel,
  saveDisabled,
}: {
  value: string
  onChange: (next: string) => void
  onSave: () => void
  onCancel?: () => void
  placeholder?: string
  saveLabel?: string
  saveDisabled?: boolean
}) {
  const [mode, setMode] = useState<'raw' | 'rendered'>('raw')
  return (
    <div className="canvas-mde" onPointerDown={(event) => event.stopPropagation()}>
      <div className="canvas-mde-bar">
        <div className="canvas-mde-tabs">
          <button type="button" className={mode === 'raw' ? 'on' : ''} onClick={() => setMode('raw')}>Raw</button>
          <button type="button" className={mode === 'rendered' ? 'on' : ''} onClick={() => setMode('rendered')}>Rendered</button>
        </div>
        <span className="canvas-mde-spacer" />
        {onCancel ? (
          <button type="button" className="canvas-mde-cancel" title="Cancel" aria-label="Cancel" onClick={onCancel}>
            <X size={13} />
          </button>
        ) : null}
        <button
          type="button"
          className="canvas-mde-save"
          title={saveLabel || 'Save'}
          aria-label={saveLabel || 'Save'}
          disabled={saveDisabled}
          onClick={onSave}
        >
          <Check size={13} />
        </button>
      </div>
      {mode === 'raw' ? (
        <textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          autoFocus
        />
      ) : (
        <div className="canvas-mde-preview">
          {value.trim() ? <Markdown text={value} /> : <p className="empty">Nothing to preview.</p>}
        </div>
      )}
    </div>
  )
}

// Built-in help shown behind the ⓘ icon when the bundle config supplies no
// `infoHtml`. Covers the general concept plus the canvas built-ins: user text,
// attachments, and chat pins (conversations and files from chat).
const CANVAS_DEFAULT_INFO_HTML = `
<h3>Pin board</h3>
<p>Keep things in quick reach — as <strong>pins</strong>, links to objects from anywhere in the system, and the assistant understands them. A pin is a proxy; the real object stays in its own app.</p>
<p><strong>Pinning isn't sending.</strong> To use a pin in chat, drag it in — one pin for that object, or the whole board to share its top pins at once. From a pin you can also open it in its app or download it.</p>
<h4>What goes on it</h4>
<ul>
  <li><strong>Your text</strong> — notes or drafts (markdown).</li>
  <li><strong>Attachments</strong> — files you drop in.</li>
  <li><strong>Chat pins</strong> — conversations and files from chat.</li>
</ul>
<p>Pins connect things to actions: once shared, the assistant can use them as sources to build on, or act on them in the apps they live in — and it can add, comment on, or refine cards.</p>
`.trim()

// Per-file size cap for files dropped/uploaded onto the board. Oversize
// files are rejected client-side with a message rather than failing the
// upload server-side.
const MAX_CANVAS_FILE_BYTES = 25 * 1024 * 1024
const MAX_CANVAS_FILE_LABEL = '25 MB'

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
}

function safeDownloadName(value: unknown): string {
  const raw = String(value || '').split(/[\\/]/).pop()?.trim() || 'download'
  return raw.replace(/[\x00-\x1f<>:"|?*]+/g, '_').replace(/\s+/g, ' ').slice(0, 180) || 'download'
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
}

function stringValue(value: unknown): string {
  return value == null ? '' : String(value).trim()
}

function numberValue(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

function providerObjectParts(value: unknown): {
  identity: Record<string, unknown>
  meta: Record<string, unknown>
  body: Record<string, unknown>
} {
  const raw = asRecord(value) || {}
  return {
    identity: asRecord(raw.identity) || {},
    meta: asRecord(raw.meta) || {},
    body: asRecord(raw.body) || {},
  }
}

function providerObjectAttachmentContexts(
  card: CanvasCard,
  resolverState?: CanvasObjectActionResponse,
): CanvasContextItem[] {
  const { identity, body } = providerObjectParts(resolverState?.object)
  const attachments = Array.isArray(body.attachments) ? body.attachments : []
  const issueId = stringValue(body.issue_id || body.id || identity.object_id)
  return attachments.map<CanvasContextItem | null>((item, index) => {
    const attachment = asRecord(item)
    if (!attachment) return null
    const ref = stringValue(
      attachment.logical_path ||
      attachment.logicalPath ||
      attachment.hosted_uri ||
      attachment.hostedUri ||
      attachment.ref ||
      attachment.object_ref ||
      attachment.objectRef,
    )
    if (!ref) return null
    const attachmentId = stringValue(
      attachment.id ||
      attachment.attachment_id ||
      attachment.attachmentId ||
      ref,
    )
    const filename = stringValue(attachment.filename || attachment.name) || safeDownloadName(ref)
    const mime = stringValue(attachment.mime || attachment.mime_type || attachment.mimeType) || 'application/octet-stream'
    const sizeBytes = numberValue(attachment.size_bytes || attachment.sizeBytes || attachment.size)
    const version = numberValue(attachment.version)
    const objectKind = stringValue(attachment.object_kind || attachment.objectKind || attachment.kind) ||
      (namespaceFromRef(ref) ? `${namespaceFromRef(ref)}.attachment` : 'object.attachment')
    const summaryParts = [
      issueId,
      mime,
      sizeBytes !== undefined ? `${sizeBytes} bytes` : '',
    ].filter(Boolean)
    return {
      id: ref,
      kind: objectKind,
      label: filename,
      summary: summaryParts.join(' · '),
      ref,
      logical_path: ref,
      hosted_uri: stringValue(attachment.hosted_uri || attachment.hostedUri) || ref,
      mime,
      card_id: card.id,
      card_type: card.kind,
      event_source_id: stringValue(resolverState?.event_source_id) || 'named_services.object',
      data: {
        parent_card_id: card.id,
        parent_object_ref: stringValue(resolverState?.object_ref || resolverState?.ref || card.ref),
        issue_id: issueId,
        attachment_id: attachmentId,
        object_ref: ref,
        object_kind: objectKind,
        filename,
        mime,
        size_bytes: sizeBytes,
        version,
        row: index,
      },
    } satisfies CanvasContextItem
  }).filter((item): item is CanvasContextItem => item !== null)
}

function triggerBase64Download(
  contentBase64: string | undefined,
  filename: string,
  mime = 'application/octet-stream',
): boolean {
  if (!contentBase64) return false
  const comma = contentBase64.indexOf(',')
  const encoded = comma >= 0 ? contentBase64.slice(comma + 1) : contentBase64
  const binary = window.atob(encoded)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  const blob = new Blob([bytes], { type: mime || 'application/octet-stream' })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = safeDownloadName(filename)
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => window.URL.revokeObjectURL(url), 1000)
  return true
}

function triggerUrlDownload(downloadUrl: string | undefined, filename: string): boolean {
  const url = String(downloadUrl || '').trim()
  if (!url) return false
  const link = document.createElement('a')
  link.href = url
  link.download = safeDownloadName(filename)
  link.rel = 'noopener'
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
  return true
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
  return toEpochMs(value)
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

// Escape a value for use inside a quoted CSS attribute selector. Card ids carry
// colons (`mem:record:…`), so CSS.escape (identifier escaping) is wrong here —
// only `"` and `\` need escaping inside the quoted value.
function cssAttrValue(value: string): string {
  return String(value).replace(/["\\]/g, '\\$&')
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
  onCreateCanvas,
  onArchiveCanvas,
  onDeleteCanvas,
  onDropFiles,
  onDropText,
  onDropContext,
  onDropIngress,
  getBrokeredDrop,
  onBrokeredDropHandled,
  onObjectAction,
  onSearchPins,
  namespaceStyles,
  infoHtml,
  hideCloseControl,
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
  const [expandFlipUp, setExpandFlipUp] = useState(false)
  // Max height for the open flyout, capped per-open to the room available in its
  // chosen direction so the board's own bounds clip it (it scrolls inside) — never
  // the card/board interior. Keeps the detail readable even on a small canvas.
  const [expandMaxHeight, setExpandMaxHeight] = useState(380)
  const [copiedCardId, setCopiedCardId] = useState<string>('')
  const [infoOpen, setInfoOpen] = useState(false)
  // Local draft for a new user-text card: edited inline (markdown) before it is
  // committed to the board, so creating text never opens a browser dialog.
  const [textDraft, setTextDraft] = useState<{ rect: CanvasCard['rect']; text: string } | null>(null)
  const [descDraftByCard, setDescDraftByCard] = useState<Record<string, string>>({})
  const [commentDraftByCard, setCommentDraftByCard] = useState<Record<string, string>>({})
  const [resolverStateByCard, setResolverStateByCard] = useState<Record<string, CanvasObjectActionResponse>>({})
  const [resolverLoadingByCard, setResolverLoadingByCard] = useState<Record<string, boolean>>({})
  const [resolverNoticeByCard, setResolverNoticeByCard] = useState<Record<string, string>>({})
  const [cardFilter, setCardFilter] = useState<CanvasCardFilter>('all')
  // Pin search. `searchResults === null` means search is inactive (full board);
  // a non-null array (even empty) means search-filter mode is engaged.
  const [searchInput, setSearchInput] = useState<string>('')
  const [searchScope, setSearchScope] = useState<'board' | 'all'>('board')
  const [searchResults, setSearchResults] = useState<CanvasSearchItem[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<string>('')
  const [patchError, setPatchError] = useState<string>('')
  const [revisionConflict, setRevisionConflict] = useState<CanvasRevisionConflict | null>(null)
  const [refreshingCanvas, setRefreshingCanvas] = useState(false)
  // In-app board dialogs (replace the browser prompt/confirm).
  const [boardDialog, setBoardDialog] = useState<null | { mode: 'create' | 'delete' }>(null)
  const [boardNameDraft, setBoardNameDraft] = useState('')
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
    if (!card?.ref || resolverLoadingByCard[card.id]) return
    const existing = resolverStateByCard[card.id]
    if (
      existing &&
      (
        existing.action === 'preview' ||
        existing.capabilities?.preview !== true ||
        providerObjectAttachmentContexts(card, existing).length > 0
      )
    ) return
    setResolverLoadingByCard((current) => ({ ...current, [card.id]: true }))
    const loadAction = async () => {
      const capabilitiesResult = existing || await onObjectAction(card, 'capabilities')
      let result = capabilitiesResult
      if (capabilitiesResult.ok && capabilitiesResult.capabilities?.preview === true) {
        const previewResult = await onObjectAction(card, 'preview')
        result = {
          ...capabilitiesResult,
          ...previewResult,
          capabilities: previewResult.capabilities || capabilitiesResult.capabilities,
        }
      }
      setResolverStateByCard((current) => ({
        ...current,
        [card.id]: {
          ...(current[card.id] || {}),
          ...result,
          capabilities: result.capabilities || current[card.id]?.capabilities,
        },
      }))
      if (!result.ok) {
        setResolverNoticeByCard((current) => ({
          ...current,
          [card.id]: result.error || result.message || 'Resolver is not available for this object.',
        }))
      }
    }
    loadAction()
      .catch((error) => {
        const message = error instanceof Error ? error.message : String(error)
        if (existing) {
          setResolverStateByCard((current) => ({ ...current, [card.id]: existing }))
        }
        if (!existing) {
          setResolverNoticeByCard((current) => ({
            ...current,
            [card.id]: message,
          }))
        }
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
  const enumByCardId = useMemo(() => {
    const out: Record<string, string> = {}
    const sorted = [...cards].sort((a, b) => {
      const ta = a.createdAt || ''
      const tb = b.createdAt || ''
      if (ta !== tb) return ta < tb ? -1 : 1
      return a.id < b.id ? -1 : 1
    })
    const counts: Record<string, number> = {}
    for (const card of sorted) {
      // Show the FULL ref path (all segments, colon-separated) — e.g. mem:record,
      // task:issue, task:attachment — not the kind-derived label ("memory"). The
      // counter is per distinct path. Ref-less cards fall back to the kind ns.
      const key = (card.ref ? ownerKeyFromRef(card.ref) : '') || namespaceLabelForCard(card, namespaceStyles)
      counts[key] = (counts[key] || 0) + 1
      out[card.id] = `${key}:${String(counts[key]).padStart(2, '0')}`
    }
    return out
  }, [cards, namespaceStyles])
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

  // --- Pin search (hybrid; results applied as a temporary board filter) ---
  const searchActive = searchResults !== null
  // Ids of cards on THIS board that matched, so non-matches recede and matches
  // are highlighted. Cross-board hits (all-boards scope) aren't on this board;
  // clicking one switches boards.
  const matchedCardIds = useMemo(() => {
    if (!searchResults) return null
    const liveIds = new Set(cards.map((card) => card.id))
    return new Set(searchResults.map((hit) => hit.card_id).filter((id) => liveIds.has(id)))
  }, [searchResults, cards])
  const boardMatchCount = matchedCardIds ? matchedCardIds.size : 0

  const exitPinSearch = useCallback(() => {
    setSearchResults(null)
    setSearchError('')
  }, [])

  // The × / Esc clear the query text too (exitPinSearch only drops the filter).
  const clearSearch = useCallback(() => {
    setSearchInput('')
    exitPinSearch()
  }, [exitPinSearch])

  // Esc closes an open card.
  useEffect(() => {
    if (!expandedCardId) return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setExpandedCardId('') }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [expandedCardId])

  // Open/close a card's detail flyout. Only one is open at a time. When opening,
  // pick the flyout direction from geometry: if the card sits near the board
  // bottom, open upward so the panel isn't clipped off-screen.
  const openCard = useCallback((cardId: string, willOpen: boolean) => {
    if (!willOpen) {
      setExpandedCardId('')
      return
    }
    let flipUp = false
    let maxH = 380
    const board = boardRef.current
    const node = board?.querySelector(`[data-card-id="${cssAttrValue(cardId)}"]`) as HTMLElement | null
    if (board && node) {
      const b = board.getBoundingClientRect()
      const c = node.getBoundingClientRect()
      const roomBelow = b.bottom - c.bottom
      const roomAbove = c.top - b.top
      // Open downward unless the full panel can't fit there and there's more room
      // above; then cap the panel to the room actually available in that direction
      // (minus a small gutter) so the board clips it instead of the interior — the
      // panel scrolls inside. Stays usable even when the canvas is small.
      flipUp = roomBelow < 396 && roomAbove > roomBelow
      const room = flipUp ? roomAbove : roomBelow
      maxH = Math.min(380, Math.max(0, Math.floor(room - 12)))
    }
    setExpandFlipUp(flipUp)
    setExpandMaxHeight(maxH)
    setExpandedCardId(cardId)
  }, [])

  const runPinSearch = useCallback(async () => {
    const query = searchInput.trim()
    if (!onSearchPins || !query) {
      exitPinSearch()
      return
    }
    setSearching(true)
    setSearchError('')
    try {
      const response = await onSearchPins({ query, allBoards: searchScope === 'all' })
      if (response.ok === false) {
        setSearchError(response.error || 'Search failed.')
        setSearchResults([])
        return
      }
      setSearchResults(response.items || response.results || [])
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : 'Search failed.')
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }, [exitPinSearch, onSearchPins, searchInput, searchScope])

  // Changing the placement filter or the board exits search (its result set no
  // longer applies); the user re-runs against the new context.
  useEffect(() => {
    exitPinSearch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCanvasName, cardFilter])

  const handleSearchResultClick = useCallback((hit: CanvasSearchItem) => {
    const onThisBoard = cards.some((card) => card.id === hit.card_id)
    if (!onThisBoard && hit.board) {
      // Cross-board hit (all-boards scope): jump to its board (search clears).
      const target = canvases.find((c) => c.id === hit.board || c.name === hit.board)
      if (target) {
        onCanvasChange(target.name)
        return
      }
    }
    setSelectedCardIds(setFromIds([hit.card_id]))
    const node = boardRef.current?.querySelector(`[data-card-id="${cssAttrValue(hit.card_id)}"]`)
    if (node) node.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' })
  }, [cards, canvases, onCanvasChange])

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
    // Canonical context-pin envelope: always the plural `contexts: [...]` shape
    // (even for one card), so every consumer (canvas drop, chat composer) reads
    // it uniformly. A bare/singular payload fell through to text on drop.
    return JSON.stringify({
      type: 'kdcube.context.attach',
      source: 'sdk-canvas',
      contexts: contextsForCards(inputCards),
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
    // The board scrolls (overflow:auto), so a viewport-relative drop point
    // must be shifted by the current scroll offset to land at the right
    // content coordinate.
    return {
      x: Math.round(clamp(event.clientX - bounds.left - width / 2, 8, Math.max(8, bounds.width - width - 8))) + board.scrollLeft,
      y: Math.round(clamp(event.clientY - bounds.top - height / 2, 8, Math.max(8, bounds.height - height - 8))) + board.scrollTop,
      w: width,
      h: height,
    }
  }

  function boardPoint(event: PointerEvent | ReactPointerEvent<HTMLElement>): { x: number; y: number } | null {
    const board = boardRef.current
    if (!board) return null
    const bounds = board.getBoundingClientRect()
    return {
      x: Math.round(clamp(event.clientX - bounds.left, 0, bounds.width)) + board.scrollLeft,
      y: Math.round(clamp(event.clientY - bounds.top, 0, bounds.height)) + board.scrollTop,
    }
  }

  // Pan the scrolled board so the cards' bounding box comes into the current
  // viewport — the way to reach pins that fell outside a small/resized window.
  function fitToView() {
    const board = boardRef.current
    if (!board) return
    const bounds = cardsBounds(cardsRef.current)
    if (!bounds) {
      board.scrollTo({ left: 0, top: 0, behavior: 'smooth' })
      return
    }
    board.scrollTo({
      left: Math.max(0, Math.round(bounds.x - 24)),
      top: Math.max(0, Math.round(bounds.y - 24)),
      behavior: 'smooth',
    })
  }

  // Create a new board. The empty canvas is created in the host's state and
  // persisted server-side on its first pin (a canvas.patch to a new name).
  function handleCreateBoard() {
    if (!onCreateCanvas) return
    setBoardNameDraft('')
    setBoardDialog({ mode: 'create' })
  }

  function handleDeleteBoard() {
    if (!onDeleteCanvas) return
    setBoardDialog({ mode: 'delete' })
  }

  // Archive is intentionally not surfaced yet: there is no un-archive flow, so a
  // board would become unreachable. `onArchiveCanvas` stays wired for when it lands.
  function handleArchiveBoard() {
    if (!onArchiveCanvas) return
    onArchiveCanvas(activeCanvas)
  }

  function confirmBoardDialog() {
    if (!boardDialog) return
    if (boardDialog.mode === 'create') {
      const name = boardNameDraft.trim()
      if (!name) return
      setBoardDialog(null)
      if (canvases.some((canvas) => canvas.name === name)) {
        onCanvasChange(name)
        return
      }
      onCreateCanvas?.(name)
      return
    }
    setBoardDialog(null)
    onDeleteCanvas?.(activeCanvas)
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

  // Empty-board drag: pan the scrolled board with the mouse (a hand tool) so
  // the user can bring the part they want into the viewport. Hold Shift to
  // rubber-band select instead.
  function startBoardGesture(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    if (event.target !== event.currentTarget && !(event.target instanceof HTMLElement && event.target.classList.contains('canvas-grid'))) {
      return
    }
    // Clicking the empty board closes any open card.
    setExpandedCardId('')
    if (event.shiftKey) {
      startMarqueeSelection(event)
      return
    }
    startPan(event)
  }

  function startPan(event: ReactPointerEvent<HTMLDivElement>) {
    const board = boardRef.current
    if (!board) return
    event.preventDefault()
    const startX = event.clientX
    const startY = event.clientY
    const startLeft = board.scrollLeft
    const startTop = board.scrollTop
    board.classList.add('panning')
    try { board.setPointerCapture(event.pointerId) } catch { /* not all browsers */ }
    const move = (move_event: PointerEvent) => {
      board.scrollLeft = startLeft - (move_event.clientX - startX)
      board.scrollTop = startTop - (move_event.clientY - startY)
    }
    const up = () => {
      board.classList.remove('panning')
      try { board.releasePointerCapture(event.pointerId) } catch { /* already released */ }
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up, { once: true })
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

  function startTextDraft() {
    setTextDraft({ rect: newCardRect(248, 150), text: '' })
  }

  function commitTextDraft() {
    const draft = textDraft
    if (!draft) return
    const text = draft.text.trim()
    if (text) onDropText(text, draft.rect)
    setTextDraft(null)
  }

  // Drop files over the per-file size cap and surface a message naming
  // them; only the within-limit files are pinned.
  function acceptCanvasFiles(files: File[]): File[] {
    const oversize = files.filter((file) => file.size > MAX_CANVAS_FILE_BYTES)
    if (oversize.length) {
      const names = oversize.map((file) => `${file.name} (${formatFileSize(file.size)})`).join(', ')
      setPatchError(
        `${oversize.length === 1 ? 'File is' : `${oversize.length} files are`} larger than the ${MAX_CANVAS_FILE_LABEL} per-file limit and ${oversize.length === 1 ? 'was' : 'were'} not pinned: ${names}`,
      )
    }
    return files.filter((file) => file.size <= MAX_CANVAS_FILE_BYTES)
  }

  function createUserAttachmentCard(files: FileList | null) {
    const selected = acceptCanvasFiles(Array.from(files || []).filter(Boolean))
    if (attachmentInputRef.current) {
      attachmentInputRef.current.value = ''
    }
    if (!selected.length) return
    onDropFiles(selected, newCardRect(260, 120))
  }

  function handleExternalDrop(event: DragEvent<HTMLElement>) {
    const rawDroppedFiles = Array.from(event.dataTransfer.files || []).filter(Boolean)
    if (rawDroppedFiles.length) {
      const droppedFiles = acceptCanvasFiles(rawDroppedFiles)
      if (droppedFiles.length) {
        onDropFiles(droppedFiles, dropRect(event, 260, 120))
      }
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
        // Invalid JSON is not a context payload. Fall through to text handling.
      }
    }

    const brokeredDrop = getBrokeredDrop?.()
    if (brokeredDrop?.kind === 'ingress') {
      onDropIngress(brokeredDrop.payload, dropRect(event, 246, 112))
      onBrokeredDropHandled?.()
      return
    }
    if (brokeredDrop?.kind === 'context') {
      onDropContext(brokeredDrop.context, dropRect(event, 224, 104))
      onBrokeredDropHandled?.()
      return
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

  function startDescriptionEdit(card: CanvasCard) {
    setExpandedCardId(card.id)
    setDescDraftByCard((current) => ({ ...current, [card.id]: card.description || '' }))
  }

  function updateDescriptionDraft(cardId: string, value: string) {
    setDescDraftByCard((current) => ({ ...current, [cardId]: value }))
  }

  function cancelDescriptionEdit(cardId: string) {
    setDescDraftByCard((current) => {
      const next = { ...current }
      delete next[cardId]
      return next
    })
  }

  function commitDescriptionEdit(card: CanvasCard) {
    const draft = descDraftByCard[card.id]
    if (draft === undefined) return
    void applyCardOperations([
      {
        op: 'update_card',
        card_id: card.id,
        set: { description: draft.trim() },
      },
    ], 'Edit description')
    cancelDescriptionEdit(card.id)
  }

  function updateCommentDraft(cardId: string, value: string) {
    setCommentDraftByCard((current) => ({ ...current, [cardId]: value }))
  }

  function commitComment(card: CanvasCard) {
    const draft = (commentDraftByCard[card.id] || '').trim()
    if (!draft) return
    void applyCardOperations([
      {
        op: 'comment_card',
        card_id: card.id,
        text: draft,
      },
    ], 'Add comment')
    setCommentDraftByCard((current) => {
      const next = { ...current }
      delete next[card.id]
      return next
    })
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
        const opened = Boolean(result.ui_event || result.resolved)
        setResolverNoticeByCard((current) => ({
          ...current,
          [card.id]: opened ? 'Open request sent.' : (result.message || 'Resolver returned no open target.'),
        }))
      } else if (action === 'download') {
        const filename = result.filename || result.title || card.title || card.id
        const downloaded = triggerUrlDownload(result.download_url, filename) || triggerBase64Download(
          result.content_base64,
          filename,
          result.mime || card.mime || 'application/octet-stream',
        )
        setResolverNoticeByCard((current) => ({
          ...current,
          [card.id]: downloaded ? `Downloaded ${safeDownloadName(filename)}.` : (result.message || 'Resolver returned no downloadable content.'),
        }))
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
      startDescriptionEdit(card)
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
    // A plain click opens the card's details (toggle). Drag is suppressed above via
    // suppressCardClickRef, and clicks on buttons/inputs already returned early.
    openCard(card.id, expandedCardId !== card.id)
  }

  return (
    <section className={`canvas-panel ${onSearchPins ? 'has-search' : ''} ${searchActive ? 'is-searching' : ''}`}>
      <div className="canvas-header">
        <div className="canvas-title">
          <p className="canvas-title-line">
            <button
              type="button"
              className="canvas-title-info"
              onClick={() => setInfoOpen(true)}
              title="What is this board?"
              aria-label="What is this board?"
            >
              <Info size={14} />
            </button>
            <span>Pin Board</span>
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
          {onCreateCanvas ? (
            <button className="secondary icon-only" onClick={handleCreateBoard} title="New board">
              <Plus size={16} />
            </button>
          ) : null}
          <button
            className="secondary icon-only"
            onClick={() => void refreshLatestCanvas()}
            title="Refresh board"
            disabled={refreshingCanvas}
          >
            <RotateCcw size={16} />
          </button>
          <button className="secondary icon-only" onClick={fitToView} title="Fit pins into view">
            <Frame size={16} />
          </button>
          {/* Archive hidden until an un-archive flow exists (a board would
              otherwise become unreachable). Re-enable with onArchiveCanvas + handleArchiveBoard. */}
          {onDeleteCanvas ? (
            <button className="secondary icon-only" onClick={handleDeleteBoard} title="Delete this board">
              <Trash2 size={16} />
            </button>
          ) : null}
          <button className="secondary" onClick={() => onAttachCanvas(canvasContext(liveCanvas))} title="Pin board to chat">
            <MessageSquarePlus size={16} />
            Pin board to chat
          </button>
          {!hideCloseControl ? (
            <button className="secondary icon-only" title="Close canvas" onClick={onCloseCanvas}>
              <X size={16} />
            </button>
          ) : null}
        </div>
      </div>

      {infoOpen ? (
        <div className="canvas-info-overlay" role="dialog" aria-modal="true" aria-label="About this board" onClick={() => setInfoOpen(false)}>
          <div className="canvas-info-panel" onClick={(event) => event.stopPropagation()}>
            <button
              type="button"
              className="canvas-info-close"
              title="Close"
              aria-label="Close"
              onClick={() => setInfoOpen(false)}
            >
              <X size={16} />
            </button>
            <div
              className="canvas-info-body canvas-md"
              dangerouslySetInnerHTML={{ __html: (infoHtml && infoHtml.trim()) ? infoHtml : CANVAS_DEFAULT_INFO_HTML }}
            />
          </div>
        </div>
      ) : null}

      {boardDialog ? (
        <div
          className="canvas-modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={boardDialog.mode === 'create' ? 'New board' : 'Delete board'}
          onClick={() => setBoardDialog(null)}
        >
          <div className="canvas-modal" onClick={(event) => event.stopPropagation()}>
            {boardDialog.mode === 'create' ? (
              <>
                <h3>New board</h3>
                <p>Give your new pin board a name.</p>
                <input
                  className="canvas-modal-input"
                  autoFocus
                  value={boardNameDraft}
                  placeholder="Board name"
                  onChange={(event) => setBoardNameDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') { event.preventDefault(); confirmBoardDialog() }
                    if (event.key === 'Escape') setBoardDialog(null)
                  }}
                />
                <div className="canvas-modal-actions">
                  <button type="button" className="secondary" onClick={() => setBoardDialog(null)}>Cancel</button>
                  <button type="button" className="canvas-modal-go" disabled={!boardNameDraft.trim()} onClick={confirmBoardDialog}>
                    Create board
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3>Delete board</h3>
                <p>Delete <strong>{activeCanvas.name}</strong> and all its pins? This can’t be undone.</p>
                <div className="canvas-modal-actions">
                  <button type="button" className="secondary" onClick={() => setBoardDialog(null)}>Cancel</button>
                  <button type="button" className="canvas-modal-danger" onClick={confirmBoardDialog}>Delete board</button>
                </div>
              </>
            )}
          </div>
        </div>
      ) : null}

      {onSearchPins ? (
        <div className="canvas-search">
          <form
            className="canvas-search-bar"
            onSubmit={(event) => { event.preventDefault(); void runPinSearch() }}
          >
            <div className="canvas-search-field">
              <Search size={15} />
              <input
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Escape') clearSearch() }}
                placeholder="Search pins — semantic + lexical"
                aria-label="Search pins"
              />
              {searchInput ? (
                <button
                  type="button"
                  className="canvas-search-clear"
                  title="Clear"
                  aria-label="Clear search"
                  onClick={clearSearch}
                >
                  <X size={13} />
                </button>
              ) : null}
            </div>
            <div className="canvas-search-scope" role="group" aria-label="Search scope">
              <button
                type="button"
                className={searchScope === 'board' ? 'active' : ''}
                aria-pressed={searchScope === 'board'}
                onClick={() => setSearchScope('board')}
              >
                This board
              </button>
              <button
                type="button"
                className={searchScope === 'all' ? 'active' : ''}
                aria-pressed={searchScope === 'all'}
                onClick={() => setSearchScope('all')}
              >
                All boards
              </button>
            </div>
            <button type="submit" className="secondary canvas-search-go" disabled={searching || !searchInput.trim()}>
              {searching ? 'Searching…' : 'Search'}
            </button>
          </form>

          {searchActive ? (
            <div className="canvas-search-results">
              <div className="canvas-search-filterbar">
                <span>
                  {searchError
                    ? searchError
                    : searchScope === 'board'
                      ? `Filtering board — ${boardMatchCount} of ${cards.length} pins`
                      : `${searchResults?.length ?? 0} match${(searchResults?.length ?? 0) === 1 ? '' : 'es'} across your boards`}
                </span>
                <span className="canvas-search-hint">Esc to exit</span>
                <button type="button" className="canvas-search-clearall" onClick={exitPinSearch}>
                  <X size={13} /> Clear search
                </button>
              </div>
              {(searchResults?.length ?? 0) > 0 ? (
                <ul className="canvas-search-list">
                  {searchResults!.map((hit, idx) => {
                    const onThisBoard = cards.some((card) => card.id === hit.card_id)
                    return (
                      <li key={`${hit.card_id}-${idx}`}>
                        <button
                          type="button"
                          className="canvas-search-row"
                          onClick={() => handleSearchResultClick(hit)}
                          title={hit.ref || hit.label || hit.title || ''}
                        >
                          <span className="canvas-search-row-body">
                            <span className="canvas-search-row-title">{hit.label || hit.title || hit.card_id}</span>
                            <span className="canvas-search-row-sub">
                              {(hit.namespace || hit.kind || 'pin')}{searchScope === 'all' && !onThisBoard ? ' · other board' : ''}
                            </span>
                          </span>
                          {typeof hit.score === 'number' ? (
                            <span className="canvas-search-row-score">{hit.score.toFixed(2)}</span>
                          ) : null}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              ) : (
                !searchError ? <div className="canvas-search-empty">No pins match — try fewer words, or switch to All boards.</div> : null
              )}
            </div>
          ) : null}
        </div>
      ) : null}

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
          aria-label="Canvas board"
          onPointerDown={startBoardGesture}
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
            const pinned = expandedCardId === card.id
            const pendingSuggestion = card.suggested && card.placement !== 'placed'
            const locallySelected = selectedCardIds.has(card.id)
            const dragged = dragState?.cardIds.includes(card.id)
            const enumTag = enumByCardId[card.id] || ''
            const addedAt = formatCardAdded(card.createdAt)
            const addedShort = formatCardAddedShort(card.createdAt)
            const copyUri = card.ref || ''
            const resolverState = resolverStateByCard[card.id]
            const resolverNotice = resolverNoticeByCard[card.id] || ''
            const resolverLoading = Boolean(resolverLoadingByCard[card.id])
            const descDraft = descDraftByCard[card.id]
            const isEditingDesc = descDraft !== undefined
            const commentDraft = commentDraftByCard[card.id] || ''
            const infoTooltip = [
              `id: ${card.id}`,
              `ref: ${card.ref || 'inline/local'}`,
              `mime: ${card.mime || 'application/octet-stream'}`,
              '',
              pinned ? 'Click to release the drawer.' : 'Click to pin the drawer open.',
            ].join('\n')
            const kind = card.kind
            const capabilities = resolverState?.capabilities
            const wantsDownload = capabilities?.download === true || kind === 'file' || kind === 'user.attachment'
            const wantsOpen = kind === 'memory' || kind === 'source' || kind === 'search.result' || kind === 'object.ref' || kind === 'conversation' || Boolean(namespaceFromRef(card.ref))
            const isObjectRefCard = kind === 'object.ref' || (Boolean(namespaceFromRef(card.ref)) && kind !== 'file' && kind !== 'user.attachment')
            const namespaceLabel = namespaceLabelForCard(card, namespaceStyles)
            const visibleSummary = card.summary || (isObjectRefCard ? card.ref : '')
            const providerAttachmentContexts = providerObjectAttachmentContexts(card, resolverState)
            return (
              <article
                key={card.id}
                data-card-id={card.id}
                className={`canvas-card ${pinned ? 'expanded' : ''} ${dragged ? 'moving' : ''} ${card.selected || locallySelected ? 'selected' : ''} ${locallySelected ? 'multi-selected' : ''} ${pendingSuggestion ? 'suggested' : ''} ${card.kind.replace('.', '-')} ${searchActive ? (matchedCardIds?.has(card.id) ? 'search-match' : 'search-dim') : ''}`}
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
                  width: card.rect.w,
                  height: card.rect.h,
                  ...namespaceCssForCard(card, namespaceStyles),
                }}
              >
                <div className="canvas-card-top">
                  <span className="canvas-card-origin" title={card.ref ? `${namespaceLabel} · ${card.kind}` : card.kind}>
                    <Icon size={13} />
                    <span className="canvas-card-origin-label">{namespaceLabel}</span>
                  </span>
                  {enumTag ? <span className="canvas-card-enum" title={`${enumTag} — pin position in this kind on the board`}>{enumTag}</span> : null}
                  <span className="canvas-card-buttons">
                    <button
                      type="button"
                      title={infoTooltip}
                      aria-label="Pin info"
                      aria-pressed={pinned}
                      className={pinned ? 'is-pinned' : ''}
                      onClick={(event) => {
                        event.stopPropagation()
                        openCard(card.id, !pinned)
                      }}
                      onMouseDown={(event) => event.stopPropagation()}
                    >
                      <Info size={13} />
                    </button>
                    {wantsDownload ? (
                      <button
                        type="button"
                        className="primary"
                        title="Download"
                        disabled={capabilities && capabilities.download === false}
                        onClick={(event) => {
                          event.stopPropagation()
                          void runObjectAction(card, 'download')
                        }}
                        onMouseDown={(event) => event.stopPropagation()}
                      >
                        <Download size={13} />
                      </button>
                    ) : wantsOpen ? (
                      <button
                        type="button"
                        className="primary"
                        title={kind === 'conversation' ? 'Open conversation in chat' : 'Open in owning surface'}
                        disabled={capabilities && capabilities.open === false}
                        onClick={(event) => {
                          event.stopPropagation()
                          // Open through the registered object resolver — for a
                          // conversation pin the resolver returns a ui_event the
                          // scene routes to the chat surface (drag-onto-chat
                          // remains a separate direct-load shortcut).
                          void runObjectAction(card, 'open')
                        }}
                        onMouseDown={(event) => event.stopPropagation()}
                      >
                        <ExternalLink size={13} />
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="primary"
                        title={pinned ? 'Collapse drawer' : 'Expand drawer'}
                        aria-pressed={pinned}
                        onClick={(event) => {
                          event.stopPropagation()
                          openCard(card.id, !pinned)
                        }}
                        onMouseDown={(event) => event.stopPropagation()}
                      >
                        {pinned ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
                      </button>
                    )}
                    {copyUri ? (
                      <button
                        type="button"
                        title={`Copy object URI\n${copyUri}`}
                        aria-label="Copy object URI"
                        onClick={(event) => {
                          event.stopPropagation()
                          void copyTextToClipboard(copyUri).then((ok) => {
                            if (!ok) return
                            setCopiedCardId(card.id)
                            window.setTimeout(() => setCopiedCardId((prev) => (prev === card.id ? '' : prev)), 1200)
                          })
                        }}
                        onMouseDown={(event) => event.stopPropagation()}
                      >
                        {copiedCardId === card.id ? <Check size={13} /> : <Copy size={13} />}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      title="Attach to chat"
                      onClick={() => onAttachCard(context)}
                      onMouseDown={(event) => event.stopPropagation()}
                    >
                      <MessageSquarePlus size={13} />
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
                  <p>{visibleSummary}</p>
                </div>
                <span className="canvas-card-kind">
                  <Grip size={12} />
                  {pendingSuggestion ? <span className="canvas-card-kind-label">pending suggestion</span> : null}
                  {addedShort ? (
                    <time className="canvas-card-time" title={`Added ${addedAt}`}>{addedShort}</time>
                  ) : null}
                  {copyUri ? (
                    <button
                      type="button"
                      className="canvas-card-copy"
                      title={`Copy URI\n${copyUri}`}
                      aria-label="Copy URI"
                      onClick={(event) => {
                        event.stopPropagation()
                        void copyTextToClipboard(copyUri).then((ok) => {
                          if (!ok) return
                          setCopiedCardId(card.id)
                          window.setTimeout(() => setCopiedCardId((prev) => (prev === card.id ? '' : prev)), 1200)
                        })
                      }}
                      onMouseDown={(event) => event.stopPropagation()}
                    >
                      {copiedCardId === card.id ? <Check size={12} /> : <Copy size={12} />}
                    </button>
                  ) : null}
                </span>
                <button
                  type="button"
                  className="canvas-card-resize"
                  title="Resize pin"
                  aria-label={`Resize ${card.title}`}
                  onPointerDown={(event) => startCardResize(card, event)}
                  onMouseDown={(event) => event.stopPropagation()}
                />
                <div
                  className={`canvas-card-flyout ${pinned && expandFlipUp ? 'up' : ''}`}
                  style={pinned ? { maxHeight: expandMaxHeight } : undefined}
                  onClick={(event) => event.stopPropagation()}
                  onPointerDown={(event) => event.stopPropagation()}
                >
                  {resolverLoading ? <p className="canvas-card-flyout-state">Resolving…</p> : null}
                  {!resolverLoading && resolverNotice ? (
                    <pre className="canvas-card-flyout-preview-text">{resolverNotice}</pre>
                  ) : null}
                  {!resolverLoading && !resolverNotice ? (
                    kind === 'conversation' ? (
                      <dl className="canvas-card-flyout-kv">
                        <div><dt>chat</dt><dd>{card.title || 'Conversation'}</dd></div>
                        {card.summary ? <div><dt>last</dt><dd>{card.summary}</dd></div> : null}
                      </dl>
                    ) : isObjectRefCard ? (
                      <dl className="canvas-card-flyout-kv">
                        <div><dt>object URI</dt><dd>{card.ref || card.title}</dd></div>
                        <div><dt>namespace</dt><dd>{namespaceLabel}</dd></div>
                        <div><dt>object kind</dt><dd>{card.object_kind || card.kind || 'object.ref'}</dd></div>
                        {card.summary ? <div><dt>preview</dt><dd>{card.summary}</dd></div> : null}
                      </dl>
                    ) : wantsDownload ? (
                      <dl className="canvas-card-flyout-kv">
                        <div><dt>file</dt><dd>{card.ref || card.title}</dd></div>
                        <div><dt>mime</dt><dd>{card.mime || '—'}</dd></div>
                      </dl>
                    ) : wantsOpen && (kind === 'source' || kind === 'search.result') ? (
                      <dl className="canvas-card-flyout-kv">
                        <div><dt>url</dt><dd>{card.ref || '—'}</dd></div>
                      </dl>
                    ) : card.summary ? (
                      <pre className="canvas-card-flyout-preview-text">{card.summary}</pre>
                    ) : null
                  ) : null}
                  {providerAttachmentContexts.length ? (
                    <section className="canvas-card-flyout-attachments">
                      <h4>Attachments <span className="count">({providerAttachmentContexts.length})</span></h4>
                      <div className="canvas-card-flyout-attachment-list">
                        {providerAttachmentContexts.map((attachment) => (
                          <div
                            key={attachment.id}
                            className="canvas-card-flyout-attachment"
                            draggable
                            title="Drag attachment to chat or canvas"
                            onDragStart={(event) => {
                              event.stopPropagation()
                              onDragCard(attachment)
                              event.dataTransfer.effectAllowed = 'copy'
                              // Canonical context-pin envelope (always plural `contexts`).
                              event.dataTransfer.setData('application/json', JSON.stringify({ type: 'kdcube.context.attach', contexts: [attachment] }))
                              event.dataTransfer.setData('text/plain', attachment.label)
                              if (attachment.logical_path || attachment.ref || attachment.hosted_uri) {
                                event.dataTransfer.setData('text/uri-list', attachment.logical_path || attachment.ref || attachment.hosted_uri || '')
                              }
                            }}
                            onDragEnd={(event) => {
                              event.stopPropagation()
                              onDragCard(null)
                            }}
                          >
                            <Paperclip size={13} />
                            <span>
                              <strong>{attachment.label}</strong>
                              {attachment.summary ? <small>{attachment.summary}</small> : null}
                            </span>
                          </div>
                        ))}
                      </div>
                    </section>
                  ) : null}
                  <section className="canvas-card-flyout-desc">
                    <div className="canvas-card-flyout-desc-head">
                      <h4>Description</h4>
                      {!isEditingDesc ? (
                        <button
                          type="button"
                          className="canvas-card-flyout-pencil"
                          title={card.description ? 'Edit description' : 'Add description'}
                          aria-label={card.description ? 'Edit description' : 'Add description'}
                          onClick={() => startDescriptionEdit(card)}
                        >
                          <PenLine size={13} />
                        </button>
                      ) : null}
                    </div>
                    {isEditingDesc ? (
                      <InlineMarkdownEditor
                        value={descDraft}
                        onChange={(value) => updateDescriptionDraft(card.id, value)}
                        onSave={() => commitDescriptionEdit(card)}
                        onCancel={() => cancelDescriptionEdit(card.id)}
                        placeholder="Describe this pin… (markdown supported)"
                        saveLabel="Save description"
                      />
                    ) : (
                      <div className="canvas-card-flyout-view">
                        {card.description ? <Markdown text={card.description} /> : <p className="empty">No description yet.</p>}
                      </div>
                    )}
                  </section>
                  <section className="canvas-card-flyout-comments">
                    <h4>Comments <span className="count">({card.commentsCount || 0})</span></h4>
                    <InlineMarkdownEditor
                      value={commentDraft}
                      onChange={(value) => updateCommentDraft(card.id, value)}
                      onSave={() => commitComment(card)}
                      placeholder="Add a comment… (markdown supported)"
                      saveLabel="Post comment"
                      saveDisabled={!commentDraft.trim()}
                    />
                  </section>
                </div>
              </article>
            )
          })}
          {textDraft ? (
            <article
              className="canvas-card user-text draft expanded"
              style={{ left: textDraft.rect.x, top: textDraft.rect.y, width: textDraft.rect.w }}
            >
              <div className="canvas-card-top">
                <span className="canvas-card-origin">
                  <PenLine size={13} />
                  <span className="canvas-card-origin-label">new user text</span>
                </span>
              </div>
              <InlineMarkdownEditor
                value={textDraft.text}
                onChange={(value) => setTextDraft((prev) => (prev ? { ...prev, text: value } : prev))}
                onSave={commitTextDraft}
                onCancel={() => setTextDraft(null)}
                placeholder="Write text… (markdown supported)"
                saveLabel="Create card"
                saveDisabled={!textDraft.text.trim()}
              />
            </article>
          ) : null}
        </div>

        <div className="canvas-create-controls" aria-label="Create canvas cards">
          <button
            type="button"
            className="canvas-round-action"
            title="New user text card"
            aria-label="New user text card"
            onClick={startTextDraft}
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
