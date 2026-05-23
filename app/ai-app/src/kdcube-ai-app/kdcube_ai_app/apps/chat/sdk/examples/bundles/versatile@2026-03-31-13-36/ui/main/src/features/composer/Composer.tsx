/** Send composer (textarea + attach + submit). Memoised — composer
 *  props (text / files / disabled / inProgress / lockedMessage) change
 *  often but the bulk of the chat transcript doesn't re-render when
 *  they do, since the transcript is rendered by sibling components. */
import { memo, useRef } from 'react'
import { formatBytes } from '../../components/utils.ts'

function ComposerImpl({
  text,
  files,
  disabled,
  inProgress,
  lockedMessage,
  onTextChange,
  onFilesAdd,
  onFileRemove,
  onSubmit,
  onStop,
}: {
  text: string
  files: File[]
  disabled: boolean
  inProgress: boolean
  lockedMessage: string | null
  onTextChange: (value: string) => void
  onFilesAdd: (files: FileList | null) => void
  onFileRemove: (index: number) => void
  onSubmit: () => void
  onStop: () => void
}) {
  return (
    <div className="flex flex-col gap-2">
      {lockedMessage ? (
        <div className="k-notice k-warning">
          <span>{lockedMessage}</span>
        </div>
      ) : null}

      <div className="k-composer">
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
