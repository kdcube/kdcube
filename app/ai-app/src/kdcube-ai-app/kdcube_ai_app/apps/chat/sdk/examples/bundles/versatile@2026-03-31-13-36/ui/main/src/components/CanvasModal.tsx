/**
 * CanvasModal — full-window expand for a canvas artifact.
 *
 * Used by the Canvas-tab `CanvasPanel` and the Chat-tab
 * `ChatCanvasBlock`. Portals to `document.body` so it escapes any
 * `<details>` or transformed-ancestor stacking context. Closes on Esc,
 * backdrop click, or the close button. Reuses `CanvasRender` for the
 * body so the iframe sandbox / debounce behaviour stays consistent
 * with the inline view.
 */

import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { CanvasArtifact } from '../features/chat/chatTypes.ts'
import { CanvasRender, canvasFilename, canvasMime } from './CanvasRender.tsx'
import { CopyButton } from './CopyButton.tsx'
import { DownloadButton } from './DownloadButton.tsx'

export function CanvasModal({
  canvas,
  onClose,
}: {
  canvas: CanvasArtifact
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
    }
  }, [onClose])

  return createPortal(
    <div className="k-canvas-modal-backdrop" onClick={onClose}>
      <div
        className="k-canvas-modal"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={canvas.title || canvas.name}
      >
        <div className="k-canvas-modal-head">
          <div className="k-canvas-modal-title">
            <span className="k-text">{canvas.title || canvas.name}</span>
            <span className="k-micro">{canvas.format || 'text'}</span>
          </div>
          <span className="k-snippet-tools">
            <CopyButton value={canvas.content} title="Copy canvas" />
            <DownloadButton
              data={canvas.content}
              filename={canvasFilename(canvas)}
              mime={canvasMime(canvas)}
              title="Download canvas"
            />
          </span>
          <button
            type="button"
            className="k-iconbtn"
            onClick={onClose}
            aria-label="Close (Esc)"
            title="Close (Esc)"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="k-canvas-modal-body">
          <CanvasRender canvas={canvas} />
        </div>
      </div>
    </div>,
    document.body,
  )
}

/** Small `Expand` icon button — sized to fit beside the existing
 *  copy / download row in canvas headers. Calling `onClick` should
 *  open a `<CanvasModal>` for the same artifact. */
export function CanvasExpandButton({ onClick, title = 'Expand' }: { onClick: () => void; title?: string }) {
  return (
    <button
      type="button"
      className="k-tinybtn"
      title={title}
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        onClick()
      }}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
      </svg>
    </button>
  )
}
