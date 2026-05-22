/**
 * Markdown renderer used everywhere in the chat surface.
 *
 * Wraps `react-markdown` with the canonical plugin set (GFM + line-breaks),
 * auto-closes unclosed fenced code blocks so partial streamed markdown
 * doesn't break the page, and exposes a compact density variant for nested
 * places like the Chat thinking timeline.
 *
 * Memoised by `React.memo`: identical `content` + `compact` reuses the
 * previously-built ReactMarkdown subtree. During streaming, the answer
 * body still re-renders on each delta (its content actually changed),
 * but all settled markdown rows — thinking timeline entries, sent user
 * messages, follow-up notes, canvas markdown — skip the AST rebuild.
 */

import { memo, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import { closeStreamingMarkdown, markdownPlugins } from './utils.ts'

function MarkdownBlockImpl({ content, compact = false }: { content: string; compact?: boolean }) {
  const normalized = useMemo(() => closeStreamingMarkdown(content), [content])

  return (
    <div className={`markdown-body ${compact ? 'text-[13px]' : 'text-[14px]'}`}>
      <ReactMarkdown
        remarkPlugins={markdownPlugins}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          p: ({ children }) => (
            <p className={compact ? 'my-1 leading-5' : 'my-2 leading-6'}>{children}</p>
          ),
          ul: ({ children }) => <ul className={compact ? 'my-1 list-disc pl-5' : 'my-2 list-disc pl-5'}>{children}</ul>,
          ol: ({ children }) => <ol className={compact ? 'my-1 list-decimal pl-5' : 'my-2 list-decimal pl-5'}>{children}</ol>,
          li: ({ children }) => <li className="my-0.5">{children}</li>,
        }}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  )
}

export const MarkdownBlock = memo(MarkdownBlockImpl)
