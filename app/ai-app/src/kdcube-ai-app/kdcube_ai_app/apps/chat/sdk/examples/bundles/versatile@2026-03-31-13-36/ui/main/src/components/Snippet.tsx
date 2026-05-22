/**
 * Snippet — the canonical bordered shell for code / JSON / markdown / text
 * with header bar, copy, optional download, and optional max-height scroll.
 *
 * Code & JSON variants get the dark-theme background and syntax-highlighted
 * HTML via `highlightCode`. Markdown variant defers to `MarkdownBlock`.
 *
 * Memoised by `React.memo` (default shallow compare). All props are
 * scalars or strings — when nothing semantically changed, React skips
 * the tokenizer pass and the HTML rebuild. `highlightCode(content, lang)`
 * is also memoised per `[content, lang]` so even when a parent prop
 * (`label`, `filename`) changes, we don't re-tokenize the code body.
 */

import { memo, useMemo } from 'react'
import { CopyButton } from './CopyButton.tsx'
import { DownloadButton } from './DownloadButton.tsx'
import { MarkdownBlock } from './MarkdownBlock.tsx'
import { HL_KEYWORDS, highlightCode, inferLanguage } from './highlight.ts'

export interface SnippetProps {
  content: string
  format: 'markdown' | 'code' | 'json' | 'text'
  language?: keyof typeof HL_KEYWORDS
  label?: string
  filename?: string
  downloadMime?: string
  showCopy?: boolean
  showDownload?: boolean
  maxHeight?: number
}

function SnippetImpl({
  content,
  format,
  language,
  label,
  filename,
  downloadMime,
  showCopy = true,
  showDownload = false,
  maxHeight,
}: SnippetProps) {
  const isCodeFamily = format === 'code' || format === 'json'
  const lang = language || (format === 'json' ? 'json' : inferLanguage(null, content))
  /* Cache the syntax-highlighted HTML — by far the most expensive op
   * inside Snippet — across renders that only change cosmetic props. */
  const html = useMemo(
    () => (isCodeFamily ? highlightCode(content, lang) : null),
    [isCodeFamily, content, lang],
  )
  const labelText = label || (isCodeFamily ? lang : format)

  return (
    <div className={`k-snippet ${isCodeFamily ? 'k-snippet-dark' : ''}`}>
      <div className="k-snippet-head">
        <span className={`k-snippet-label ${isCodeFamily ? 'k-mono' : ''}`}>{labelText}</span>
        <span className="k-snippet-tools">
          {showCopy ? <CopyButton value={content} /> : null}
          {showDownload && filename ? (
            <DownloadButton data={content} filename={filename} mime={downloadMime} />
          ) : null}
        </span>
      </div>
      {format === 'markdown' ? (
        <div
          className="k-snippet-body"
          style={maxHeight ? { maxHeight: `${maxHeight}px` } : undefined}
        >
          <MarkdownBlock content={content} compact />
        </div>
      ) : isCodeFamily ? (
        <pre
          className="k-snippet-body k-snippet-pre"
          style={maxHeight ? { maxHeight: `${maxHeight}px` } : undefined}
          dangerouslySetInnerHTML={{ __html: html || '' }}
        />
      ) : (
        <pre
          className="k-snippet-body k-snippet-pre k-snippet-wrap"
          style={maxHeight ? { maxHeight: `${maxHeight}px` } : undefined}
        >
          {content}
        </pre>
      )}
    </div>
  )
}

export const Snippet = memo(SnippetImpl)
