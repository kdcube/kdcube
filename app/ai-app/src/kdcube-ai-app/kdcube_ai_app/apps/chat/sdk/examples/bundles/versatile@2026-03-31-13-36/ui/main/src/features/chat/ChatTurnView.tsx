/** Chat view family — additive tab that renders the same turn data as
 *  Overview in a calmer chat-style layout.
 *
 *  Moved verbatim from App.tsx (Wave 2). See `turnTabs.tsx` for the Overview
 *  primitives this borrows from (FollowupMessageBlock, OverviewEvent +
 *  mergeOverviewEvents).
 */
import { memo, useMemo, useState } from 'react'
import { downloadResourceByRN } from '../../service.ts'
import {
  formatBytes,
  formatTime,
  messageForError,
  shortUrl,
} from '../../components/utils.ts'
import { inferLanguage } from '../../components/highlight.ts'
import { MarkdownBlock } from '../../components/MarkdownBlock.tsx'
import { CaretIcon } from '../../components/CaretIcon.tsx'
import { SuggestedQuestions } from '../../components/SuggestedQuestions.tsx'
import { CopyButton } from '../../components/CopyButton.tsx'
import { DownloadButton } from '../../components/DownloadButton.tsx'
import { Snippet } from '../../components/Snippet.tsx'
import { CanvasRender, canvasFilename, canvasMime } from '../../components/CanvasRender.tsx'
import type {
  Artifact,
  CanvasArtifact,
  ChatTurn,
  CodeExecArtifact,
  FileArtifact,
  LinkArtifact,
  ServiceErrorArtifact,
  TimelineArtifact,
  TimelineEntry,
  WebFetchArtifact,
  WebSearchArtifact,
} from './chatTypes.ts'
import {
  FollowupMessageBlock,
  fetchStatusToneClass,
  mergeOverviewEvents,
  type OverviewEvent,
} from './turnTabs.tsx'
import { FaviconImg } from '../../components/Favicon.tsx'
import { FileExtIcon, fileExtension, fileKind } from '../../components/FileExtIcon.tsx'

/* ---------------------------------------------------------------------- */
/*  Chat view                                                             */
/*                                                                        */
/*  A "light" rendering of the same turn data Overview uses. The Chat     */
/*  view trims agent-name noise from thinking, surfaces favicons on web   */
/*  search/fetch results, and auto-collapses code-exec while keeping      */
/*  canvas + search expanded. All §3 primitives, no new colours.          */
/*                                                                        */
/*  Data sources are intentionally the same as Overview                   */
/*  (mergeOverviewEvents + turn.timeline.kind === 'thinking') — there is  */
/*  no parallel state and no new event handler.                           */
/* ---------------------------------------------------------------------- */

/* resolveFavicon + FaviconImg now live in ../../components/Favicon.tsx
 * and are imported above. */

/** Strip markdown syntax to a short plain-text preview.
 *
 *  Used by the collapsed Thinking heading so we can echo the latest streaming
 *  thinking content without exposing fenced code or markdown noise. */
export function shortenForPreview(raw: string, max = 120): string {
  if (!raw) return ''
  /* Collapse fenced code blocks and inline code, strip basic markdown
     decorations, then squash whitespace. */
  const stripped = raw
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/[*_~>#]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (stripped.length <= max) return stripped
  return `${stripped.slice(0, Math.max(0, max - 1)).trimEnd()}…`
}

/** Compact thinking timeline for the Chat view.
 *
 *  Always collapsible. While streaming, the collapsed heading echoes a short
 *  preview of the latest thinking step so the viewer sees what the model is
 *  reasoning about without expanding. After the turn completes the heading
 *  collapses to a calm "Thinking · N steps". The block defaults to expanded
 *  while live, collapsed once finished.
 *
 *  Visual: dotted timeline, steel-blue dots, faded body text. Light grey
 *  surface when collapsed. */
function ChatThinkingTimelineImpl({
  entries,
  streaming,
}: {
  entries: TimelineEntry[]
  streaming: boolean
}) {
  /* useMemo must run on every render — keep it above any early return
   * so the hook call order is stable. The empty-entries case is then
   * handled below as a render-time branch. (React error #310 fix.) */
  const sorted = useMemo(
    () => entries.slice().sort((left, right) => left.timestamp - right.timestamp),
    [entries],
  )
  if (entries.length === 0) return null
  const latest = sorted[sorted.length - 1]
  const latestPreview = streaming ? shortenForPreview(latest?.body || '', 120) : ''
  /* Default open while streaming, closed once the turn is done. Each render
     uses a fresh `key` derived from streaming so React resets the
     <details> open state when the turn transitions out of streaming. */
  return (
    <details
      className={`k-chat-think ${streaming ? 'k-chat-think--live' : 'k-chat-think--done'}`}
      open={streaming}
    >
      <summary className="k-chat-think-head">
        <span className="k-status k-warn" aria-hidden="true" />
        <span className="k-chat-think-title">Thinking</span>
        {streaming && latestPreview ? (
          <span className="k-chat-think-preview">{latestPreview}</span>
        ) : (
          <span className="k-chat-think-count">
            {sorted.length} step{sorted.length === 1 ? '' : 's'}
          </span>
        )}
        <CaretIcon />
      </summary>
      <ol className="k-chat-think-list">
        {sorted.map((entry) => (
          <li key={entry.id} className="k-chat-think-item">
            <span className="k-chat-think-dot" aria-hidden="true" />
            <div className="k-chat-think-body">
              {entry.body ? (
                <MarkdownBlock content={entry.body} compact />
              ) : (
                <p className="k-chat-think-empty">Reasoning step.</p>
              )}
            </div>
          </li>
        ))}
      </ol>
    </details>
  )
}

/** Web search artifact rendered for the Chat view.
 *  Same shape as ArtifactFeed's web_search render but with real favicons
 *  and "queries · objective" demoted to a single muted line. Open by default. */
function ChatWebSearchBlockImpl({ artifact }: { artifact: WebSearchArtifact }) {
  return (
    <details className="k-workitem k-tint-sky">
      <summary className="k-workitem-head">
        <span className="k-workitem-icon" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4.3-4.3" />
          </svg>
        </span>
        <span className="k-workitem-title">
          <span className="k-text">{artifact.title || artifact.name || 'Web search'}</span>
          <span className="k-micro">web search · {artifact.items.length}</span>
        </span>
        <span className="k-workitem-meta">{formatTime(artifact.timestamp)}</span>
        <CaretIcon />
      </summary>
      <div className="k-workitem-body">
        {artifact.objective ? (
          <p className="text-[12px] text-[var(--muted)]">{artifact.objective}</p>
        ) : null}
        {artifact.queries.length > 0 ? (
          <div className="k-query-row">
            <span className="k-micro">queries</span>
            {artifact.queries.map((query) => (
              <span key={query} className="k-query-chip">{query}</span>
            ))}
          </div>
        ) : null}
        {artifact.items.length > 0 ? (
          <div className="k-result-list">
            {artifact.items.slice(0, 6).map((item, idx) => (
              <a
                key={item.url}
                href={item.url}
                target="_blank"
                rel="noreferrer"
                className="k-result-row"
              >
                <FaviconImg url={item.url} favicon={item.favicon} />
                <div className="k-result-main">
                  <span className="k-result-title">{item.title || shortUrl(item.url)}</span>
                  <span className="k-result-host">{shortUrl(item.url)}</span>
                  {item.body ? <span className="k-result-body">{item.body}</span> : null}
                </div>
                <span className="k-result-tag">[{idx + 1}]</span>
              </a>
            ))}
          </div>
        ) : null}
        {artifact.reportContent ? (
          <details>
            <summary className="cursor-pointer text-[12px] font-medium text-[var(--blue-dark)]">
              Show report
            </summary>
            <div className="mt-1 max-h-[360px] overflow-auto pr-1">
              <MarkdownBlock content={artifact.reportContent} compact />
            </div>
          </details>
        ) : null}
      </div>
    </details>
  )
}

/** Web fetch artifact rendered for the Chat view (favicons + coloured
 *  per-row status). Collapsed by default — users open it when they care. */
function ChatWebFetchBlockImpl({ artifact }: { artifact: WebFetchArtifact }) {
  return (
    <details className="k-workitem k-tint-gold">
      <summary className="k-workitem-head">
        <span className="k-workitem-icon" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 12a9 9 0 1 1-9-9" />
            <path d="M21 3v6h-6" />
          </svg>
        </span>
        <span className="k-workitem-title">
          <span className="k-text">{artifact.title || artifact.name || 'Web fetch'}</span>
          <span className="k-micro">web fetch · {artifact.items.length}</span>
        </span>
        <span className="k-workitem-meta">{formatTime(artifact.timestamp)}</span>
        <CaretIcon />
      </summary>
      <div className="k-workitem-body">
        <div className="k-result-list">
          {artifact.items.slice(0, 6).map((item) => (
            <a
              key={item.url}
              href={item.url}
              target="_blank"
              rel="noreferrer"
              className="k-result-row"
            >
              <FaviconImg url={item.url} favicon={item.favicon} />
              <div className="k-result-main">
                <span className="k-result-title">{shortUrl(item.url)}</span>
                <span className="k-result-host">
                  <span className={fetchStatusToneClass(item.status)}>
                    {(item.status || 'unknown').toUpperCase()}
                  </span>
                  {item.mime ? ` · ${item.mime}` : ''}
                  {typeof item.content_length === 'number' ? ` · ${formatBytes(item.content_length)}` : ''}
                </span>
              </div>
            </a>
          ))}
        </div>
      </div>
    </details>
  )
}

/** Code exec artifact in Chat view — collapsed by default. */
function ChatCodeExecBlockImpl({ artifact }: { artifact: CodeExecArtifact }) {
  const statusLabel =
    artifact.status?.status === 'error'
      ? 'Error'
      : artifact.status?.status === 'exec'
        ? 'Executing'
        : artifact.status?.status === 'gen'
          ? 'Generating'
          : artifact.status?.status === 'done'
            ? 'Done'
            : 'Ready'
  const isError = artifact.status?.status === 'error'
  const isRunning = artifact.status?.status === 'exec' || artifact.status?.status === 'gen'
  const lang = inferLanguage(null, artifact.program || '')
  return (
    <details
      className={`k-workitem k-tint-purple ${isError ? 'k-err' : isRunning ? 'k-live' : ''}`}
      /* Chat view auto-collapses code unless it errored or is mid-run. */
      open={isError || isRunning}
    >
      <summary className="k-workitem-head">
        <span className="k-workitem-icon" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="16 18 22 12 16 6" />
            <polyline points="8 6 2 12 8 18" />
          </svg>
        </span>
        <span className="k-workitem-title">
          <span className="k-text">{artifact.title || artifact.name || 'Program'}</span>
          <span className="k-micro">exec · {statusLabel.toLowerCase()}</span>
        </span>
        <span className="k-workitem-meta">{formatTime(artifact.timestamp)}</span>
        <CaretIcon />
      </summary>
      <div className="k-workitem-body">
        {artifact.objective ? (
          <p className="text-[12px] text-[var(--muted)]">{artifact.objective}</p>
        ) : null}
        {artifact.contract && artifact.contract.length > 0 ? (
          <div className="k-result-list">
            {artifact.contract.map((item) => (
              <div key={item.filename} className="k-result-row" style={{ gridTemplateColumns: 'auto minmax(0,1fr)' }}>
                <span className="k-workitem-icon" style={{ width: 18, height: 18 }}>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <path d="M14 2v6h6" />
                  </svg>
                </span>
                <div className="k-result-main">
                  <span className="k-result-title">{item.filename}</span>
                  {item.description ? <span className="k-result-host">{item.description}</span> : null}
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {artifact.program ? (
          <Snippet
            content={artifact.program}
            format="code"
            language={lang}
            label={lang}
            filename={`program.${lang === 'python' ? 'py' : lang === 'javascript' ? 'js' : lang === 'bash' ? 'sh' : 'txt'}`}
            downloadMime="text/plain"
            showDownload
          />
        ) : null}
        {artifact.status?.status === 'error' && artifact.status.error ? (
          <div className="k-notice k-error">
            <span>{Object.values(artifact.status.error).join(' ')}</span>
          </div>
        ) : null}
      </div>
    </details>
  )
}

/** Canvas artifact in Chat view — open, with copy + download tools on
 *  the header bar so the user can grab the content without expanding
 *  Overview. Mirrors `CanvasPanel`'s tool layout from the Canvas tab. */
function ChatCanvasBlockImpl({ artifact }: { artifact: CanvasArtifact }) {
  return (
    <details className="k-workitem k-tint-green" open>
      <summary className="k-workitem-head">
        <span className="k-workitem-icon" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <path d="M3 9h18M9 21V9" />
          </svg>
        </span>
        <span className="k-workitem-title">
          <span className="k-text">{artifact.title || artifact.name}</span>
          <span className="k-micro">{artifact.format || 'text'}</span>
        </span>
        {/* Stop propagation so clicking copy/download doesn't toggle the
            <details> open/closed state. */}
        <span className="k-snippet-tools" onClick={(e) => e.stopPropagation()}>
          <CopyButton value={artifact.content} title="Copy canvas" />
          <DownloadButton
            data={artifact.content}
            filename={canvasFilename(artifact)}
            mime={canvasMime(artifact)}
            title="Download canvas"
          />
        </span>
        <CaretIcon />
      </summary>
      <div className="k-workitem-body">
        <CanvasRender canvas={artifact} />
      </div>
    </details>
  )
}

/** Timeline-text artifact in Chat view — flat in-flow markdown note.
 *
 *  No bordered card, no chip, no timestamp. The note is simply written into
 *  the transcript flow as a markdown block so it reads like the assistant
 *  jotted it down inline. */
function ChatTimelineBlockImpl({ artifact }: { artifact: TimelineArtifact }) {
  if (!artifact.markdown || !artifact.markdown.trim()) return null
  return (
    <div className="k-chat-note">
      <MarkdownBlock content={artifact.markdown} compact />
    </div>
  )
}

/* Citation artifacts are intentionally not rendered in Chat view.
 * See ChatArtifactRow comment for the rationale. */

/** Lowercase extension extractor for a filename (returns 'png', 'pdf', …
 *  or '' when the name has no dot). */

/** File artifact in Chat view — extension-aware icon + working Download.
 *
 *  Click the row to download via the existing `downloadResourceByRN`
 *  helper. The download button mirrors `DownloadsPanel`'s behaviour so
 *  failures surface through the parent's `onError` channel. */
function ChatFileBlockImpl({
  artifact,
  onError,
}: {
  artifact: FileArtifact
  onError: (text: string) => void
}) {
  const [downloading, setDownloading] = useState(false)
  const ext = fileExtension(artifact.filename)
  const kind = fileKind(ext)
  const canDownload = Boolean(artifact.rn)
  const subtitle = artifact.description || artifact.mime || (artifact.rn ? artifact.rn.split(':').pop() : '') || ''
  const handle = async () => {
    if (!canDownload || downloading) return
    try {
      setDownloading(true)
      await downloadResourceByRN(artifact.rn, artifact.filename)
    } catch (error) {
      onError(messageForError(error))
    } finally {
      setDownloading(false)
    }
  }
  return (
    <button
      type="button"
      onClick={() => void handle()}
      disabled={!canDownload || downloading}
      className="k-chat-file"
      title={canDownload ? `Download ${artifact.filename}` : artifact.filename}
    >
      <span className="k-chat-file-icon" aria-hidden="true">
        <FileExtIcon kind={kind.icon} />
      </span>
      <span className="k-chat-file-main">
        <span className="k-chat-file-name">{artifact.filename}</span>
        {subtitle ? <span className="k-chat-file-sub">{subtitle}</span> : null}
      </span>
      <span className="k-chat-file-ext">{kind.label}</span>
      <span className="k-chat-file-action">
        {downloading ? (
          'Downloading…'
        ) : canDownload ? (
          <>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
            </svg>
            <span>Download</span>
          </>
        ) : (
          'Unavailable'
        )}
      </span>
    </button>
  )
}

/** Service error artifact in Chat view — inline error notice. */
function ChatServiceErrorBlockImpl({ artifact }: { artifact: ServiceErrorArtifact }) {
  return (
    <div className="k-notice k-error">
      <span>{artifact.message}</span>
    </div>
  )
}

function ChatArtifactRowImpl({
  artifact,
  onDownloadError,
}: {
  artifact: Artifact
  onDownloadError: (text: string) => void
}) {
  switch (artifact.kind) {
    case 'web_search': return <ChatWebSearchBlock artifact={artifact} />
    case 'web_fetch':  return <ChatWebFetchBlock artifact={artifact} />
    case 'code_exec':  return <ChatCodeExecBlock artifact={artifact} />
    case 'canvas':     return <ChatCanvasBlock artifact={artifact} />
    case 'timeline':   return <ChatTimelineBlock artifact={artifact} />
    /* Chat view intentionally suppresses bare citation cards.
     *
     *  Citation artifacts come from the `citations` step and overlap with
     *  what `web_search` already shows. Surfacing them again at the top
     *  level produced the "two raw HTML-entity cards above the search
     *  block" symptom. If a future design needs a separate "Sources" list,
     *  build it from `collectTurnLinks(turn.artifacts)` and render it as a
     *  small footnote section below the answer — not as bordered cards in
     *  the chronological flow. */
    case 'citation':   return null
    case 'file':       return <ChatFileBlock artifact={artifact} onError={onDownloadError} />
    case 'service_error': return <ChatServiceErrorBlock artifact={artifact} />
    default:           return null
  }
}

function ChatMergedFeedImpl({
  events,
  onDownloadError,
}: {
  events: OverviewEvent[]
  onDownloadError: (text: string) => void
}) {
  if (events.length === 0) return null
  return (
    <div className="flex flex-col gap-2 pt-1">
      {events.map((event) => {
        if (event.kind === 'followup') {
          return <FollowupMessageBlock key={event.key} message={event.message} />
        }
        return (
          <ChatArtifactRow
            key={event.key}
            artifact={event.artifact}
            onDownloadError={onDownloadError}
          />
        )
      })}
    </div>
  )
}

function ChatTurnViewImpl({
  turn,
  sendingDisabled,
  onFollowup,
  onDownloadError,
}: {
  turn: ChatTurn
  sendingDisabled: boolean
  onFollowup: (text: string) => void
  onDownloadError: (text: string) => void
}) {
  const thinkingEntries = useMemo(
    () => turn.timeline.filter((entry) => entry.kind === 'thinking'),
    [turn.timeline],
  )
  const overviewEvents = useMemo(
    () => mergeOverviewEvents(turn.artifacts, turn.additionalUserMessages),
    [turn.artifacts, turn.additionalUserMessages],
  )
  const isStreaming = turn.state === 'pending' || turn.state === 'running'
  return (
    <div className="k-chat-view">
      <ChatThinkingTimeline entries={thinkingEntries} streaming={isStreaming} />
      <ChatMergedFeed events={overviewEvents} onDownloadError={onDownloadError} />
      {turn.answer ? (
        <div className="k-msg mt-1 rounded-md border border-[var(--line-soft)] bg-[var(--surface)] px-3 py-2">
          <MarkdownBlock content={turn.answer} />
          <span className="k-msg-toolbar">
            <CopyButton value={turn.answer} title="Copy answer" />
          </span>
        </div>
      ) : turn.state === 'error' ? (
        <div className="k-notice k-error">
          <span>{turn.error || 'Request failed.'}</span>
        </div>
      ) : isStreaming ? (
        <div className="flex items-center gap-2 text-[12px] text-[var(--muted)]">
          <span className="k-status k-live" />
          <span>Streaming response…</span>
        </div>
      ) : null}
      <SuggestedQuestions items={turn.followups} disabled={sendingDisabled} onSelect={onFollowup} />
    </div>
  )
}


/* ---------------------------------------------------------------------- */
/*  Memoised component exports.                                           */
/*                                                                        */
/*  Each Chat-tab block is wrapped in `React.memo`. When a streaming      */
/*  delta updates only `turn.answer`, the artifact references stay        */
/*  stable thanks to Immer, so every block above the answer is skipped    */
/*  by the reconciler. The active turn re-renders only the parts that    */
/*  actually changed (the answer body or the streaming code-exec block). */
/* ---------------------------------------------------------------------- */

export const ChatThinkingTimeline = memo(ChatThinkingTimelineImpl)
export const ChatWebSearchBlock = memo(ChatWebSearchBlockImpl)
export const ChatWebFetchBlock = memo(ChatWebFetchBlockImpl)
export const ChatCodeExecBlock = memo(ChatCodeExecBlockImpl)
export const ChatCanvasBlock = memo(ChatCanvasBlockImpl)
export const ChatTimelineBlock = memo(ChatTimelineBlockImpl)
export const ChatFileBlock = memo(ChatFileBlockImpl)
export const ChatServiceErrorBlock = memo(ChatServiceErrorBlockImpl)
export const ChatArtifactRow = memo(ChatArtifactRowImpl)
export const ChatMergedFeed = memo(ChatMergedFeedImpl)
export const ChatTurnView = memo(ChatTurnViewImpl)
