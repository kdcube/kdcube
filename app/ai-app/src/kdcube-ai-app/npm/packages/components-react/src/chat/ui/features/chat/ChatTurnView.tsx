/** Chat view family — additive tab that renders the same turn data as
 *  Overview in a calmer chat-style layout.
 *
 *  Moved verbatim from App.tsx (Wave 2). See `turnTabs.tsx` for the Overview
 *  primitives this borrows from (FollowupMessageBlock, OverviewEvent +
 *  mergeOverviewEvents).
 */
import { memo, useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useChatViewModel } from '../../context.tsx'
import {
  formatBytes,
  formatTime,
  messageForError,
  shortUrl,
} from '../../support/utils.ts'
import { inferLanguage } from '../../support/highlight.ts'
import { MarkdownBlock } from '../../components/MarkdownBlock.tsx'
import { CaretIcon } from '../../components/CaretIcon.tsx'
import { SuggestedQuestions } from '../../components/SuggestedQuestions.tsx'
import { CopyButton } from '../../components/CopyButton.tsx'
import { DownloadButton } from '../../components/DownloadButton.tsx'
import { Snippet } from '../../components/Snippet.tsx'
import { CanvasRender, canvasFilename, canvasMime } from '../../components/CanvasRender.tsx'
import { CanvasExpandButton, CanvasModal } from '../../components/CanvasModal.tsx'
import type {
  Artifact,
  CanvasArtifact,
  ChatTurn,
  CodeExecArtifact,
  FileArtifact,
  LinkArtifact,
  NamedServiceSearchArtifact,
  NamedServiceSearchItem,
  NamespaceStyleMap,
  ServiceErrorArtifact,
  TimelineArtifact,
  TimelineEntry,
  WebFetchArtifact,
  WebSearchArtifact,
} from '@kdcube/components-core/chat'
import {
  FollowupMessageBlock,
  fetchStatusToneClass,
  mergeOverviewEvents,
  type OverviewEvent,
} from './turnTabs.tsx'
import { FaviconImg } from '../../components/Favicon.tsx'
import { FileExtIcon, fileExtension, fileKind } from '../../components/FileExtIcon.tsx'
import { canonicalObjectRef, contextChipStyle, isDirectDownloadObjectRef, namespaceFromObjectRef, namespaceStyleVars } from '@kdcube/components-core/chat'
import { setChatFileDragData } from '../../support/fileDrag.ts'
import { setContextDragData } from '../../support/contextDrop.ts'

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

function namedServiceSearchItemRef(item: NamedServiceSearchItem): string {
  const raw = item.object_ref || item.ref || item.id
  return typeof raw === 'string' ? raw : ''
}

function namedServiceContext(item: NamedServiceSearchItem): Record<string, unknown> {
  const ref = namedServiceSearchItemRef(item)
  const objectKind = item.object_kind || item.kind || item.search_scope || item.namespace
  return {
    ...item,
    kind: 'object.ref',
    object_kind: objectKind,
    id: item.id || ref,
    label: item.label || item.title || ref || 'result',
    ref,
    object_ref: ref,
  }
}

function ChatNamedServiceSearchBlockImpl({
  artifact,
  onError,
  namespaceStyles = {},
}: {
  artifact: NamedServiceSearchArtifact
  onError: (text: string) => void
  namespaceStyles?: NamespaceStyleMap
}) {
  const vm = useChatViewModel()
  const scope = artifact.searchScope || artifact.namespace || 'namespace'
  return (
    <details
      className="k-workitem k-namespace-tint"
      style={namespaceStyleVars(scope, namespaceStyles) as CSSProperties | undefined}
      open
    >
      <summary className="k-workitem-head">
        <span className="k-workitem-icon" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
            <line x1="7" y1="7" x2="7.01" y2="7" />
          </svg>
        </span>
        <span className="k-workitem-title">
          <span className="k-text">{artifact.title || 'Namespace search'}</span>
          <span className="k-micro">{scope} · {artifact.items.length} result{artifact.items.length === 1 ? '' : 's'}</span>
        </span>
        <span className="k-workitem-meta">{formatTime(artifact.timestamp)}</span>
        <CaretIcon />
      </summary>
      <div className="k-workitem-body">
        {artifact.query ? (
          <div className="k-query-row">
            <span className="k-micro">query</span>
            <span className="k-query-chip">{artifact.query}</span>
          </div>
        ) : null}
        {artifact.items.length > 0 ? (
          <div className="k-result-list">
            {artifact.items.slice(0, 8).map((item, idx) => {
              const context = namedServiceContext(item)
              const ref = namedServiceSearchItemRef(item)
              const label = item.label || item.title || ref || 'Search result'
              const subtitle = item.object_kind || item.search_scope || item.namespace || 'object'
              return (
                <button
                  key={`${ref || label}-${idx}`}
                  type="button"
                  draggable={Boolean(ref)}
                  className="k-result-row k-namespace-row text-left"
                  style={contextChipStyle(context, namespaceStyles) as CSSProperties | undefined}
                  title={item.summary || label}
                  onClick={() => {
                    vm.openContextChip(context as Parameters<typeof vm.openContextChip>[0])
                  }}
                  onDragStart={(event) => {
                    if (!ref) return
                    setContextDragData(event.dataTransfer, context)
                  }}
                >
                  <span className="k-workitem-icon" aria-hidden="true">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
                      <line x1="7" y1="7" x2="7.01" y2="7" />
                    </svg>
                  </span>
                  <div className="k-result-main">
                    <span className="k-result-title">{label}</span>
                    <span className="k-result-host">{subtitle}</span>
                    {item.summary ? <span className="k-result-body">{item.summary}</span> : null}
                  </div>
                  <span className="k-result-tag">[{idx + 1}]</span>
                </button>
              )
            })}
          </div>
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

/** Canvas artifact in Chat view — open, with expand + copy + download
 *  tools on the header bar. Expand opens a full-window `CanvasModal`
 *  for non-trivial HTML canvases. Mirrors `CanvasPanel`'s tool layout. */
function ChatCanvasBlockImpl({ artifact }: { artifact: CanvasArtifact }) {
  const [modalOpen, setModalOpen] = useState(false)
  return (
    <>
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
          {/* Stop propagation so clicking the tools doesn't toggle the
              <details> open/closed state. */}
          <span className="k-snippet-tools" onClick={(e) => e.stopPropagation()}>
            <CanvasExpandButton onClick={() => setModalOpen(true)} />
            <CopyButton value={artifact.content} title="Copy artifact" />
            <DownloadButton
              data={artifact.content}
              filename={canvasFilename(artifact)}
              mime={canvasMime(artifact)}
              title="Download artifact"
            />
          </span>
          <CaretIcon />
        </summary>
        <div className="k-workitem-body">
          <CanvasRender canvas={artifact} />
        </div>
      </details>
      {modalOpen ? <CanvasModal canvas={artifact} onClose={() => setModalOpen(false)} /> : null}
    </>
  )
}

/** Timeline-text artifact in Chat view — flat in-flow markdown note.
 *
 *  No bordered card, no chip, no timestamp. The note is simply written into
 *  the transcript flow as a markdown block so it reads like the assistant
 *  jotted it down inline. */
/* Timeline blocks include the assistant's reaction notes AND its final
 * answer(s): the answer is just another block in the chronological feed
 * (`final_answer:<attempt>`), styled prominently so it still reads as the
 * response while staying in order with the events around it. */
function ChatTimelineBlockImpl({ artifact }: { artifact: TimelineArtifact }) {
  if (!artifact.markdown || !artifact.markdown.trim()) return null
  if (/^final_answer:\d+$/.test(artifact.name)) {
    return (
      <div className="k-msg mt-1 rounded-md border border-[var(--line-soft)] bg-[var(--surface)] px-3 py-2">
        <MarkdownBlock content={artifact.markdown} />
        <span className="k-msg-toolbar">
          <CopyButton value={artifact.markdown} title="Copy answer" />
        </span>
      </div>
    )
  }
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

/** File artifact in Chat view — extension-aware icon + resolver-backed Download.
 *
 *  The public identity is `objectRef`/`logicalPath` (usually `fi:`).
 *  Download and drag both use that canonical object ref; transport handles
 *  stay behind the resolver boundary. */
function isPreviewableImageFile(artifact: FileArtifact, iconKind: ReturnType<typeof fileKind>['icon']): boolean {
  const mime = artifact.mime ? artifact.mime.toLowerCase() : ''
  return mime.startsWith('image/') || iconKind === 'image'
}

function ChatFileBlockImpl({
  artifact,
  onError,
  namespaceStyles = {},
}: {
  artifact: FileArtifact
  onError: (text: string) => void
  namespaceStyles?: NamespaceStyleMap
}) {
  const vm = useChatViewModel()
  const [downloading, setDownloading] = useState(false)
  const ext = fileExtension(artifact.filename)
  const kind = fileKind(ext)
  const pinRef = canonicalObjectRef(artifact.objectRef, artifact.logicalPath)
  // A durable `fi:` artifact downloads directly; any other namespace ref OPENS
  // through its resolver (object.action) — matching the in-tree widget.
  const isDirectDownload = Boolean(pinRef && isDirectDownloadObjectRef(pinRef))
  const canActivate = Boolean(pinRef)
  const namespace = namespaceFromObjectRef(pinRef)
  const namespaceVars = namespace
    ? (namespaceStyleVars(namespace, namespaceStyles) as CSSProperties | undefined)
    : undefined
  const actionLabel = isDirectDownload ? 'Download' : 'Open'
  const extLabel = isDirectDownload ? kind.label : (namespace ? namespace.toUpperCase() : kind.label)
  const subtitle = artifact.description || artifact.mime || pinRef || ''
  const imagePreviewable = Boolean(isDirectDownload && pinRef && isPreviewableImageFile(artifact, kind.icon))
  const loadFileBlob = vm.loadFileBlob
  const [imageUrl, setImageUrl] = useState<string | null>(null)

  useEffect(() => {
    if (!imagePreviewable || !pinRef) {
      setImageUrl(null)
      return undefined
    }
    setImageUrl(null)
    let cancelled = false
    let objectUrl: string | null = null
    loadFileBlob(pinRef, artifact.filename, artifact.mime ?? undefined)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setImageUrl(objectUrl)
      })
      .catch(() => {
        if (!cancelled) setImageUrl(null)
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [artifact.filename, artifact.mime, imagePreviewable, loadFileBlob, pinRef])

  const handle = () => {
    if (!canActivate || downloading) return
    if (isDirectDownload) {
      try {
        setDownloading(true)
        vm.downloadFile(pinRef, artifact.filename, artifact.mime ?? undefined)
      } catch (error) {
        onError(messageForError(error))
      } finally {
        setDownloading(false)
      }
      return
    }
    // Open via the resolver. The engine runs the action and surfaces any failure as
    // a banner, so there's no local error plumbing (and openContextChip is void).
    vm.openContextChip({
      id: pinRef,
      kind: 'object.ref',
      label: artifact.filename || pinRef,
      title: artifact.filename || pinRef,
      summary: artifact.description || undefined,
      ref: pinRef,
      object_ref: pinRef,
      mime: artifact.mime,
      namespace: namespace || undefined,
      data: {
        source: 'chat.file_artifact',
        object_ref: pinRef,
        filename: artifact.filename,
        mime: artifact.mime,
      },
    } as Parameters<typeof vm.openContextChip>[0])
  }
  return (
    <>
      {imageUrl ? (
        <figure className="k-chat-image-preview">
          <a href={imageUrl} target="_blank" rel="noreferrer" title={`Open ${artifact.filename}`}>
            <img src={imageUrl} alt={artifact.description || artifact.filename} loading="lazy" />
          </a>
          <figcaption>{artifact.filename}</figcaption>
        </figure>
      ) : null}
      <button
        type="button"
        draggable={Boolean(pinRef)}
        onClick={() => handle()}
        onDragStart={(event) => {
          if (!pinRef) return
          setChatFileDragData(event.dataTransfer, {
            ref: pinRef,
            filename: artifact.filename,
            mime: artifact.mime,
            preview: artifact.description,
            sourceKind: 'assistant.file',
          }, event)
        }}
        disabled={!pinRef || downloading}
        className="k-chat-file"
        style={namespaceVars}
        title={
          pinRef
            ? `${actionLabel} ${artifact.filename}; drag to attach or pin`
            : artifact.filename
        }
      >
        <span className="k-chat-file-icon" aria-hidden="true">
          <FileExtIcon kind={kind.icon} />
        </span>
        <span className="k-chat-file-main">
          <span className="k-chat-file-name">{artifact.filename}</span>
          {subtitle ? <span className="k-chat-file-sub">{subtitle}</span> : null}
        </span>
        <span className="k-chat-file-ext">{extLabel}</span>
        <span className="k-chat-file-action">
          {downloading ? (
            isDirectDownload ? 'Downloading…' : 'Opening…'
          ) : canActivate ? (
            <>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
              </svg>
              <span>{actionLabel}</span>
            </>
          ) : (
            'Unavailable'
          )}
        </span>
      </button>
    </>
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
  namespaceStyles = {},
}: {
  artifact: Artifact
  onDownloadError: (text: string) => void
  namespaceStyles?: NamespaceStyleMap
}) {
  switch (artifact.kind) {
    case 'web_search': return <ChatWebSearchBlock artifact={artifact} />
    case 'named_service_search': return <ChatNamedServiceSearchBlock artifact={artifact} onError={onDownloadError} namespaceStyles={namespaceStyles} />
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
    case 'file':       return <ChatFileBlock artifact={artifact} onError={onDownloadError} namespaceStyles={namespaceStyles} />
    case 'service_error': return <ChatServiceErrorBlock artifact={artifact} />
    default:           return null
  }
}

function ChatMergedFeedImpl({
  events,
  onDownloadError,
  namespaceStyles = {},
}: {
  events: OverviewEvent[]
  onDownloadError: (text: string) => void
  namespaceStyles?: NamespaceStyleMap
}) {
  if (events.length === 0) return null
  return (
    <div className="flex flex-col gap-2 pt-1">
      {events.map((event) => {
        if (event.kind === 'followup') {
          return (
            <FollowupMessageBlock
              key={event.key}
              message={event.message}
              onDownloadError={onDownloadError}
              namespaceStyles={namespaceStyles}
            />
          )
        }
        return (
          <ChatArtifactRow
            key={event.key}
            artifact={event.artifact}
            onDownloadError={onDownloadError}
            namespaceStyles={namespaceStyles}
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
  namespaceStyles = {},
}: {
  turn: ChatTurn
  sendingDisabled: boolean
  onFollowup: (text: string) => void
  onDownloadError: (text: string) => void
  namespaceStyles?: NamespaceStyleMap
}) {
  const thinkingEntries = useMemo(
    () => turn.timeline.filter((entry) => entry.kind === 'thinking'),
    [turn.timeline],
  )
  const overviewEvents = useMemo(
    () => mergeOverviewEvents(turn.artifacts, turn.additionalUserMessages),
    [turn.artifacts, turn.additionalUserMessages],
  )
  /* The answer(s) render inline in the feed above (as `final_answer:*` timeline
   * blocks), so the turn no longer pins a separate answer bubble at the bottom.
   * Only the error notice and the pre-answer streaming hint remain. */
  const isStreaming = turn.state === 'pending' || turn.state === 'running'
  return (
    <div className="k-chat-view">
      {turn.interruptedNotice ? (
        <div className="k-notice k-info">
          <span>{turn.interruptedNotice}</span>
        </div>
      ) : null}
      <ChatThinkingTimeline entries={thinkingEntries} streaming={isStreaming} />
      <ChatMergedFeed events={overviewEvents} onDownloadError={onDownloadError} namespaceStyles={namespaceStyles} />
      {turn.state === 'error' ? (
        <div className="k-notice k-error">
          <span>{turn.error || 'Request failed.'}</span>
        </div>
      ) : isStreaming && !turn.answer ? (
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
export const ChatNamedServiceSearchBlock = memo(ChatNamedServiceSearchBlockImpl)
export const ChatWebFetchBlock = memo(ChatWebFetchBlockImpl)
export const ChatCodeExecBlock = memo(ChatCodeExecBlockImpl)
export const ChatCanvasBlock = memo(ChatCanvasBlockImpl)
export const ChatTimelineBlock = memo(ChatTimelineBlockImpl)
export const ChatFileBlock = memo(ChatFileBlockImpl)
export const ChatServiceErrorBlock = memo(ChatServiceErrorBlockImpl)
export const ChatArtifactRow = memo(ChatArtifactRowImpl)
export const ChatMergedFeed = memo(ChatMergedFeedImpl)
export const ChatTurnView = memo(ChatTurnViewImpl)
