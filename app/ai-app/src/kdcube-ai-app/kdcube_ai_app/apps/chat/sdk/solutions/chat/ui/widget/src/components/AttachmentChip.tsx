/**
 * AttachmentChip — clickable chip showing a user-message attachment.
 *
 * Used inside the main user-message bubble and the followup pill. Click
 * triggers a download via the appropriate transport for the attachment:
 *
 *   - `attachment.file`       (live send, before upload completes) →
 *                             `downloadBlobAsFile`.
 *   - `attachment.logicalPath` (hosted object) →
 *                             resolver-backed `downloadObjectRef`.
 *
 * Errors surface through the parent's `onError` handler (typically the
 * banner-push hook) so the failure is visible.
 */

import { useState } from 'react'
import {
  downloadBlobAsFile,
  downloadObjectRef,
} from '../service.ts'
import type { TurnAttachment } from '../features/chat/chatTypes.ts'
import { canonicalObjectRef, setChatFileDragData } from '../features/chat/fileDrag.ts'
import { durableHistoricalObjectRef } from '../features/chat/historicalRefs.ts'
import { formatBytes, messageForError } from './utils.ts'

export function AttachmentChip({
  attachment,
  conversationId,
  onError,
}: {
  attachment: TurnAttachment
  conversationId?: string | null
  onError?: (text: string) => void
}) {
  const [downloading, setDownloading] = useState(false)
  // A user attachment's logical_path is often the non-durable `fi:turn_…`
  // form; promote it to the durable `fi:conv_<id>.turn_…` ref the canvas
  // accepts (the same normalization assistant files already get), so the
  // chip becomes draggable/pinnable like a bot-produced file.
  const dragRef = canonicalObjectRef(durableHistoricalObjectRef(attachment.logicalPath, conversationId ?? undefined))
  const canDownload = Boolean(attachment.file || dragRef)
  const handleClick = async (event: React.MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()
    if (!canDownload || downloading) return
    try {
      setDownloading(true)
      if (attachment.file) {
        downloadBlobAsFile(attachment.file, attachment.name)
        return
      }
      if (dragRef) {
        await downloadObjectRef(dragRef, attachment.name, attachment.mime)
        return
      }
    } catch (error) {
      onError?.(messageForError(error))
    } finally {
      setDownloading(false)
    }
  }
  return (
    <button
      type="button"
      onClick={(event) => void handleClick(event)}
      draggable={Boolean(dragRef)}
      onDragStart={(event) => {
        if (!dragRef) return
        setChatFileDragData(event.dataTransfer, {
          ref: dragRef,
          filename: attachment.name,
          mime: attachment.mime,
          preview: attachment.description,
          sourceKind: 'user.attachment',
        })
      }}
      disabled={(!canDownload && !dragRef) || downloading}
      className="k-attach-chip"
      title={
        dragRef
          ? (canDownload ? `Download ${attachment.name}; drag to attach or pin` : `Drag ${attachment.name} to attach or pin`)
          : (canDownload ? `Download ${attachment.name}` : attachment.name)
      }
    >
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M21.4 11.05 12.5 19.95a5 5 0 1 1-7-7l9-9a3.5 3.5 0 1 1 5 5l-9 9a2 2 0 1 1-3-3l8.5-8.5" />
      </svg>
      <span className="k-attach-chip-name">{attachment.name}</span>
      {typeof attachment.size === 'number' ? (
        <span className="k-attach-chip-size">{formatBytes(attachment.size)}</span>
      ) : null}
      {downloading ? <span className="k-attach-chip-state">…</span> : null}
    </button>
  )
}
