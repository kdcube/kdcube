import { memo, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'

import { closeStreamingMarkdown, markdownPlugins, standardMarkdownPlugins } from './support.ts'

export type MarkdownSoftBreakBehavior = 'line-break' | 'space'

export interface MarkdownBlockProps {
  content: string
  compact?: boolean
  softBreakBehavior?: MarkdownSoftBreakBehavior
}

function MarkdownBlockImpl({ content, compact = false, softBreakBehavior = 'line-break' }: MarkdownBlockProps) {
  const normalized = useMemo(() => closeStreamingMarkdown(content), [content])
  const plugins = softBreakBehavior === 'space' ? standardMarkdownPlugins : markdownPlugins

  return (
    <div className={`markdown-body ${compact ? 'markdown-body--compact text-[13px]' : 'text-[14px]'}`}>
      <ReactMarkdown
        remarkPlugins={plugins}
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
