/** Tiny icon-only copy button (clipboard with execCommand fallback). Ported from
 *  the in-tree widget; `./utils` rewired to the package UI lib. */
import { useState } from 'react'
import { copyToClipboard } from '../support/utils.ts'

export function CopyButton({ value, title = 'Copy' }: { value: string; title?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      className="k-tinybtn"
      title={title}
      data-flash={done ? 'true' : undefined}
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        void copyToClipboard(value).then(() => {
          setDone(true)
          window.setTimeout(() => setDone(false), 1200)
        }).catch((error) => {
          console.warn('[kdcube.chat] copy failed', error)
        })
      }}
    >
      {done ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
          <path d="M5 12l4 4 10-10" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="9" y="9" width="13" height="13" rx="2" />
          <path d="M5 15V5a2 2 0 0 1 2-2h10" />
        </svg>
      )}
    </button>
  )
}
