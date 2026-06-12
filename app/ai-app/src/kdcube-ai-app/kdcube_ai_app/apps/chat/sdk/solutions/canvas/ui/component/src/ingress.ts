/** Canvas ingress normalizers.
 *
 * Translates the five user-facing ingress gestures (per
 * `doc/handoff/canvas-ingress-note.md`) into the structured `new_card`
 * shape the bundle's `canvas_patch` operation accepts. The functions are
 * pure and synchronous; the orchestrators wrap them with the HTTP round
 * trip.
 *
 * Two write modes per the canvas design:
 *   - `content`: the UI sends bytes/text the bundle should rehost as a
 *     versioned `cnv:` object (user text, user upload, dragged assistant
 *     text). The storage layer replaces `content` with a `logical_path` on
 *     write.
 *   - `logical_path`: the UI pins an existing resolver-backed ref
 *     (search results, chat artifacts, foreign namespace refs) without rehosting.
 *     This preserves cross-conversation `fi:conv_<id>...` refs verbatim.
 *
 * The CanvasBoard wiring (drop zones, drag images, selection state) lives
 * separately — this module is consumable from both the parent main UI and
 * any future SDK-extracted canvas component without coupling either side.
 */

import { timestampSlugId } from './ids'
import type {
  CanvasCardKind,
  CanvasNewCardInput,
  CanvasNewCardOp,
  CanvasPatchResponse,
  CanvasUploadCardDescriptor,
} from './canvasTypes'

export interface CanvasIngressClient {
  patchCanvas: (input: {
    canvas_id?: string
    canvas_name?: string
    story_id?: string
    base_revision?: number
    patch: {
      canvas_id?: string
      canvas_name?: string
      base_revision?: number
      actor?: 'user' | 'agent' | 'system'
      operations: CanvasNewCardOp[]
    }
  }) => Promise<CanvasPatchResponse>
  uploadCanvasAttachments: (
    payload: {
      story_id?: string
      canvas_id?: string
      canvas_name?: string
      attachments?: Array<{
        placement?: 'floating' | 'placed' | 'suggested' | 'trashed'
        rect?: { x: number; y: number; w: number; h: number }
      }>
    },
    files: File[],
  ) => Promise<{
    ok: boolean
    cards?: CanvasUploadCardDescriptor[]
    error?: string
  }>
}

/** Canvas selection the orchestrators target. `storyId` is required
 *  because canvas storage scopes refs by story; the rest defaults to the
 *  active board. */
export interface CanvasIngressTarget {
  storyId: string
  canvasName?: string
  canvasId?: string
  baseRevision?: number
}

/** Optional per-card placement / authorship hints. Defaults: floating
 *  placement, `user` author, generated timestamp id based on card kind. */
export interface CardOptions {
  id?: string
  title?: string
  placement?: 'floating' | 'placed' | 'suggested'
  rect?: { x: number; y: number; w: number; h: number }
  source_card_ids?: string[]
  source_refs?: string[]
}

function prefixForCardKind(kind: CanvasCardKind): string {
  if (kind === 'user.attachment') return 'ua'
  if (kind === 'user.text') return 'ut'
  if (kind === 'agent.text') return 'at'
  return 'obj'
}

function timestampCardIdForKind(kind: CanvasCardKind): string {
  return timestampSlugId(prefixForCardKind(kind))
}

function compactPreview(value: string, max = 60): string {
  const collapsed = value.replace(/\s+/g, ' ').trim()
  if (collapsed.length <= max) return collapsed
  return `${collapsed.slice(0, max - 1)}…`
}

function compactFilename(value: string): string {
  const raw = String(value || '').trim()
  if (!raw) return ''
  let decoded = raw
  try {
    decoded = decodeURIComponent(raw)
  } catch {
    decoded = raw
  }
  const slashTail = decoded.split(/[\\/]/).filter(Boolean).pop() || decoded
  const colonTail = slashTail.includes(':') ? slashTail.split(':').filter(Boolean).pop() || slashTail : slashTail
  return compactPreview(colonTail, 48)
}

export function isCanvasDurableFiRef(ref: string): boolean {
  return /^fi:conv_[^.]+\.(turn_[^.]+)\./.test(String(ref || '').trim())
}

function assertCanvasLogicalPath(ref: string, source: string): void {
  const value = String(ref || '').trim()
  if (!value) {
    throw new Error(`${source}: ref is required`)
  }
  if (value.startsWith('fi:') && !isCanvasDurableFiRef(value)) {
    throw new Error(`${source}: canvas fi: refs must include conv_<conversation_id>`)
  }
}

// --- Normalizers ----------------------------------------------------------

/** User dragged selected text from anywhere (page, chat input, document)
 *  onto the canvas. The bundle should rehost it as a versioned `cnv:`
 *  text/markdown object so subsequent reads carry a stable ref. */
export function cardFromSelectedText(
  text: string,
  options: CardOptions & { title?: string } = {},
): CanvasNewCardInput {
  if (!text.trim()) {
    throw new Error('cardFromSelectedText: text is required')
  }
  return {
    id: options.id ?? timestampCardIdForKind('user.text'),
    kind: 'user.text',
    title: options.title ?? compactPreview(text),
    mime: 'text/markdown',
    content: { text },
    placement: options.placement ?? 'floating',
    rect: options.rect,
    source_card_ids: options.source_card_ids,
    source_refs: options.source_refs,
    created_by: 'user',
  }
}

/** Generic search result drag (memory, source pool, future subsystem
 *  search). The caller supplies the already-resolver-backed `ref` — this
 *  normalizer does NOT rewrite the namespace; resolver-backed refs flow
 *  through verbatim and are resolved by the host/provider configuration. */
export function cardFromSearchResult(
  hit: {
    ref: string
    title: string
    mime?: string
    summary?: string
    kind?: CanvasCardKind
  },
  options: CardOptions = {},
): CanvasNewCardInput {
  assertCanvasLogicalPath(hit.ref, 'cardFromSearchResult')
  return {
    id: options.id ?? hit.ref,
    kind: hit.kind ?? 'object.ref',
    title: options.title ?? hit.title,
    mime: hit.mime ?? 'application/json',
    namespace: ownerKeyFromLogicalPath(hit.ref),
    logical_path: hit.ref,
    placement: options.placement ?? 'floating',
    rect: options.rect,
    source_card_ids: options.source_card_ids,
    source_refs: options.source_refs,
    created_by: 'user',
  }
}

function namespaceFromLogicalPath(ref: string): string | undefined {
  const match = String(ref || '').trim().match(/^([A-Za-z][A-Za-z0-9_.-]*):/)
  return match?.[1]?.toLowerCase()
}

function ownerKeyFromLogicalPath(ref: string): string | undefined {
  const value = String(ref || '').trim()
  const index = value.lastIndexOf(':')
  if (index <= 0) return namespaceFromLogicalPath(value)
  return value.slice(0, index).trim().toLowerCase() || namespaceFromLogicalPath(value)
}

/** User dragged an agent-produced file from the chat iframe onto canvas.
 *  Preserves cross-conversation `fi:conv_<id>.turn_<id>...` refs verbatim
 *  per the canvas namespace contract. */
export function cardFromChatArtifact(
  artifact: { ref: string; mime: string; filename?: string; preview?: string },
  options: CardOptions = {},
): CanvasNewCardInput {
  assertCanvasLogicalPath(artifact.ref, 'cardFromChatArtifact')
  const title = options.title || compactFilename(artifact.filename || '') || compactFilename(artifact.ref) || 'File'
  return {
    id: options.id ?? artifact.ref,
    kind: 'file',
    title,
    mime: artifact.mime,
    logical_path: artifact.ref,
    placement: options.placement ?? 'floating',
    rect: options.rect,
    source_card_ids: options.source_card_ids,
    source_refs: options.source_refs,
    created_by: 'user',
  }
}

/** User dragged assistant response text from chat onto canvas. The bundle
 *  rehosts it as a versioned `cnv:` `agent.text` object so the canvas
 *  carries a stable ref — the chat history alone is not a durable home. */
export function cardFromChatAssistantText(
  text: string,
  options: CardOptions & { title?: string } = {},
): CanvasNewCardInput {
  if (!text.trim()) {
    throw new Error('cardFromChatAssistantText: text is required')
  }
  return {
    id: options.id ?? timestampCardIdForKind('agent.text'),
    kind: 'agent.text',
    title: options.title ?? compactPreview(text),
    mime: 'text/markdown',
    content: { text },
    placement: options.placement ?? 'floating',
    rect: options.rect,
    source_card_ids: options.source_card_ids,
    source_refs: options.source_refs,
    created_by: 'user',
  }
}

// --- Orchestrators --------------------------------------------------------

function buildPatchOp(card: CanvasNewCardInput): CanvasNewCardOp {
  return { op: 'new_card', card }
}

/** Apply one or more pre-normalized cards to a canvas via `canvas_patch`.
 *  Cards are applied as a single patch revision; this is the right entry
 *  point for the four ref/text-based ingress paths.
 *
 *  The orchestrator does NOT auto-select the active canvas — the caller
 *  passes `{storyId, canvasName, canvasId}` so the same module is reusable
 *  from outside the main workbench. */
export async function applyCanvasCards(
  cards: CanvasNewCardInput[],
  target: CanvasIngressTarget,
  client: CanvasIngressClient,
): Promise<CanvasPatchResponse> {
  if (!cards.length) {
    throw new Error('applyCanvasCards: at least one card is required')
  }
  return client.patchCanvas({
    canvas_id: target.canvasId,
    canvas_name: target.canvasName,
    story_id: target.storyId,
    base_revision: target.baseRevision,
    patch: {
      canvas_id: target.canvasId,
      canvas_name: target.canvasName,
      base_revision: target.baseRevision,
      actor: 'user',
      operations: cards.map(buildPatchOp),
    },
  })
}

/** Upload local files (the file drop ingress path) and pin them onto the
 *  canvas as `user.attachment` cards. Two round trips:
 *    1. `canvas_attachment_upload` rehosts the bytes and returns
 *       `cards[]` carrying `logical_path` + `mime` + `size`.
 *    2. `canvas_patch` adds those cards as `new_card` ops so they actually
 *       appear on the board.
 *
 *  Returns the patch response (with the post-write canvas state) so the UI
 *  can use the same projection refresh path as the other ingress paths. */
export async function uploadAndPinFiles(
  files: File[],
  target: CanvasIngressTarget,
  client: CanvasIngressClient,
  options: CardOptions = {},
): Promise<CanvasPatchResponse> {
  if (!files.length) {
    throw new Error('uploadAndPinFiles: at least one file is required')
  }
  const uploadPlacement = options.placement === 'placed' ? 'placed' : 'floating'
  const uploaded = await client.uploadCanvasAttachments(
    {
      story_id: target.storyId,
      canvas_id: target.canvasId,
      canvas_name: target.canvasName,
      attachments: files.map((_, index) => ({
        placement: uploadPlacement,
        rect: options.rect
          ? {
              ...options.rect,
              x: options.rect.x + index * 18,
              y: options.rect.y + index * 18,
            }
          : undefined,
      })),
    },
    files,
  )
  if (!uploaded.ok || !uploaded.cards || uploaded.cards.length === 0) {
    throw new Error(uploaded.error || 'canvas_attachment_upload returned no cards')
  }
  return applyCanvasCards(uploaded.cards.map(uploadedCardToInput), target, client)
}

function uploadedCardToInput(card: CanvasUploadCardDescriptor): CanvasNewCardInput {
  return {
    id: card.id,
    kind: card.kind,
    title: card.title,
    mime: card.mime,
    logical_path: card.logical_path,
    placement: card.placement,
    rect: card.rect,
    created_by: card.created_by,
  }
}
