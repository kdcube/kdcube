import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'

/** CommonMark-compatible plugins: soft line breaks remain spaces. */
export const standardMarkdownPlugins = [remarkGfm]

/** Chat-oriented plugins: soft line breaks render as explicit breaks. */
export const markdownPlugins = [remarkGfm, remarkBreaks]

/** Close partial fenced blocks so streamed content cannot break its container. */
export function closeStreamingMarkdown(text: string): string {
  const tripleBackticks = text.match(/```/g)?.length || 0
  const tripleTildes = text.match(/~~~/g)?.length || 0
  let next = text
  if (tripleBackticks % 2 === 1) next += '\n```'
  if (tripleTildes % 2 === 1) next += '\n~~~'
  return next
}
