/**
 * View-model hook for conversation search in the sidebar.
 *
 * Owns the whole search surface state: the query, the WHERE/WHEN/HOW/RANK
 * settings, the results (or browse) response, sort mode and the visited card.
 * The `titles` scope is purely local (it never calls the backend — the parent
 * filters the conversation list by title as the user types); the deep scopes
 * (`current`/`all`) call the backend only when the user presses Search.
 *
 * A blank query with a time range is BROWSE mode: the backend returns the
 * turns in the window chronologically with `score: null` per hit, and the UI
 * renders them newest-first without relevance.
 */
import { useEffect, useMemo, useState } from 'react'
import type {
  ConversationSearchParams,
  ConversationSearchResponse,
  ConversationSearchTarget,
  ConversationSearchWeights,
} from '@kdcube/components-core/chat'
import { DEFAULT_SEARCH_WEIGHTS } from '@kdcube/components-core/chat'
import { useStableCallback } from '../../support/useStableCallback.ts'

export type ConversationSearchScope = 'titles' | 'current' | 'all'
export type ConversationSearchTimePreset = 'any' | '7' | '30' | '90' | 'custom'
export type ConversationSearchSort = 'rel' | 'ts'

export const SCOPE_HINTS: Record<ConversationSearchScope, string> = {
  titles: 'Filters chat titles as you type — free, instant.',
  current: 'Searches messages of the OPEN chat. Press Search to run.',
  all: 'Searches messages across ALL your chats. Press Search to run.',
}

export const SCOPE_PLACEHOLDERS: Record<ConversationSearchScope, string> = {
  titles: 'Filter chat titles…',
  current: 'Search in this chat…',
  all: 'Search across all chats…',
}

const DAY_MS = 24 * 60 * 60 * 1000
const SEARCH_LIMIT = 40

interface ResolvedTimeRange {
  fromTs: string | null
  toTs: string | null
}

/** The WHEN row as concrete ISO bounds, or `null` when no range is set. */
export function resolveSearchTimeRange(
  preset: ConversationSearchTimePreset,
  dateFrom: string,
  dateTo: string,
): ResolvedTimeRange | null {
  if (preset === 'custom') {
    if (!dateFrom && !dateTo) return null
    return {
      fromTs: dateFrom ? new Date(`${dateFrom}T00:00:00`).toISOString() : null,
      toTs: dateTo ? new Date(`${dateTo}T23:59:59.999`).toISOString() : null,
    }
  }
  const days = Number(preset)
  if (!Number.isFinite(days) || days <= 0) return null
  return { fromTs: new Date(Date.now() - days * DAY_MS).toISOString(), toTs: null }
}

/** One-shot externally-supplied search state (e.g. the `conversation_search.open`
 *  seed carried when the chat undocks its search into a scene window). */
export interface ConversationSearchSeed {
  query?: string
  scope?: ConversationSearchScope
  targets?: ConversationSearchTarget[]
  timePreset?: ConversationSearchTimePreset
  dateFrom?: string
  dateTo?: string
  weights?: Partial<ConversationSearchWeights>
  /** Run the seeded search immediately (once the state committed). */
  autorun?: boolean
}

const VALID_TARGETS: ConversationSearchTarget[] = ['user', 'assistant', 'summary']
const VALID_TIME_PRESETS: ConversationSearchTimePreset[] = ['any', '7', '30', '90', 'custom']

export interface ConversationSearchVm {
  query: string
  setQuery: (value: string) => void
  scope: ConversationSearchScope
  setScope: (scope: ConversationSearchScope) => void
  settingsOpen: boolean
  toggleSettings: () => void
  infoOpen: boolean
  setInfoOpen: (open: boolean) => void
  timePreset: ConversationSearchTimePreset
  setTimePreset: (preset: ConversationSearchTimePreset) => void
  dateFrom: string
  setDateFrom: (value: string) => void
  dateTo: string
  setDateTo: (value: string) => void
  /** HOW kinds currently ON (all-on default; opt-out keeps at least one). */
  targets: ConversationSearchTarget[]
  toggleTarget: (target: ConversationSearchTarget) => void
  weights: ConversationSearchWeights
  setWeight: (arm: keyof ConversationSearchWeights, value: number) => void
  resetWeights: () => void
  /** Search runs on a deep scope with SOMETHING to ask: words, or a time range. */
  canSearch: boolean
  mode: 'list' | 'results'
  browse: boolean
  searching: boolean
  error: string | null
  response: ConversationSearchResponse | null
  /** The query the current results were produced for (drives highlighting). */
  resultsQuery: string
  sort: ConversationSearchSort
  setSort: (sort: ConversationSearchSort) => void
  /** Key of the last-visited result card. A hit renders one card per snippet,
   *  so the key is finer-grained than the hit (`conv:turn:<card>`). */
  visitedKey: string | null
  markVisited: (cardKey: string) => void
  runSearch: () => void
  clearSearch: () => void
  /** Apply an externally-supplied search state in one shot (see
   *  `ConversationSearchSeed`); `autorun` searches after the state commits. */
  applySeed: (seed: ConversationSearchSeed) => void
}

export function useConversationSearch({
  search,
  activeConversationId,
  initialScope = 'titles',
  allowBrowse = true,
}: {
  search: (request: ConversationSearchParams) => Promise<ConversationSearchResponse>
  activeConversationId: string | null
  /** Surfaces without a local chat list (the undocked window) start deep. */
  initialScope?: ConversationSearchScope
  /** Enable blank-query temporal browsing when the host persists turn catalogs. */
  allowBrowse?: boolean
}): ConversationSearchVm {
  const [query, setQuery] = useState('')
  const [scope, setScopeState] = useState<ConversationSearchScope>(initialScope)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [infoOpen, setInfoOpen] = useState(false)
  const [timePreset, setTimePreset] = useState<ConversationSearchTimePreset>('any')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [targets, setTargets] = useState<ConversationSearchTarget[]>(['user', 'assistant', 'summary'])
  const [weights, setWeights] = useState<ConversationSearchWeights>({ ...DEFAULT_SEARCH_WEIGHTS })
  const [mode, setMode] = useState<'list' | 'results'>('list')
  const [browse, setBrowse] = useState(false)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [response, setResponse] = useState<ConversationSearchResponse | null>(null)
  const [resultsQuery, setResultsQuery] = useState('')
  const [sort, setSort] = useState<ConversationSearchSort>('rel')
  const [visitedKey, setVisitedKey] = useState<string | null>(null)

  const timeRange = useMemo(
    () => resolveSearchTimeRange(timePreset, dateFrom, dateTo),
    [timePreset, dateFrom, dateTo],
  )

  const canSearch =
    scope !== 'titles' &&
    (scope !== 'current' || Boolean(activeConversationId)) &&
    (Boolean(query.trim()) || (allowBrowse && timeRange !== null))

  /** Titles scope is the free local filter — leaving results mode with it. */
  const setScope = useStableCallback((next: ConversationSearchScope) => {
    setScopeState(next)
    if (next === 'titles') setMode('list')
  })

  const toggleSettings = useStableCallback(() => setSettingsOpen((open) => !open))

  const toggleTarget = useStableCallback((target: ConversationSearchTarget) => {
    setTargets((current) => {
      if (current.includes(target)) {
        /* Opt-out, but keep at least one kind in play. */
        if (current.length <= 1) return current
        return current.filter((entry) => entry !== target)
      }
      return [...current, target]
    })
  })

  const setWeight = useStableCallback((arm: keyof ConversationSearchWeights, value: number) => {
    setWeights((current) => ({ ...current, [arm]: value }))
  })

  const resetWeights = useStableCallback(() => setWeights({ ...DEFAULT_SEARCH_WEIGHTS }))

  const runSearch = useStableCallback(() => {
    if (scope === 'titles' || searching) return
    const trimmed = query.trim()
    const range = resolveSearchTimeRange(timePreset, dateFrom, dateTo)
    if (!trimmed && (!allowBrowse || !range)) return
    if (scope === 'current' && !activeConversationId) return
    const isBrowse = !trimmed
    setSearching(true)
    setError(null)
    void search({
      query: trimmed,
      scope: scope === 'current' ? 'conversation' : 'user',
      ...(scope === 'current' ? { conversation_id: activeConversationId } : {}),
      targets,
      ...(range?.fromTs ? { from_ts: range.fromTs } : {}),
      ...(range?.toTs ? { to_ts: range.toTs } : {}),
      limit: SEARCH_LIMIT,
      /* Browse carries no relevance question — no rank weights to send. */
      weights: isBrowse ? null : { ...weights },
      include_recovery_sessions: false,
    })
      .then((result) => {
        setResponse(result)
        setResultsQuery(trimmed)
        setBrowse(isBrowse)
        /* Browse is chronological by nature. */
        setSort(isBrowse ? 'ts' : 'rel')
        setMode('results')
      })
      .catch((cause) => {
        setError(cause instanceof Error ? cause.message : String(cause))
      })
      .finally(() => setSearching(false))
  })

  const clearSearch = useStableCallback(() => {
    setQuery('')
    setMode('list')
    setError(null)
  })

  const markVisited = useStableCallback((cardKey: string) => {
    setVisitedKey(cardKey)
  })

  /* Seed application is two-phase: set the state, then (for `autorun`) run
   * the search from an effect — `runSearch` reads the committed state, so
   * running it in the same tick would search the PRE-seed values. */
  const [autorunNonce, setAutorunNonce] = useState(0)
  useEffect(() => {
    if (!autorunNonce) return
    runSearch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autorunNonce])

  const applySeed = useStableCallback((seed: ConversationSearchSeed) => {
    if (seed.query !== undefined) setQuery(seed.query)
    if (seed.scope === 'titles' || seed.scope === 'current' || seed.scope === 'all') {
      setScopeState(seed.scope)
      if (seed.scope === 'titles') setMode('list')
    }
    if (seed.targets) {
      const targets = seed.targets.filter((target) => VALID_TARGETS.includes(target))
      if (targets.length) setTargets(targets)
    }
    if (seed.timePreset && VALID_TIME_PRESETS.includes(seed.timePreset)) setTimePreset(seed.timePreset)
    if (seed.dateFrom !== undefined) setDateFrom(seed.dateFrom)
    if (seed.dateTo !== undefined) setDateTo(seed.dateTo)
    if (seed.weights) {
      const patch: Partial<ConversationSearchWeights> = {}
      for (const arm of ['semantic', 'lexical', 'recency'] as const) {
        const value = seed.weights[arm]
        if (typeof value === 'number' && Number.isFinite(value)) patch[arm] = Math.min(2, Math.max(0, value))
      }
      if (Object.keys(patch).length) setWeights((current) => ({ ...current, ...patch }))
    }
    if (seed.autorun) setAutorunNonce((nonce) => nonce + 1)
  })

  /* All the callbacks above are referentially stable (useState setters +
   * useStableCallback), so the vm identity only changes with actual state —
   * keeping the memoized sidebar quiet. */
  return useMemo(
    () => ({
      query,
      setQuery,
      scope,
      setScope,
      settingsOpen,
      toggleSettings,
      infoOpen,
      setInfoOpen,
      timePreset,
      setTimePreset,
      dateFrom,
      setDateFrom,
      dateTo,
      setDateTo,
      targets,
      toggleTarget,
      weights,
      setWeight,
      resetWeights,
      canSearch,
      mode,
      browse,
      searching,
      error,
      response,
      resultsQuery,
      sort,
      setSort,
      visitedKey,
      markVisited,
      runSearch,
      clearSearch,
      applySeed,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      query, scope, settingsOpen, infoOpen, timePreset, dateFrom, dateTo,
      targets, weights, canSearch, mode, browse, searching, error, response,
      resultsQuery, sort, visitedKey,
    ],
  )
}
