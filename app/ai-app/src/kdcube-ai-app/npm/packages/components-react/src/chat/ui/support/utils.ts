/**
 * Pure presentational helpers used across the chat UI. Ported from the in-tree
 * widget (src/components/utils.ts); behaviour unchanged. The only rewire vs. the
 * widget is the `BannerTone`/`StepStatus` type import, which now comes from the
 * engine package instead of the widget's local `service.ts`.
 */

import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import type { BannerTone, StepStatus } from '@kdcube/components-core/chat'

/** Plugin list used by every MarkdownBlock invocation. */
export const markdownPlugins = [remarkGfm, remarkBreaks]

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

export function toneClass(tone: BannerTone): string {
  switch (tone) {
    case 'error':
      return 'border-[rgba(247,96,154,0.3)] bg-[var(--danger-soft)] text-[var(--danger)]'
    case 'warning':
      return 'border-[rgba(240,188,46,0.38)] bg-[var(--gold-soft)] text-[var(--warning)]'
    default:
      return 'border-[rgba(217,229,99,0.34)] bg-[var(--accent-soft)] text-[var(--accent)]'
  }
}

export function stepTone(status: StepStatus): string {
  switch (status) {
    case 'completed':
      return 'bg-[var(--success-soft)] text-[var(--success)]'
    case 'error':
      return 'bg-[var(--danger-soft)] text-[var(--danger)]'
    case 'skipped':
      return 'bg-[rgba(94,107,120,0.12)] text-[var(--muted)]'
    default:
      return 'bg-[var(--accent-soft)] text-[var(--accent)]'
  }
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

/** Hostname for a URL, stripping `www.`. Falls back to the raw URL string
 *  when the input is not a parseable URL. */
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

/** Copy `text` to the clipboard.
 *
 *  Prefers the secure-context `navigator.clipboard.writeText` API and falls
 *  back to a hidden `<textarea>` + `document.execCommand('copy')` when
 *  running outside a secure context. */
function copyToClipboardWithTextarea(text: string): Promise<void> {
  return new Promise((resolve, reject) => {
    let ta: HTMLTextAreaElement | null = null
    try {
      ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.focus()
      ta.select()
      const ok = document.execCommand('copy')
      if (!ok) throw new Error('document.execCommand("copy") returned false')
      resolve()
    } catch (error) {
      reject(error)
    } finally {
      if (ta && ta.parentNode) ta.parentNode.removeChild(ta)
    }
  })
}

export async function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // Cross-origin iframes can expose navigator.clipboard but reject writes
      // when the host did not delegate clipboard-write. Try the legacy path
      // before surfacing the failure to the caller.
    }
  }
  await copyToClipboardWithTextarea(text)
}
