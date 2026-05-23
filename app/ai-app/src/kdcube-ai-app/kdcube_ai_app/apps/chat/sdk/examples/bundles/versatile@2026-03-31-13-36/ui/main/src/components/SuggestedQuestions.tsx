/** Inline followup chip row. Moved verbatim from App.tsx (Wave 2). */

export function SuggestedQuestions({
  items,
  disabled,
  onSelect,
}: {
  items: string[]
  disabled: boolean
  onSelect: (text: string) => void
}) {
  if (items.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5 pt-2">
      {items.map((item) => (
        <button
          key={item}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(item)}
          className="k-followup"
        >
          {item}
        </button>
      ))}
    </div>
  )
}
