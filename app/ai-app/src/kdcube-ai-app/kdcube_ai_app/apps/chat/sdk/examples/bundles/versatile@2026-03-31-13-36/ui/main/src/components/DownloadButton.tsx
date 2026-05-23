/**
 * Tiny icon-only download button — programmatic anchor + Blob URL.
 *
 * Used by Snippet for in-place code/markdown downloads; the canvas/file
 * surfaces use the parent-managed downloads instead (so we don't synthesise
 * arbitrary blobs server-side data should resolve through).
 *
 * Moved verbatim from src/App.tsx (Wave 1).
 */

export function DownloadButton({
  data,
  filename,
  mime = 'text/plain',
  title = 'Download',
}: {
  data: string
  filename: string
  mime?: string
  title?: string
}) {
  return (
    <button
      type="button"
      className="k-tinybtn"
      title={title}
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        try {
          const blob = new Blob([data], { type: mime })
          const url = URL.createObjectURL(blob)
          const anchor = document.createElement('a')
          anchor.href = url
          anchor.download = filename
          anchor.style.display = 'none'
          document.body.appendChild(anchor)
          anchor.click()
          window.setTimeout(() => {
            URL.revokeObjectURL(url)
            document.body.removeChild(anchor)
          }, 0)
        } catch (error) {
          console.warn('Download failed', error)
        }
      }}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
      </svg>
    </button>
  )
}
