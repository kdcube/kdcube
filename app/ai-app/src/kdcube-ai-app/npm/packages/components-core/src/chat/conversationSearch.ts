/**
 * Conversation search — the `/api/cb/conversations/{tenant}/{project}/search`
 * contract plus the pure presentation helpers the UI needs (grouping by
 * conversation, relative-relevance percentages, `<mark>` highlight segments).
 *
 * Two request shapes:
 *   - SEARCH: a non-empty `query` (optionally time-boxed) — hits come back
 *     relevance-ranked with fused `score`s.
 *   - BROWSE: a blank `query` with `from_ts`/`to_ts` set — hits are the turns
 *     in the window, `score: null`, rendered chronologically without relevance.
 */

export type ConversationSearchScope = 'user' | 'conversation'

export type ConversationSearchTarget = 'user' | 'assistant' | 'summary' | 'attachment'

export type ConversationSearchSortMode = 'relevance' | 'time'

/** Rank-arm multipliers (0..2, default 1.0 each). Only the ratios matter. */
export interface ConversationSearchWeights {
  semantic: number
  lexical: number
  recency: number
}

export const DEFAULT_SEARCH_WEIGHTS: ConversationSearchWeights = {
  semantic: 1.0,
  lexical: 1.0,
  recency: 1.0,
}

/** Engine-facing request: everything but `bundle_id`, which the engine/transport
 *  fills from its runtime. */
export interface ConversationSearchParams {
  query: string
  scope: ConversationSearchScope
  conversation_id?: string | null
  targets: ConversationSearchTarget[]
  from_ts?: string | null
  to_ts?: string | null
  limit?: number
  weights?: ConversationSearchWeights | null
  include_recovery_sessions: boolean
}

/** Full wire request body. */
export interface ConversationSearchRequest extends ConversationSearchParams {
  bundle_id: string
  /** Narrow to one agent's conversations — set by the engine for an agent-bound
   *  widget (a dedicated per-agent chat). Omitted for a plain single-agent widget. */
  agent_id?: string | null
}

export interface ConversationSearchSnippet {
  role?: string | null
  text?: string | null
  ts?: string | null
  path?: string | null
}

export interface ConversationSearchHit {
  conversation_id: string
  turn_id: string
  snippets: ConversationSearchSnippet[]
  /** 1-based position of the turn within its conversation. */
  ordinal?: number | null
  total_turns?: number | null
  /** Fused relevance. `null` in browse mode (no query — no relevance). */
  score?: number | null
  sim_score?: number | null
  recency_score?: number | null
  matched_via_role?: string | null
  ts?: string | null
  /** Where the backend read the matched turn: the live index (`hot`) or the archive (`cold`). */
  storage?: 'hot' | 'cold' | null
}

export interface ConversationSearchConversationMeta {
  title?: string | null
  last_activity_at?: string | null
}

export interface ConversationSearchResponse {
  user_id?: string | null
  effective_mode?: string | null
  warnings?: string[]
  hits: ConversationSearchHit[]
  conversations: Record<string, ConversationSearchConversationMeta>
}

/** Stable identity of one hit card (visited tint, best-match pill). */
export function conversationSearchHitKey(hit: ConversationSearchHit): string {
  return `${hit.conversation_id}:${hit.turn_id}`
}

export interface ConversationSearchGroup {
  conversationId: string
  hits: ConversationSearchHit[]
}

/** Group hits by conversation, preserving the incoming hit order — both the
 *  group order (first appearance) and the in-group order. */
export function groupHitsByConversation(hits: ConversationSearchHit[]): ConversationSearchGroup[] {
  const groups = new Map<string, ConversationSearchGroup>()
  for (const hit of hits) {
    let group = groups.get(hit.conversation_id)
    if (!group) {
      group = { conversationId: hit.conversation_id, hits: [] }
      groups.set(hit.conversation_id, group)
    }
    group.hits.push(hit)
  }
  return [...groups.values()]
}

/** Relevance of each hit relative to the best hit of this search, as a rounded
 *  percentage (score / topScore * 100). Null-safe: hits without a score (browse
 *  mode) map to `null`; a non-positive top score maps everything to `null`. */
export function relativeRelevance(hits: ConversationSearchHit[]): Map<ConversationSearchHit, number | null> {
  let top = 0
  for (const hit of hits) {
    if (typeof hit.score === 'number' && hit.score > top) top = hit.score
  }
  const out = new Map<ConversationSearchHit, number | null>()
  for (const hit of hits) {
    out.set(
      hit,
      typeof hit.score === 'number' && top > 0 ? Math.round((hit.score / top) * 100) : null,
    )
  }
  return out
}

export interface HighlightSegment {
  text: string
  match: boolean
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Split `text` into segments for `<mark>` rendering: every case-insensitive
 *  occurrence of any whitespace-separated query token becomes a `match: true`
 *  segment. Tokens are regex-escaped; longer tokens win at a shared start. */
export function buildHighlightSegments(text: string, query: string): HighlightSegment[] {
  const safeText = text || ''
  const tokens = (query || '')
    .split(/\s+/)
    .filter(Boolean)
    .sort((left, right) => right.length - left.length)
    .map(escapeRegExp)
  if (!safeText || tokens.length === 0) {
    return safeText ? [{ text: safeText, match: false }] : []
  }
  const pattern = new RegExp(`(${tokens.join('|')})`, 'gi')
  const segments: HighlightSegment[] = []
  let cursor = 0
  for (const found of safeText.matchAll(pattern)) {
    const index = found.index ?? 0
    if (!found[0]) continue
    if (index > cursor) segments.push({ text: safeText.slice(cursor, index), match: false })
    segments.push({ text: found[0], match: true })
    cursor = index + found[0].length
  }
  if (cursor < safeText.length) segments.push({ text: safeText.slice(cursor), match: false })
  return segments
}
