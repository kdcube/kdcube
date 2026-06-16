/**
 * Context-chip encoding — packs structured context objects into a chat message
 * tail (`{"context":[…]}`) and splits them back out.
 *
 * Ported verbatim from the widget's `features/chat/contextChips.ts` (pure).
 */
export interface ContextChip {
  id: string
  label: string
  [key: string]: unknown
}

function normalizeContextChips(contexts: unknown): ContextChip[] {
  if (!Array.isArray(contexts)) return []
  return contexts
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item, index) => {
      const label =
        (typeof item.label === 'string' && item.label.trim()) ||
        (typeof item.ref === 'string' && item.ref.trim()) ||
        (typeof item.kind === 'string' && item.kind.trim()) ||
        'context'
      const id =
        (typeof item.id === 'string' && item.id.trim()) ||
        (typeof item.ref === 'string' && item.ref.trim()) ||
        `context-${index}`
      return { ...item, id, label }
    })
}

function parseContextPayload(value: string): ContextChip[] | null {
  const trimmed = value.trim()
  if (!/^\{\s*"context"\s*:/.test(trimmed)) return null
  try {
    const parsed = JSON.parse(trimmed) as { context?: unknown }
    const contexts = normalizeContextChips(parsed.context)
    return contexts.length ? contexts : null
  } catch {
    return null
  }
}

function dedupeContextChips(contexts: ContextChip[]): ContextChip[] {
  const seen = new Set<string>()
  const out: ContextChip[] = []
  for (const context of contexts) {
    const key = context.id || context.label
    if (seen.has(key)) continue
    seen.add(key)
    out.push(context)
  }
  return out
}

export function splitContextChips(raw: string): { text: string; contexts: ContextChip[] } {
  const text = String(raw || '')
  const tail = text.match(/^([\s\S]*?)\n\n(\{\s*"context"\s*:\s*\[[\s\S]*\]\s*\})\s*$/)
  if (tail) {
    const prefix = splitContextChips(tail[1])
    const contexts = parseContextPayload(tail[2])
    if (contexts) {
      return {
        text: prefix.text,
        contexts: dedupeContextChips([...prefix.contexts, ...contexts]),
      }
    }
  }
  const bare = parseContextPayload(text)
  if (bare) return { text: '', contexts: bare }
  return { text, contexts: [] }
}

export function messageWithContextChips(text: string, contexts: unknown): string {
  const parsed = splitContextChips(text)
  const chips = dedupeContextChips([...parsed.contexts, ...normalizeContextChips(contexts)])
  const visibleText = parsed.text.trim()
  if (!chips.length) return visibleText
  const payload = JSON.stringify({ context: chips })
  return visibleText ? `${visibleText}\n\n${payload}` : payload
}
