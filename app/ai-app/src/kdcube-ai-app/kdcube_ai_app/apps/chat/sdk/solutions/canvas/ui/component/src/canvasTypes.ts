/** Canvas wire shapes consumed by the main UI ingress paths.
 *
 * These mirror the bundle-side payloads accepted by `canvas_patch` and the
 * envelope returned by `canvas_attachment_upload`. Keep this file aligned
 * with `canvas/api.py` and `canvas/storage.py`; if the patch op set or
 * card field set changes there, update both.
 *
 * Card kind reference (per `doc/design/canvas.md`):
 *   - `user.text`          canvas-hosted user-authored text (cnv: ref)
 *   - `user.attachment`    canvas-hosted user upload (cnv: ref)
 *   - `agent.text`         canvas-hosted assistant-authored text (cnv: ref); not the same as suggestion state
 *   - `file`               file artifact, including cross-conversation fi: refs
 *   - `memory`             memory store result (mem: ref)
 *   - `source`             source-pool row (so: ref)
 *   - `search.result`      generic search result with whatever resolver-backed ref
 *   - `issue.ref`          issue/task pin (for example task:issues/<issue_id>)
 *   - `story.ref`          story pin
 *   - `note`               free-form authored note
 */

export type CanvasCardKind =
  | 'user.text'
  | 'user.attachment'
  | 'agent.text'
  | 'file'
  | 'memory'
  | 'source'
  | 'search.result'
  | 'issue.ref'
  | 'story.ref'
  | 'note'
  | 'object.ref'

/** `suggested` is a pending/limbo state for any card kind: file, memory,
 *  source result, link, or text. It is not equivalent to `agent.text`. */
export type CanvasCardPlacement = 'floating' | 'placed' | 'suggested' | 'trashed'

/** Geometry rectangle used by placed cards. Mirrors the Python shape; the
 *  storage layer may default to a free-position rect when the UI omits it. */
export interface CanvasRect {
  x: number
  y: number
  w: number
  h: number
}

/** Input shape for a new card. Either `content` (UI sends bytes/text to be
 *  rehosted as a `cnv:` object by the storage layer) or `logical_path`
 *  (UI pins an existing ref) — never both. The card may carry both keys when
 *  the storage layer's rehoster expects to ignore one based on `kind`. */
export interface CanvasNewCardInput {
  id?: string
  kind: CanvasCardKind
  title: string
  mime: string
  /** Versioned ref to existing content. Mutually exclusive with `content`
   *  for cards the bundle rehosts. */
  logical_path?: string
  /** Inline content for cards the bundle is supposed to host as `cnv:`.
   *  The storage layer replaces this with a `logical_path` on write. */
  content?: { text?: string; data?: unknown }
  placement?: CanvasCardPlacement
  rect?: CanvasRect
  desired_size?: { w: number; h: number }
  source_card_ids?: string[]
  source_refs?: string[]
  created_by?: 'user' | 'agent' | 'system'
}

export interface CanvasNewCardOp {
  op: 'new_card'
  card: CanvasNewCardInput
}

export interface CanvasUpdateCardOp {
  op: 'update_card'
  card_id: string
  set?: {
    title?: string
    description?: string
    summary?: string
    content_preview?: string
    placement?: CanvasCardPlacement
    rect?: CanvasRect
    trashed?: boolean
    trash_state?: unknown
    [key: string]: unknown
  }
  content?: { text?: string; data?: unknown }
}

export interface CanvasMoveCardOp {
  op: 'move_card'
  card_id: string
  x: number
  y: number
}

export interface CanvasResizeCardOp {
  op: 'resize_card'
  card_id: string
  w: number
  h: number
}

export interface CanvasCommentCardOp {
  op: 'comment_card'
  card_id: string
  text: string
  comment_id?: string
}

export interface CanvasDeleteCardOp {
  op: 'delete_card'
  card_id: string
}

export type CanvasPatchOp =
  | CanvasNewCardOp
  | CanvasUpdateCardOp
  | CanvasMoveCardOp
  | CanvasResizeCardOp
  | CanvasCommentCardOp
  | CanvasDeleteCardOp

export interface CanvasPatchInput {
  canvas_id?: string
  canvas_name?: string
  story_id?: string
  base_revision?: number
  patch: {
    canvas_id?: string
    canvas_name?: string
    base_revision?: number
    actor?: 'user' | 'agent' | 'system'
    operations: CanvasPatchOp[]
  }
}

export interface CanvasPatchAppliedCard {
  id?: string
  kind?: CanvasCardKind
  title?: string
  mime?: string
  logical_path?: string
  placement?: CanvasCardPlacement
}

export interface CanvasPatchResponse {
  ok: boolean
  user_id?: string
  story_id?: string
  canvas_id?: string
  canvas_name?: string
  revision?: number
  canvas_ref?: string
  latest_ref?: string
  storage_uri?: string
  /** The history tail entry summarizing the applied ops. */
  changed?: unknown[]
  /** Cards touched by the patch, used as fallback and visual emphasis. */
  changed_cards?: unknown[]
  canvas?: Record<string, unknown>
  projection?: Record<string, unknown>
  /** A UI-facing summary mirroring a canvas patch applied event. */
  ui_event?: {
    type: string
    source?: string
    story_id?: string
    canvas_name?: string
    canvas_id?: string
    revision?: number
    canvas_uri?: string
    canvas_ref?: string
    latest_ref?: string
    changed?: unknown[]
    changed_cards?: unknown[]
    projection?: Record<string, unknown>
  }
  error?: string
  detail?: string
  message?: string
  status?: number
  expected_revision?: number
  current_revision?: number
}

export interface CanvasListInput {
  story_id?: string
}

export interface CanvasListItem {
  canvas_id?: string
  canvas_name?: string
  latest_revision?: number
  revision?: number
  latest_ref?: string
  canvas_ref?: string
  storage_uri?: string
  updated_at?: string | number
  created_at?: string | number
  card_count?: number
  summary?: string
}

export interface CanvasListResponse {
  ok: boolean
  user_id?: string
  story_id?: string
  canvases?: CanvasListItem[]
  error?: string
  status?: number
}

export interface CanvasReadInput {
  uri?: string
  canvas_id?: string
  canvas_name?: string
  name?: string
  story_id?: string
  revision?: number
}

export interface CanvasReadResponse {
  ok: boolean
  found?: boolean
  user_id?: string
  story_id?: string
  canvas_id?: string
  canvas_name?: string
  revision?: number
  canvas_ref?: string
  latest_ref?: string
  canvas_uri?: string
  canvas?: Record<string, unknown>
  projection?: Record<string, unknown>
  agent_view?: string
  error?: string
  status?: number
}

export type CanvasObjectActionName = 'capabilities' | 'describe' | 'preview' | 'open' | 'download' | 'rehost'

export interface CanvasObjectActionInput {
  object_ref: string
  action: CanvasObjectActionName
  card_id?: string
  canvas_id?: string
  canvas_name?: string
  story_id?: string
  mime?: string
}

export interface CanvasObjectCapabilities {
  preview?: boolean
  open?: boolean
  download?: boolean
  rehost?: boolean
  [key: string]: boolean | undefined
}

export interface CanvasObjectActionResponse {
  ok: boolean
  action?: string
  ref?: string
  object_ref?: string
  namespace?: string
  resolver?: string
  resolver_status?: string
  capabilities?: CanvasObjectCapabilities
  title?: string
  summary?: string
  mime?: string
  text?: string
  json?: unknown
  content_base64?: string
  filename?: string
  size?: number
  issue?: unknown
  memory?: unknown
  ui_event?: {
    type?: string
    subject?: string
    request_id?: string
    source?: string
    object_ref?: string
    target_surface?: string
    mode?: string
    issue_id?: string
    memory_id?: string
    title?: string
  }
  error?: string
  message?: string
  status?: number
}

/** Returned by `canvas_attachment_upload`. The `cards` are NOT yet pinned
 *  on the canvas — the caller must run a `canvas_patch` with `new_card` ops
 *  carrying the returned `logical_path`. */
export interface CanvasUploadedAttachment {
  logical_path: string
  storage_ref: string
  mime: string
  size: number
  version: number
}

export interface CanvasUploadCardDescriptor {
  id: string
  kind: CanvasCardKind
  title: string
  mime: string
  logical_path: string
  storage_ref: string
  version: number
  placement: CanvasCardPlacement
  created_by: 'user' | 'agent' | 'system'
  size: number
  rect?: CanvasRect
}

export interface CanvasUploadResponse {
  ok: boolean
  user_id?: string
  story_id?: string
  canvas_id?: string
  canvas_name?: string
  attachments?: CanvasUploadedAttachment[]
  cards?: CanvasUploadCardDescriptor[]
  error?: string
  status?: number
}
