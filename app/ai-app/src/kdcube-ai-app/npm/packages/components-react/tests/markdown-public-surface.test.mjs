import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import { MarkdownBlock } from '@kdcube/components-react/markdown'

test('the public Markdown renderer supports chat message content', () => {
  const html = renderToStaticMarkup(createElement(MarkdownBlock, {
    compact: true,
    content: '**Decision**\n\n- first\n- second\n\n[Source](https://example.com)',
  }))

  assert.match(html, /class="markdown-body markdown-body--compact/)
  assert.match(html, /<strong>Decision<\/strong>/)
  assert.match(html, /<ul/)
  assert.match(html, /<a href="https:\/\/example\.com" target="_blank" rel="noreferrer">Source<\/a>/)
})

test('the public Markdown renderer closes an unfinished fenced block', () => {
  const html = renderToStaticMarkup(createElement(MarkdownBlock, {
    content: '```ts\nconst ready = true',
  }))

  assert.match(html, /<pre><code class="language-ts">/)
  assert.match(html, /const ready = true/)
})

test('the public Markdown renderer can preserve standard soft-line semantics', () => {
  const html = renderToStaticMarkup(createElement(MarkdownBlock, {
    content: 'A sentence wraps in the\nsource without ending the paragraph.\n\nA new paragraph starts here.',
    softBreakBehavior: 'space',
  }))

  assert.doesNotMatch(html, /<br/)
  assert.equal((html.match(/<p /g) || []).length, 2)
  assert.match(html, /A sentence wraps in the\nsource without ending the paragraph\.<\/p>/)
  assert.match(html, /<p class="my-2 leading-6">A new paragraph starts here\.<\/p>/)
})

test('the public Markdown renderer keeps chat-style soft breaks by default', () => {
  const html = renderToStaticMarkup(createElement(MarkdownBlock, {
    content: 'first line\nsecond line',
  }))

  assert.match(html, /first line<br\/>\nsecond line/)
})
