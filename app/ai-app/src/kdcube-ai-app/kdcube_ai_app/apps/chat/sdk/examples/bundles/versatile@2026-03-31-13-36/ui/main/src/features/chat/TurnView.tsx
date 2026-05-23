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

function TurnViewImpl({
  turn,
  sendingDisabled,
  onFollowup,
  onDownloadError,
}: {
  turn: ChatTurn
  sendingDisabled: boolean
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
    () => mergeOverviewEvents(turn.artifacts, turn.additionalUserMessages),
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

  const stateChipClass =
    turn.state === 'error'
      ? 'k-chip k-pink'
      : turn.state === 'completed'
        ? 'k-chip k-green'
        : 'k-chip k-teal'

  /* Hide the user-bubble entirely when the turn has no text AND no
   * attachments — that's a phantom turn (e.g. a server-side spurious
   * `chat.start` for a queued continuation id) and showing a placeholder
   * "You" bubble would be misleading. */
  const hasUserContent = Boolean(turn.userMessage) || turn.userAttachments.length > 0

  return (
    <article className="flex flex-col gap-3">
      {/* User turn */}
      {hasUserContent ? (
        <div className="flex flex-col gap-1 self-end max-w-[760px]">
          <div className="flex items-center gap-2 text-[11px] text-[var(--muted)]">
            <span className="font-semibold text-[var(--text-2)]">You</span>
            <span>{formatTime(turn.createdAt)}</span>
          </div>
          <div className="k-msg rounded-md border border-[var(--line-soft)] bg-[var(--surface-2)] px-3 py-2 text-[14px] leading-6">
            {turn.userMessage ? (
              <div className="whitespace-pre-wrap">{turn.userMessage}</div>
            ) : null}
            {turn.userAttachments.length > 0 ? (
              <div className="flex flex-wrap gap-1.5 pt-1.5">
                {turn.userAttachments.map((attachment) => (
                  <AttachmentChip
                    key={attachment.id}
                    attachment={attachment}
                    onError={onDownloadError}
                  />
                ))}
              </div>
            ) : null}
            {turn.userMessage ? (
              <span className="k-msg-toolbar">
                <CopyButton value={turn.userMessage} title="Copy message" />
              </span>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* Assistant turn */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2 text-[11px] text-[var(--muted)]">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-[var(--text-2)]">Assistant</span>
            <span className={stateChipClass}>{turn.state}</span>
          </div>
        </div>

        <div className="k-tabs">
          {([
            /* Chat is the default visual; sits first per design. */
            ['chat', 'Chat', null],
            ['overview', 'Overview', null],
            ['timeline', 'Timeline', turn.timeline.length || null],
            ['steps', 'Steps', steps.length || null],
            ['canvases', 'Canvas', canvases.length || null],
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
            <DownloadsPanel attachments={allUserAttachments} files={assistantFiles} onError={onDownloadError} />
          ) : null}
        </div>
      </div>
    </article>
  )
}

export const TurnView = memo(TurnViewImpl)
