/** Left-hand chat list.
 *
 *  Each row is the conversation summary as a `<button>` (load on click). On
 *  hover or focus the row reveals a trash icon that calls `onDelete` —
 *  parents are responsible for confirming with the user before invoking
 *  the irreversible backend delete. Search and refresh live in the header
 *  bar.
 */

import { memo } from 'react'
import type { ConversationSummary } from '../../service.ts'
import { formatConversationTime } from '../../components/utils.ts'

function ConversationsSidebarImpl({
  conversations,
  query,
  activeConversationId,
  disabled,
  loading,
  error,
  loadingConversationId,
  deletingConversationId,
  onQueryChange,
  onRefresh,
  onSelect,
  onStartNew,
  onDelete,
}: {
  conversations: ConversationSummary[]
  query: string
  activeConversationId: string | null
  disabled: boolean
  loading: boolean
  error: string | null
  loadingConversationId: string | null
  deletingConversationId: string | null
  onQueryChange: (value: string) => void
  onRefresh: () => void
  onSelect: (conversationId: string) => void
  onStartNew: () => void
  onDelete: (conversation: ConversationSummary) => void
}) {
  return (
    <aside className="glass-panel flex min-h-[520px] flex-col overflow-hidden lg:sticky lg:top-4">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--line-soft)] px-3 py-2">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-[var(--ink)]">Chats</div>
          <div className="text-[11px] text-[var(--muted)]">Bundle conversations</div>
        </div>
        <button
          type="button"
          onClick={onStartNew}
          disabled={disabled}
          className="k-iconbtn"
          aria-label="New chat"
          title="New chat"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </div>

      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--line-soft)]">
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search chats"
          disabled={disabled}
          className="k-input"
        />
        <button
          type="button"
          onClick={onRefresh}
          className="k-iconbtn"
          aria-label="Refresh"
          title="Refresh"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 12a9 9 0 1 1-3-6.7" />
            <path d="M21 3v6h-6" />
          </svg>
        </button>
      </div>

      {error ? (
        <div className="px-3 pt-3">
          <div className="k-notice k-error">
            <span>{error}</span>
          </div>
        </div>
      ) : null}

      {loading && conversations.length === 0 ? (
        <p className="px-3 py-3 text-[12px] text-[var(--muted)]">Loading conversations…</p>
      ) : null}

      {!loading && conversations.length === 0 ? (
        <p className="px-3 py-3 text-[12px] leading-5 text-[var(--muted)]">
          {query.trim()
            ? 'No chats match the current search.'
            : 'No saved chats yet. Start a new one and it will appear here.'}
        </p>
      ) : null}

      {conversations.length > 0 ? (
        <div className="k-rows min-w-0 flex-1 overflow-auto">
          {conversations.map((conversation) => {
            const isActive = conversation.id === activeConversationId
            const isLoading = loadingConversationId === conversation.id
            const isDeleting = deletingConversationId === conversation.id
            /* The row uses position:relative; the delete affordance is
               absolutely positioned at the right edge so the underlying
               <button> stays the click target for the whole row.
               We render a sibling button rather than nesting one inside
               the row <button> (invalid HTML). */
            return (
              <div key={conversation.id} className="k-row-shell">
                <button
                  type="button"
                  onClick={() => onSelect(conversation.id)}
                  disabled={disabled || isLoading || isDeleting}
                  className={`k-row ${isActive ? 'k-active' : ''}`}
                >
                  <div className="k-row-main">
                    <div className="k-row-title">
                      {conversation.title || 'Untitled conversation'}
                    </div>
                    <div className="k-row-sub">
                      {formatConversationTime(conversation.lastActivityAt || conversation.startedAt)}
                      {isLoading ? ' · loading…' : ''}
                      {isDeleting ? ' · deleting…' : ''}
                    </div>
                  </div>
                  {isActive ? <span className="k-chip k-teal">open</span> : null}
                </button>
                <button
                  type="button"
                  className="k-row-delete"
                  aria-label="Delete conversation"
                  title="Delete conversation"
                  disabled={disabled || isLoading || isDeleting}
                  onClick={(event) => {
                    event.preventDefault()
                    event.stopPropagation()
                    onDelete(conversation)
                  }}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6" />
                  </svg>
                </button>
              </div>
            )
          })}
        </div>
      ) : null}
    </aside>
  )
}

export const ConversationsSidebar = memo(ConversationsSidebarImpl)
