/** Per-turn tab dispatcher. Renders the user bubble, tab strip, and the
 *  body for whichever tab is active.
 *
 *  Memoised: the parent (`App`) re-creates the per-turn list on every
 *  Redux dispatch (composer keystroke, banner, conversation refresh,
 *  every streaming delta). With Immer's structural sharing, the `turn`
 *  reference for unchanged turns stays the same — so memoised TurnView
 *  short-circuits and only the actively-streaming turn re-renders.
 *  Other props (`sendingDisabled`, callbacks) must also be reference-
 *  stable; App.tsx wraps the callbacks with `useStableCallback`. */
import { memo, useMemo, useState } from 'react'
import { formatBytes, formatTime } from '../../components/utils.ts'
import { CopyButton } from '../../components/CopyButton.tsx'
import { MarkdownBlock } from '../../components/MarkdownBlock.tsx'
import { SuggestedQuestions } from '../../components/SuggestedQuestions.tsx'
import { AttachmentChip } from '../../components/AttachmentChip.tsx'
import type {
  CanvasArtifact,
  ChatTurn,
  FileArtifact,
  TurnTab,
} from './chatTypes.ts'
import type { TurnReaction } from '../../service.ts'
import {
  ArtifactFeed,
  CanvasPanel,
  DownloadsPanel,
  LinksPanel,
  MergedOverviewFeed,
  StepList,
  ThinkingBlock,
  TimelineFeed,
  collectTurnLinks,
  mergeOverviewEvents,
} from './turnTabs.tsx'
import { ChatTurnView } from './ChatTurnView.tsx'

/** Split a sent user message into its visible text and any trailing
 *  `{"context":[…]}` block the composer folds in for the assistant. The block
 *  is rendered as small chips instead of raw JSON so the bubble stays clean
 *  (and short). Placeholder until the turn payload carries context as a
 *  first-class field rather than appended text. */
function splitUserMessage(raw: string): { text: string; contexts: Array<{ id: string; label: string }> } {
  const match = raw.match(/^([\s\S]*?)\n\n(\{"context":\[[\s\S]*\]\})\s*$/)
  if (!match) return { text: raw, contexts: [] }
  try {
    const parsed = JSON.parse(match[2]) as { context?: Array<{ id?: string; label?: string }> }
    const contexts = (parsed.context || []).map((entry) => ({
      id: String(entry.id || ''),
      label: String(entry.label || entry.id || ''),
    }))
    return { text: match[1], contexts }
  } catch {
    return { text: raw, contexts: [] }
  }
}

/** Turn-level reaction control: thumbs up/down on a completed answer.
 *  Liking is instant; disliking expands an inline optional-comment box
 *  (Skip / Submit) — inline rather than a modal so it never clips when the
 *  chat is embedded as an iframe tile. Re-clicking the active thumb clears. */
function TurnFeedback({
  turnId,
  reaction,
  onFeedback,
}: {
  turnId: string
  reaction: TurnReaction | null
  onFeedback: (turnId: string, reaction: TurnReaction | null, text?: string) => void
}) {
  const [commentOpen, setCommentOpen] = useState(false)
  const [comment, setComment] = useState('')

  const likeActive = reaction === 'ok'
  const dislikeActive = reaction === 'not_ok' || commentOpen

  const handleLike = () => {
    setCommentOpen(false)
    setComment('')
    onFeedback(turnId, likeActive ? null : 'ok')
  }
  const handleDislike = () => {
    if (reaction === 'not_ok') {
      setCommentOpen(false)
      setComment('')
      onFeedback(turnId, null)
      return
    }
    /* Open the optional-comment box; don't POST until Submit/Skip. */
    setComment('')
    setCommentOpen(true)
  }
  const submitComment = () => {
    const text = comment.trim()
    onFeedback(turnId, 'not_ok', text || undefined)
    setCommentOpen(false)
  }
  const skipComment = () => {
    onFeedback(turnId, 'not_ok')
    setCommentOpen(false)
  }

  return (
    <div className="k-feedback">
      <span className="k-feedback-label">Was this helpful?</span>
      <button
        type="button"
        className={`k-iconbtn k-borderless ${likeActive ? 'k-iconbtn-active' : ''}`}
        aria-label="Helpful"
        aria-pressed={likeActive}
        title="Helpful"
        onClick={handleLike}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill={likeActive ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M7 10v11" />
          <path d="M18 21H6a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h2l3-7a2 2 0 0 1 2 2v4h4a2 2 0 0 1 2 2l-2 8a2 2 0 0 1-2 2z" />
        </svg>
      </button>
      <button
        type="button"
        className={`k-iconbtn k-borderless ${dislikeActive ? 'k-iconbtn-active' : ''}`}
        aria-label="Not helpful"
        aria-pressed={dislikeActive}
        title="Not helpful"
        onClick={handleDislike}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill={reaction === 'not_ok' ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M17 14V3" />
          <path d="M6 3h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-2l-3 7a2 2 0 0 1-2-2v-4H7a2 2 0 0 1-2-2l2-8a2 2 0 0 1 2-2z" />
        </svg>
      </button>
      {commentOpen ? (
        <div className="k-feedback-comment">
          <label className="k-feedback-comment-label">What went wrong? (optional)</label>
          <textarea
            className="k-feedback-textarea"
            rows={3}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Tell us what was off…"
            autoFocus
          />
          <div className="k-feedback-comment-actions">
            <button type="button" className="k-btn k-sm k-ghost" onClick={skipComment}>Skip</button>
            <button type="button" className="k-btn k-sm k-primary" onClick={submitComment}>Submit</button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function TurnViewImpl({
  turn,
  conversationId,
  sendingDisabled,
  reaction,
  onFeedback,
  onFollowup,
  onDownloadError,
}: {
  turn: ChatTurn
  conversationId?: string | null
  sendingDisabled: boolean
  reaction: TurnReaction | null
  onFeedback: (turnId: string, reaction: TurnReaction | null, text?: string) => void
  onFollowup: (text: string) => void
  onDownloadError: (text: string) => void
}) {
  const [activeTab, setActiveTab] = useState<TurnTab>('chat')
  const steps = useMemo(
    () => Object.values(turn.steps).sort((left, right) => left.timestamp - right.timestamp),
    [turn.steps],
  )
  const assistantFiles = useMemo(
    () => turn.artifacts.filter((artifact): artifact is FileArtifact => artifact.kind === 'file'),
    [turn.artifacts],
  )
  const turnLinks = useMemo(() => collectTurnLinks(turn.artifacts), [turn.artifacts])
  const thinkingEntries = useMemo(
    () => turn.timeline.filter((entry) => entry.kind === 'thinking'),
    [turn.timeline],
  )
  const canvases = useMemo(
    () => turn.artifacts.filter((artifact): artifact is CanvasArtifact => artifact.kind === 'canvas'),
    [turn.artifacts],
  )
  /* Overview shows artifacts AND follow-up user messages in real timestamp
     order so the user can see the conversation evolve. Thinking entries are
     consolidated separately into ThinkingBlock and never enter this list. */
  const overviewEvents = useMemo(
    () => mergeOverviewEvents(
      // The final answer renders as its own pinned block below; keep its
      // `final_answer:*` timeline artifacts out of the Overview feed.
      turn.artifacts.filter(
        (artifact) => !(artifact.kind === 'timeline' && /^final_answer:\d+$/.test(artifact.name)),
      ),
      turn.additionalUserMessages,
    ),
    [turn.artifacts, turn.additionalUserMessages],
  )
  /* All user-side attachments for this turn — main message + every
   * followup. Feeds the Files tab count badge and the DownloadsPanel
   * so users can re-download anything they sent, including attachments
   * that came in with followups. */
  const allUserAttachments = useMemo(
    () => [
      ...turn.userAttachments,
      ...turn.additionalUserMessages.flatMap((message) => message.attachments),
    ],
    [turn.userAttachments, turn.additionalUserMessages],
  )
  /* Visible user text + dropped-context chips, split from the sent message. */
  const userParsed = useMemo(() => splitUserMessage(turn.userMessage), [turn.userMessage])

  const stateChipClass =
    turn.state === 'error'
      ? 'k-chip k-pink'
      : turn.state === 'completed'
        ? 'k-chip k-green'
        : 'k-chip k-teal'

  /* Turn cost (from the accounting.usage event) and wall time (from
   * chat.turn.summary), shown in the status line once they arrive. Cost shows
   * more decimals when it is small; time as m:ss. */
  const costUsd = turn.costUsd
  const costLabel =
    typeof costUsd === 'number' && costUsd >= 0
      ? costUsd >= 0.1
        ? costUsd.toFixed(2)
        : costUsd.toFixed(4)
      : null
  const elapsedMs = turn.elapsedMs
  let timeLabel: string | null = null
  if (typeof elapsedMs === 'number' && elapsedMs >= 0) {
    const total = Math.round(elapsedMs / 1000)
    // min.sec (dot, not colon, which reads as a clock time)
    timeLabel = `${Math.floor(total / 60)}.${String(total % 60).padStart(2, '0')}`
  }

  /* Hide the user-bubble entirely when the turn has no text AND no
   * attachments — that's a phantom turn (e.g. a server-side spurious
   * `chat.start` for a queued continuation id) and showing a placeholder
   * "You" bubble would be misleading. */
  const hasUserContent = Boolean(turn.userMessage) || turn.userAttachments.length > 0

  return (
    <article className="flex flex-col gap-2">
      {/* User turn */}
      {hasUserContent ? (
        <div className="flex flex-col gap-1 self-end max-w-[760px]" data-turn-anchor={turn.id}>
          <div className="flex items-center gap-2 text-[11px] text-[var(--muted)]">
            <span className="font-semibold text-[var(--text-2)]">You</span>
            <span>{formatTime(turn.createdAt)}</span>
          </div>
          <div className="k-msg rounded-md border border-[var(--line)] bg-[var(--surface-2)] px-3 py-1.5 text-[13.5px] leading-[1.45] text-[var(--ink)]">
            {userParsed.text ? (
              <div className="whitespace-pre-wrap">{userParsed.text}</div>
            ) : null}
            {userParsed.contexts.length > 0 ? (
              <div className="flex flex-wrap gap-1.5 pt-1.5">
                {userParsed.contexts.map((ctx) => (
                  <span
                    key={ctx.id}
                    className="inline-flex items-center gap-1 rounded-full border border-[var(--purple)] bg-[var(--purple-pale)] px-2 py-0.5 text-[11px] font-semibold text-[var(--purple)]"
                  >
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
                      <line x1="7" y1="7" x2="7.01" y2="7" />
                    </svg>
                    {ctx.label}
                  </span>
                ))}
              </div>
            ) : null}
            {turn.userAttachments.length > 0 ? (
              <div className="flex flex-wrap gap-1.5 pt-1.5">
                {turn.userAttachments.map((attachment) => (
                  <AttachmentChip
                    key={attachment.id}
                    attachment={attachment}
                    conversationId={conversationId}
                    onError={onDownloadError}
                  />
                ))}
              </div>
            ) : null}
            {userParsed.text ? (
              <span className="k-msg-toolbar">
                <CopyButton value={userParsed.text} title="Copy message" />
              </span>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* Assistant turn */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-2 text-[11px] text-[var(--muted)]">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-[var(--text-2)]">Assistant</span>
            <span className={stateChipClass}>{turn.state}</span>
          </div>
        </div>

        <div className="k-tabsbar">
        <div className="k-tabs">
          {([
            /* Chat is the default visual; sits first per design. */
            ['chat', 'Chat', null],
            ['overview', 'Overview', null],
            ['timeline', 'Timeline', turn.timeline.length || null],
            ['steps', 'Steps', steps.length || null],
            ['canvases', 'Artifacts', canvases.length || null],
            ['links', 'Links', turnLinks.length || null],
            ['files', 'Files', (allUserAttachments.length + assistantFiles.length) || null],
          ] as Array<[TurnTab, string, number | null]>).map(([tab, label, count]) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`k-tab ${activeTab === tab ? 'k-active' : ''}`}
            >
              {label}
              {count ? <span className="k-count">{count}</span> : null}
            </button>
          ))}
        </div>
          {costLabel || timeLabel ? (
            <div className="k-turn-meta k-tabs-meta">
              {costLabel ? (
                <span
                  className="k-turn-cost"
                  title={costUsd != null ? `Turn cost: $${costUsd.toFixed(6)}` : undefined}
                >
                  <span className="k-coin" aria-hidden="true">$</span>
                  {costLabel}
                </span>
              ) : null}
              {timeLabel ? (
                <span className="k-turn-time" title="Turn time (min.sec)">t={timeLabel}</span>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="flex flex-col gap-2 pt-1">
          {activeTab === 'chat' ? (
            <ChatTurnView
              turn={turn}
              sendingDisabled={sendingDisabled}
              onFollowup={onFollowup}
              onDownloadError={onDownloadError}
            />
          ) : null}

          {activeTab === 'overview' ? (
            <>
              <ThinkingBlock entries={thinkingEntries} active={turn.state === 'pending' || turn.state === 'running'} />
              <MergedOverviewFeed events={overviewEvents} onDownloadError={onDownloadError} />
              {turn.answer ? (
                <div className="k-msg mt-1 rounded-md border border-[var(--line-soft)] bg-[var(--surface)] px-3 py-2">
                  <MarkdownBlock content={turn.answer} />
                  <span className="k-msg-toolbar">
                    <CopyButton value={turn.answer} title="Copy answer" />
                  </span>
                </div>
              ) : turn.state === 'error' ? (
                <div className="k-notice k-error">
                  <span>{turn.error || 'Request failed.'}</span>
                </div>
              ) : (
                <div className="flex items-center gap-2 text-[12px] text-[var(--muted)]">
                  <span className="k-status k-live" />
                  <span>Streaming response…</span>
                </div>
              )}
              <SuggestedQuestions items={turn.followups} disabled={sendingDisabled} onSelect={onFollowup} />
            </>
          ) : null}

          {activeTab === 'timeline' ? <TimelineFeed entries={turn.timeline} /> : null}
          {activeTab === 'steps' ? <StepList steps={steps} /> : null}
          {activeTab === 'canvases' ? <CanvasPanel canvases={canvases} /> : null}
          {activeTab === 'links' ? <LinksPanel links={turnLinks} /> : null}
          {activeTab === 'files' ? (
            <DownloadsPanel attachments={allUserAttachments} files={assistantFiles} conversationId={conversationId} onError={onDownloadError} />
          ) : null}
        </div>

        {/* Turn-level feedback — available once the answer is complete,
            regardless of which tab is open. */}
        {turn.state === 'completed' && turn.answer ? (
          <TurnFeedback turnId={turn.id} reaction={reaction} onFeedback={onFeedback} />
        ) : null}
      </div>
    </article>
  )
}

export const TurnView = memo(TurnViewImpl)
