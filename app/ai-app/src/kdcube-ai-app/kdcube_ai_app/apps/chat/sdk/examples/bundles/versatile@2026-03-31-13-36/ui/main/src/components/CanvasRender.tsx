/**
 * CanvasRender — pick the right renderer for a canvas artifact.
 *
 * Variants:
 *  - html / srcdoc → sandboxed `<iframe>` (no scripts, no plugins, no forms)
 *  - markdown / md → MarkdownBlock-style render, GFM enabled
 *  - everything else → Snippet (json / code / plain text)
 *
 * Moved verbatim from src/App.tsx (Wave 1).
 */

import { memo } from 'react'
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

/* Canvas content renderer — picks markdown / html-iframe / code-with-highlight
   based on the canvas format. Falls back to plain pre-text for unknown types.
   Memoised: the canvas artifact reference is stable across deltas that
   don't touch it, so iframe srcDoc / markdown AST stay reused. */
function CanvasRenderImpl({ canvas }: { canvas: CanvasArtifact }) {
  const format = String(canvas.format || '').toLowerCase()
  const content = canvas.content || ''

  if (format === 'html' || format === 'srcdoc') {
    return (
      <iframe
        className="k-canvas-frame"
        srcDoc={content}
        sandbox="allow-same-origin"
        title={canvas.title || canvas.name}
      />
    )
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
