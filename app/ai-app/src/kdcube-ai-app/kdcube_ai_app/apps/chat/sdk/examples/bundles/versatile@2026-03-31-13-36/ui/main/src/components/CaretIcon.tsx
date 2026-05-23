/**
 * Caret SVG used by every collapsible `<details>` summary in the chat UI.
 *
 * The rotation animation lives in `index.css` under `.k-workitem-caret`.
 * Moved verbatim from src/App.tsx (Wave 1).
 */

export function CaretIcon() {
  return (
    <svg className="k-workitem-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M9 6l6 6-6 6" />
    </svg>
  )
}
