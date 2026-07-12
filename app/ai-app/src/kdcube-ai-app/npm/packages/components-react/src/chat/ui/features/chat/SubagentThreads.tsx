/** Subagent threads — collapsible sub-conversations anchored under the parent
 *  turn they forked from.
 *
 *  LIVE: stamped child emissions accumulate in `state.threads` (the engine
 *  multiplexes on the `subagent` envelope stamp); the thread streams here with
 *  the SAME delta/step/event pipeline as the main lane, drawn nested.
 *  RELOAD: a fetched parent turn's `forks` descriptors arrive as collapsed
 *  stubs; expanding one fetches the child conversation through the same
 *  conversation-fetch path (`loadSubagentThread`) and renders its turns —
 *  live and reload produce the same visual result.
 *
 *  Layout is INLINE in the message flow: the thread block owns no reserved
 *  column or width — a left teal rail marks the nesting, everything else is
 *  normal chat content. Collapsed: charter goal + live status + contribution
 *  milestones. Expanded: the full child stream (ChatTurnView per child turn).
 */
import { memo, useState } from 'react'
import type { NamespaceStyleMap, SubagentThread } from '@kdcube/components-core/chat'
import { CaretIcon } from '../../components/CaretIcon.tsx'
import { ChatTurnView, shortenForPreview } from './ChatTurnView.tsx'
import { formatTime } from '../../support/utils.ts'

function BranchIcon() {
  return (
    <svg
      className="k-subthread-icon"
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="6" cy="5" r="2.4" />
      <circle cx="18" cy="12" r="2.4" />
      <circle cx="6" cy="19" r="2.4" />
      <path d="M6 7.4v9.2M6 12h5a4 4 0 0 0 4-3.6M6 12h5a4 4 0 0 1 4 3.6" />
    </svg>
  )
}

/** Status chip in user words. `unknown` (a reloaded stub whose completion the
 *  stored parent hasn't surfaced) renders nothing rather than a guess. */
function ThreadStatusChip({ thread }: { thread: SubagentThread }) {
  switch (thread.status) {
    case 'running':
      return <span className="k-chip k-teal">running</span>
    case 'converged':
      return <span className="k-chip k-green">done</span>
    case 'failed':
      return <span className="k-chip k-pink" title={thread.statusDetail || undefined}>failed</span>
    default:
      return null
  }
}

/** One contribution milestone line (`react.contribute` report). */
function MilestoneLine({ text, timestamp }: { text: string; timestamp: number }) {
  return (
    <div className="k-subthread-milestone">
      <span className="k-subthread-milestone-dot" aria-hidden="true" />
      <span className="k-subthread-milestone-text" title={text}>
        {shortenForPreview(text, 160) || 'Sent results back'}
      </span>
      <span className="k-subthread-milestone-time">{formatTime(timestamp)}</span>
    </div>
  )
}

function SubagentThreadViewImpl({
  thread,
  loadThread,
  onDownloadError,
  namespaceStyles = {},
}: {
  thread: SubagentThread
  loadThread: (childConversationId: string) => void
  onDownloadError: (text: string) => void
  namespaceStyles?: NamespaceStyleMap
}) {
  const [open, setOpen] = useState(false)
  const needsFetch = thread.hydration === 'stub' || thread.hydration === 'error'
  const toggleOpen = () => {
    setOpen((value) => {
      const next = !value
      if (next && needsFetch) loadThread(thread.childConversationId)
      return next
    })
  }
  const milestones = thread.contributions
  const goal = thread.charterGoal || 'Delegated task'
  /* The persona name the delegating agent chose; the thread header and the
   * continuation-turn persona show the SAME name. */
  const personaName = thread.agentTitle || 'Helper agent'
  return (
    <section className="k-subthread" data-thread-anchor={thread.childConversationId}>
      <button
        type="button"
        className="k-subthread-head"
        aria-expanded={open}
        onClick={toggleOpen}
        title={goal}
      >
        <BranchIcon />
        <span className="k-subthread-title">
          <span className="k-subthread-kicker">{personaName}</span>
          <span className="k-subthread-goal">{goal}</span>
        </span>
        <span className="k-subthread-meta">
          <ThreadStatusChip thread={thread} />
          {milestones.length ? (
            <span className="k-subthread-count">
              {milestones.length} update{milestones.length === 1 ? '' : 's'}
            </span>
          ) : null}
        </span>
        <CaretIcon />
      </button>
      {!open && milestones.length ? (
        <div className="k-subthread-milestones">
          {milestones.slice(-2).map((item) => (
            <MilestoneLine key={item.id} text={item.text} timestamp={item.timestamp} />
          ))}
        </div>
      ) : null}
      {open ? (
        <div className="k-subthread-body">
          {milestones.length ? (
            <div className="k-subthread-milestones">
              {milestones.map((item) => (
                <MilestoneLine key={item.id} text={item.text} timestamp={item.timestamp} />
              ))}
            </div>
          ) : null}
          {thread.hydration === 'loading' || thread.hydration === 'stub' ? (
            <div className="k-subthread-status">
              <span className="k-status k-live" aria-hidden="true" />
              <span>Opening the helper&rsquo;s conversation…</span>
            </div>
          ) : null}
          {thread.hydration === 'error' ? (
            <div className="k-notice k-error">
              <span>{thread.hydrationError || 'The helper’s conversation couldn’t be loaded.'}</span>
              <button
                type="button"
                className="k-btn k-sm"
                onClick={() => loadThread(thread.childConversationId)}
              >
                Retry
              </button>
            </div>
          ) : null}
          {thread.turns.map((turn) => (
            <div key={turn.id} className="k-subthread-turn" data-child-turn={turn.id}>
              <ChatTurnView
                turn={turn}
                sendingDisabled
                onFollowup={() => {}}
                onDownloadError={onDownloadError}
                namespaceStyles={namespaceStyles}
              />
            </div>
          ))}
          {thread.hydration === 'live' && thread.turns.length === 0 ? (
            <div className="k-subthread-status">
              <span className="k-status k-live" aria-hidden="true" />
              <span>The helper is starting up…</span>
            </div>
          ) : null}
          {thread.status === 'failed' && thread.statusDetail ? (
            <div className="k-notice k-error">
              <span>{thread.statusDetail}</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}

const SubagentThreadView = memo(SubagentThreadViewImpl)

function SubagentThreadsImpl({
  threads,
  loadThread,
  onDownloadError,
  namespaceStyles = {},
}: {
  /** Threads anchored at ONE parent turn, in delegate (fork) order. */
  threads: SubagentThread[]
  loadThread: (childConversationId: string) => void
  onDownloadError: (text: string) => void
  namespaceStyles?: NamespaceStyleMap
}) {
  if (!threads.length) return null
  return (
    <div className="k-subthreads">
      {threads.map((thread) => (
        <SubagentThreadView
          key={thread.childConversationId}
          thread={thread}
          loadThread={loadThread}
          onDownloadError={onDownloadError}
          namespaceStyles={namespaceStyles}
        />
      ))}
    </div>
  )
}

export const SubagentThreads = memo(SubagentThreadsImpl)
