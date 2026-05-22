/**
 * CanvasRender — pick the right renderer for a canvas artifact.
 *
 * Variants:
 *  - html / srcdoc → sandboxed `<iframe>` running scripts in an opaque
 *    origin. Streaming srcDoc updates are debounced so the iframe
 *    doesn't re-parse the entire HTML document on every char delta.
 *  - markdown / md → MarkdownBlock-style render, GFM enabled.
 *  - everything else → Snippet (json / code / plain text).
 *
 * The component is memoised — combined with Immer's structural sharing
 * on the chat slice, an unchanged canvas artifact reference short-
 * circuits this whole subtree across deltas.
 */

import { memo, useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import type { CanvasArtifact } from '../features/chat/chatTypes.ts'
import { Snippet } from './Snippet.tsx'
import { inferLanguage } from './highlight.ts'
import { closeStreamingMarkdown, markdownPlugins } from './utils.ts'

/** Best-effort filename for a canvas, used by the Canvas tab toolbar. */
export function canvasFilename(canvas: CanvasArtifact): string {
  const base = canvas.name || canvas.title || 'canvas'
  // strip path-y parts the agent might emit
  const trimmed = String(base).split('/').pop() || base
  const format = String(canvas.format || '').toLowerCase()
  const ext = format === 'markdown' || format === 'md' ? 'md'
    : format === 'html' || format === 'srcdoc' ? 'html'
    : format === 'json' ? 'json'
    : format === 'csv' ? 'csv'
    : format === 'python' || format === 'py' ? 'py'
    : format === 'javascript' || format === 'js' ? 'js'
    : format === 'bash' || format === 'shell' || format === 'sh' ? 'sh'
    : format === 'text' || !format ? 'txt'
    : format
  return trimmed.includes('.') ? trimmed : `${trimmed}.${ext}`
}

/** Best-effort MIME for a canvas, used by the Canvas tab toolbar download. */
export function canvasMime(canvas: CanvasArtifact): string {
  const format = String(canvas.format || '').toLowerCase()
  if (format === 'html' || format === 'srcdoc') return 'text/html'
  if (format === 'markdown' || format === 'md') return 'text/markdown'
  if (format === 'json') return 'application/json'
  if (format === 'csv') return 'text/csv'
  return 'text/plain'
}

/** Throttled iframe for canvas html / srcdoc payloads.
 *
 *  Why debounce: during streaming the bundle deltas canvas content
 *  char-by-char. Writing `srcDoc` on every render forces the browser
 *  to re-parse the entire HTML document — full HTML parser → CSSOM →
 *  layout → paint — which is the dominant cause of the lag the user
 *  sees with non-trivial canvases. We commit a new `srcDoc` only when
 *  content has stopped changing for ~250 ms, then commit the final
 *  version once streaming settles.
 *
 *  Why `allow-scripts` (and not the previous `allow-same-origin`):
 *  charts and other interactive HTML need JS to run. Without
 *  `allow-scripts` the canvas is a frozen screenshot — exactly the
 *  "not reactive" symptom the user reported. The iframe runs in an
 *  opaque origin (we deliberately do NOT add `allow-same-origin`;
 *  combining the two with `allow-scripts` would effectively disable
 *  the sandbox per the HTML spec), so scripts cannot read parent
 *  document/window/storage. `allow-popups` is added so the canvas
 *  can open `target="_blank"` links from inside. */
function CanvasHtmlFrame({ content, title }: { content: string; title?: string | null }) {
  const [committed, setCommitted] = useState(content)
  useEffect(() => {
    if (content === committed) return
    const t = window.setTimeout(() => setCommitted(content), 250)
    return () => window.clearTimeout(t)
  }, [content, committed])
  return (
    <iframe
      className="k-canvas-frame"
      srcDoc={committed}
      sandbox="allow-scripts allow-popups"
      title={title || undefined}
    />
  )
}

/* Canvas content renderer — picks markdown / html-iframe / code-with-highlight
   based on the canvas format. Falls back to plain pre-text for unknown types. */
function CanvasRenderImpl({ canvas }: { canvas: CanvasArtifact }) {
  const format = String(canvas.format || '').toLowerCase()
  const content = canvas.content || ''

  if (format === 'html' || format === 'srcdoc') {
    return <CanvasHtmlFrame content={content} title={canvas.title || canvas.name} />
  }

  if (format === 'markdown' || format === 'md') {
    return (
      <div className="k-canvas-markdown markdown-body">
        <ReactMarkdown
          remarkPlugins={markdownPlugins}
          components={{
            a: ({ children, href }) => (
              <a href={href} target="_blank" rel="noreferrer">{children}</a>
            ),
          }}
        >
          {closeStreamingMarkdown(content)}
        </ReactMarkdown>
      </div>
    )
  }

  return (
    <Snippet
      content={content}
      format={format === 'json' ? 'json' : 'code'}
      language={inferLanguage(format, content)}
      label={format || inferLanguage(format, content)}
    />
  )
}

export const CanvasRender = memo(CanvasRenderImpl)
