/**
 * WebappPane — left-side surface that iframes a bundle widget.
 *
 * Currently used for `versatile_webapp` (memories). The pane has a
 * small header with controls:
 *   - "Back to chats" — flips left pane back to the conversations list.
 *   - "Expand" — opens a full-screen modal of the same widget.
 *   - "Hide" — collapses the entire left column.
 *
 * The widget is served same-origin by the platform, so we deliberately
 * do NOT set a sandbox attribute — the widget needs to use parent
 * cookies for authentication and call back into bundle APIs.
 *
 * `WebappModal` mirrors `CanvasModal`: portal-mounted, ESC / backdrop
 * close, locks body scroll while open.
 */

import { memo, useEffect } from 'react'
import { createPortal } from 'react-dom'

export interface WebappPaneProps {
  src: string
  title: string
  onBackToChats: () => void
  onExpand: () => void
  onCollapse: () => void
}

function WebappPaneImpl({ src, title, onBackToChats, onExpand, onCollapse }: WebappPaneProps) {
  return (
    <aside className="glass-panel flex min-h-[520px] flex-col overflow-hidden lg:sticky lg:top-4">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--line-soft)] px-3 py-2">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-[var(--ink)]">{title}</div>
          <div className="text-[11px] text-[var(--muted)]">Bundle widget</div>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="k-iconbtn"
            onClick={onBackToChats}
            aria-label="Back to chats"
            title="Back to chats"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </button>
          <button
            type="button"
            className="k-iconbtn"
            onClick={onExpand}
            aria-label="Expand"
            title="Expand"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
            </svg>
          </button>
          <button
            type="button"
            className="k-iconbtn"
            onClick={onCollapse}
            aria-label="Hide panel"
            title="Hide panel"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>
      <iframe
        className="k-webapp-frame"
        src={src}
        title={title}
        loading="lazy"
      />
    </aside>
  )
}

export const WebappPane = memo(WebappPaneImpl)

export function WebappModal({
  src,
  title,
  onClose,
}: {
  src: string
  title: string
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
        aria-label={title}
      >
        <div className="k-canvas-modal-head">
          <div className="k-canvas-modal-title">
            <span className="k-text">{title}</span>
            <span className="k-micro">bundle widget</span>
          </div>
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
          <iframe className="k-webapp-frame" src={src} title={title} />
        </div>
      </div>
    </div>,
    document.body,
  )
}
