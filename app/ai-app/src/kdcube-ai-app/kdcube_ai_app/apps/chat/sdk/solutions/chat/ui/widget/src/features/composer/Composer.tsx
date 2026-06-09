/** Send composer (textarea + attach + submit). Memoised — composer
 *  props (text / files / disabled / inProgress / lockedMessage) change
 *  often but the bulk of the chat transcript doesn't re-render when
 *  they do, since the transcript is rendered by sibling components. */
import { memo, useEffect, useMemo, useRef, useState, type DragEvent, type KeyboardEvent, type MouseEvent } from 'react'
import { formatBytes } from '../../components/utils.ts'
import type { AttachedContext } from '../chat/chatTypes.ts'
import { recognizeContextMessage } from '../../host.ts'
import { CHAT_CONTEXT_ATTACH_MESSAGE } from '../../settings.ts'
import { recognizeContextPayload } from '../context/contextMessages.ts'

const CONTEXT_DROP_MIME_TYPES = [
  'application/vnd.kdcube.context+json',
  'application/json',
]

function contextTypeLabel(ctx: AttachedContext): string {
  return ctx.cardType || ctx.kind
}

function contextChipClass(ctx: AttachedContext): string {
  const classes = [ctx.kind, ctx.cardType].filter(Boolean)
  return classes.map((item) => String(item).replace('.', '-')).join(' ')
}

function parseDroppedContexts(dataTransfer: DataTransfer): AttachedContext[] {
  for (const mimeType of CONTEXT_DROP_MIME_TYPES) {
    const raw = dataTransfer.getData(mimeType)
    if (!raw) continue
    try {
      const payload = JSON.parse(raw)
      if (mimeType === 'application/vnd.kdcube.context+json') {
        const fromContextPayload = recognizeContextPayload(payload)
        if (fromContextPayload.length > 0) return fromContextPayload
      }
      const fromMessage = recognizeContextMessage(payload)
      if (fromMessage.length > 0) return fromMessage
      const wrapped = recognizeContextMessage({
        type: CHAT_CONTEXT_ATTACH_MESSAGE,
        context: payload,
      })
      if (wrapped.length > 0) return wrapped
    } catch {
      // Try the next MIME flavor. Drag payloads often expose both a precise
      // application/vnd.kdcube.* payload and a generic application/json copy.
    }
  }
  return []
}

function ComposerImpl({
  text,
  files,
  contexts,
  disabled,
  inProgress,
  lockedMessage,
  onTextChange,
  onFilesAdd,
  onFileRemove,
  onContextsAdd,
  onContextRemove,
  onContextRemoveMany,
  onSubmit,
  onStop,
}: {
  text: string
  files: File[]
  contexts: AttachedContext[]
  disabled: boolean
  inProgress: boolean
  lockedMessage: string | null
  onTextChange: (value: string) => void
  onFilesAdd: (files: FileList | null) => void
  onFileRemove: (index: number) => void
  onContextsAdd: (contexts: AttachedContext[]) => void
  onContextRemove: (id: string) => void
  onContextRemoveMany: (ids: string[]) => void
  onSubmit: () => void
  onStop: () => void
}) {
  const composerRef = useRef<HTMLDivElement | null>(null)
  const [selectedContextIds, setSelectedContextIds] = useState<string[]>([])
  const selectedContextIdSet = useMemo(() => new Set(selectedContextIds), [selectedContextIds])

  useEffect(() => {
    setSelectedContextIds((current) => (
      current.filter((id) => contexts.some((ctx) => ctx.id === id))
    ))
  }, [contexts])

  function removeContexts(ids: string[]) {
    const uniqueIds = Array.from(new Set(ids.filter(Boolean)))
    if (!uniqueIds.length) return
    setSelectedContextIds((current) => current.filter((id) => !uniqueIds.includes(id)))
    onContextRemoveMany(uniqueIds)
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const key = event.key.toLowerCase()
    if ((event.metaKey || event.ctrlKey) && key === 'a' && contexts.length > 0) {
      event.preventDefault()
      setSelectedContextIds(contexts.map((ctx) => ctx.id))
      return
    }
    if ((event.key === 'Backspace' || event.key === 'Delete') && selectedContextIds.length > 0) {
      event.preventDefault()
      removeContexts(selectedContextIds)
      return
    }
    if (event.key === 'Escape' && selectedContextIds.length > 0) {
      event.preventDefault()
      setSelectedContextIds([])
    }
  }

  function handleContextChipClick(ctx: AttachedContext, event: MouseEvent<HTMLSpanElement>) {
    if ((event.target as HTMLElement).closest('button')) return
    setSelectedContextIds((current) => {
      if (event.metaKey || event.ctrlKey) {
        return current.includes(ctx.id)
          ? current.filter((id) => id !== ctx.id)
          : [...current, ctx.id]
      }
      return [ctx.id]
    })
  }

  function handleComposerDragOver(event: DragEvent<HTMLDivElement>) {
    if (parseDroppedContexts(event.dataTransfer).length === 0) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
  }

  function handleComposerDrop(event: DragEvent<HTMLDivElement>) {
    const droppedContexts = parseDroppedContexts(event.dataTransfer)
    if (droppedContexts.length === 0) return
    event.preventDefault()
    event.stopPropagation()
    onContextsAdd(droppedContexts)
  }

  return (
    <div ref={composerRef} className="flex flex-col gap-2" onKeyDownCapture={handleComposerKeyDown}>
      {lockedMessage ? (
        <div className="k-notice k-warning">
          <span>{lockedMessage}</span>
        </div>
      ) : null}

      <div className="k-composer" onDragOver={handleComposerDragOver} onDrop={handleComposerDrop}>
        {contexts.length > 0 ? (
          <div className="k-context-chip-list">
            {contexts.map((ctx) => (
              <span
                key={ctx.id}
                className={`k-context-chip ${contextChipClass(ctx)} ${selectedContextIdSet.has(ctx.id) ? 'is-selected' : ''}`}
                title={ctx.summary || ctx.label}
                tabIndex={0}
                aria-selected={selectedContextIdSet.has(ctx.id)}
                onClick={(event) => handleContextChipClick(ctx, event)}
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
                  <line x1="7" y1="7" x2="7.01" y2="7" />
                </svg>
                <span className="k-context-chip-text">
                  <strong>{ctx.label}</strong>
                  <em>{contextTypeLabel(ctx)}</em>
                </span>
                <button type="button" aria-label={`Remove ${ctx.label}`} onClick={() => onContextRemove(ctx.id)}>×</button>
              </span>
            ))}
            <button
              type="button"
              className="k-context-clear"
              onClick={() => removeContexts(selectedContextIds.length ? selectedContextIds : contexts.map((ctx) => ctx.id))}
            >
              {selectedContextIds.length ? `Remove ${selectedContextIds.length}` : 'Clear'}
            </button>
          </div>
        ) : null}

        {files.length > 0 ? (
          <div className="k-composer-attachments">
            {files.map((file, index) => (
              <span key={`${file.name}-${file.size}-${index}`} className="k-composer-attach-pill">
                <span>{file.name}</span>
                <span className="text-[var(--muted)]">{formatBytes(file.size)}</span>
                <button type="button" aria-label="Remove" onClick={() => onFileRemove(index)}>×</button>
              </span>
            ))}
          </div>
        ) : null}

        <textarea
          value={text}
          disabled={disabled}
          onChange={(event) => onTextChange(event.target.value)}
          onKeyDown={(event) => {
            /* Cmd+Enter (macOS) and Ctrl+Enter (Windows/Linux) submit
             * — matches the "⌘↵ to send" hint shown next to the send
             * button. Plain Enter inserts a newline, which is the
             * default textarea behaviour. */
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault()
              if (!disabled && (text.trim() || files.length > 0)) {
                onSubmit()
              }
            }
          }}
          placeholder={
            inProgress
              ? 'Send a follow-up while the current turn is still running.'
              : 'Ask anything — attachments, web search, code exec, and follow-ups are supported.'
          }
          rows={2}
        />

        <div className="k-composer-bar">
          <div className="left">
            <label className="k-iconbtn cursor-pointer" title="Attach files">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21.4 11.05 12.5 19.95a5 5 0 1 1-7-7l9-9a3.5 3.5 0 1 1 5 5l-9 9a2 2 0 1 1-3-3l8.5-8.5" />
              </svg>
              <input
                type="file"
                multiple
                className="hidden"
                disabled={disabled}
                onChange={(event) => onFilesAdd(event.target.files)}
              />
            </label>
            {inProgress ? (
              <button
                type="button"
                disabled={disabled}
                onClick={onStop}
                className="k-btn k-sm k-danger"
                title="Stop the current turn"
              >
                Stop
              </button>
            ) : null}
          </div>
          <div className="right">
            <span className="k-micro hidden sm:inline">⌘↵ to send</span>
            <button
              type="button"
              disabled={disabled || (!text.trim() && files.length === 0)}
              onClick={onSubmit}
              className="k-btn k-primary"
            >
              {inProgress ? 'Follow up' : 'Send'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export const Composer = memo(ComposerImpl)
