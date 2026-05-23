/**
 * FileDropZone — wraps a region of the page in a drag-and-drop file
 * receiver. Used by the chat area so the user can drop files anywhere
 * over the transcript or composer.
 *
 * Strategy: window-level drag listeners track whether files are being
 * dragged anywhere over the page. The overlay is rendered inside the
 * wrapper div, so it's visually scoped to the chat area even though the
 * listeners are global. Drops are consumed only when they land inside
 * the wrapper; anywhere else we still `preventDefault` to keep the
 * browser from navigating away to the file URL.
 *
 * Drag-counter pattern: `dragenter` / `dragleave` fire for child
 * elements too (any time the pointer crosses into a nested element).
 * We track a counter and only flip `active=false` when it returns to
 * zero, so the overlay doesn't flicker as the pointer moves over
 * nested elements inside the drop zone.
 */

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

export function FileDropZone({
  children,
  onFiles,
  disabled = false,
  className = '',
  message = 'Drop files to attach',
}: {
  children: ReactNode
  onFiles: (files: File[]) => void
  disabled?: boolean
  className?: string
  message?: string
}) {
  const [active, setActive] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (disabled) {
      /* If the composer is locked or the connection is booting, don't
       * activate the overlay at all. We still don't need to swallow
       * drops because if `disabled` was previously false the listeners
       * are removed here on cleanup. */
      setActive(false)
      return
    }
    let counter = 0
    const hasFiles = (event: DragEvent) =>
      !!event.dataTransfer && Array.from(event.dataTransfer.types || []).includes('Files')

    const dragenter = (event: DragEvent) => {
      if (!hasFiles(event)) return
      counter += 1
      setActive(true)
    }
    const dragover = (event: DragEvent) => {
      if (!hasFiles(event)) return
      /* Required to allow `drop` to fire AND to stop the browser from
       * its default "navigate to the dropped file" behaviour anywhere
       * on the page. */
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
    }
    const dragleave = (event: DragEvent) => {
      if (!hasFiles(event)) return
      counter -= 1
      if (counter <= 0) {
        counter = 0
        setActive(false)
      }
    }
    const drop = (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      counter = 0
      setActive(false)
      const dropped = event.dataTransfer?.files
      if (!dropped || dropped.length === 0) return
      /* Only consume the drop when the user actually released over our
       * container; releasing over the sidebar should not attach. */
      const container = containerRef.current
      if (container && event.target instanceof Node && container.contains(event.target)) {
        onFiles(Array.from(dropped))
      }
    }

    window.addEventListener('dragenter', dragenter)
    window.addEventListener('dragover', dragover)
    window.addEventListener('dragleave', dragleave)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragenter', dragenter)
      window.removeEventListener('dragover', dragover)
      window.removeEventListener('dragleave', dragleave)
      window.removeEventListener('drop', drop)
    }
  }, [disabled, onFiles])

  return (
    <div
      ref={containerRef}
      className={`k-dropzone ${active ? 'k-dropzone--active' : ''} ${className}`.trim()}
    >
      {children}
      {active ? (
        <div className="k-dropzone-overlay" aria-hidden="true">
          <div className="k-dropzone-card">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
            </svg>
            <span>{message}</span>
          </div>
        </div>
      ) : null}
    </div>
  )
}
