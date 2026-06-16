/**
 * Pure content/format helpers used by the reducers and controller.
 *
 * Split out of the widget's `components/utils.ts` + `components/highlight.ts`:
 * only the framework-free helpers live here. The markdown plugin list, CSS-class
 * mappers (toneClass/stepTone), clipboard, and the actual syntax-highlight
 * rendering stay in the view — they are not engine concerns.
 */

export function timestampValue(value?: string): number {
  const parsed = value ? Date.parse(value) : NaN
  return Number.isFinite(parsed) ? parsed : Date.now()
}

export function formatTime(value: number): string {
  return new Date(value).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatConversationTime(value?: number | null): string {
  if (!value || !Number.isFinite(value)) return 'No activity yet'
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = bytes
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size >= 10 || index === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[index]}`
}

/** Auto-close any unclosed triple-fenced code block so partial markdown
 *  streamed from the model doesn't break the page layout. */
export function closeStreamingMarkdown(text: string): string {
  const tripleBackticks = text.match(/```/g)?.length || 0
  const tripleTildes = text.match(/~~~/g)?.length || 0
  let next = text
  if (tripleBackticks % 2 === 1) next += '\n```'
  if (tripleTildes % 2 === 1) next += '\n~~~'
  return next
}

export function safeJsonParse<T>(raw: string, fallback: T): T {
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export function messageForError(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

export function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/** Hostname for a URL, stripping `www.`. Falls back to the raw URL string. */
export function shortUrl(url: string): string {
  try {
    const parsed = new URL(url)
    return parsed.hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export type CodeLanguage = 'python' | 'javascript' | 'bash' | 'json'

/** Heuristic source-language inference from an optional hint + a code sample.
 *  Pure — the syntax-highlight rendering it once fed stays in the view. */
export function inferLanguage(hint: string | null | undefined, code: string): CodeLanguage {
  const h = String(hint || '').toLowerCase()
  if (h.startsWith('py')) return 'python'
  if (h === 'js' || h === 'jsx' || h === 'ts' || h === 'tsx' || h === 'javascript' || h === 'typescript') return 'javascript'
  if (h === 'sh' || h === 'bash' || h === 'shell') return 'bash'
  if (h === 'json') return 'json'
  const sample = code.slice(0, 240)
  if (/^\s*(def |class |import |from |if __name__)/m.test(sample)) return 'python'
  if (/^\s*(const |let |var |function |export |import )/m.test(sample)) return 'javascript'
  if (/^\s*(#!\/|echo |cd |export )/m.test(sample)) return 'bash'
  return 'python'
}
