/** Inline notice strip for transient banners. Memoised so an unchanged
 *  banners array + stable `onDismiss` skips re-render during streaming. */
import { memo } from 'react'
import type { BannerTone } from '@kdcube/components-core/chat'
import type { Banner, ConnectionsConsentOpen } from '@kdcube/components-core/chat'

function BannerStripImpl({
  banners,
  onDismiss,
  onOpenConnections,
  onAdjustTools,
}: {
  banners: Banner[]
  onDismiss: (id: string) => void
  /** Routes a consent card's action through the host's connections surface
   *  (the `connections.hub.open` scene contract) instead of the plain link.
   *  Passed only when the host registered an `open-connections` handler. */
  onOpenConnections?: (consent: ConnectionsConsentOpen) => void
  /** The consent banner's second real option: open the composer tools menu
   *  with the blocked tools highlighted so the user can turn them off
   *  instead of granting the access. */
  onAdjustTools?: (tools: string[]) => void
}) {
  if (banners.length === 0) return null
  const noticeClass = (tone: BannerTone) => {
    /* BannerTone is currently 'info' | 'warning' | 'error'. The dead
     * 'success' case from the pre-refactor App.tsx was removed because TS
     * flags it as unreachable. If the API ever starts emitting a success
     * tone, widen the union in service.ts and re-add the branch. */
    switch (tone) {
      case 'error':
        return 'k-notice k-error'
      case 'warning':
        return 'k-notice k-warning'
      default:
        return 'k-notice k-info'
    }
  }
  /* Layout contract (k-banner-strip is a size container): wide containers
   * keep the single row — text, actions, dismiss; narrow containers stack —
   * full-width text first, the action buttons on their own row, the dismiss
   * pinned top-right. The claims render as compact code chips after the
   * sentence, so the text wraps cleanly in both layouts. */
  const hasActions = (banner: Banner) =>
    Boolean((banner.consent && onOpenConnections) || banner.actionUrl || (banner.consentTools?.length && onAdjustTools))
  return (
    <div className="k-banner-strip flex flex-col gap-2">
      {banners.map((banner) => (
        <div key={banner.id} className={noticeClass(banner.tone)}>
          <div className="k-banner-body min-w-0 flex-1">
            <span>{banner.text}</span>
            {banner.consentClaims?.length ? (
              <span className="k-banner-claims">
                {banner.consentClaims.map((claim) => (
                  <code key={claim} className="k-banner-claim">{claim}</code>
                ))}
              </span>
            ) : null}
          </div>
          {hasActions(banner) ? (
            <div className="k-banner-actions">
              {banner.consent && onOpenConnections ? (
                <button
                  type="button"
                  className="k-btn k-ghost k-notice-action"
                  onClick={() => onOpenConnections(banner.consent as ConnectionsConsentOpen)}
                >
                  {banner.actionLabel || 'Open'}
                </button>
              ) : banner.actionUrl ? (
                <a
                  className="k-btn k-ghost k-notice-action"
                  href={banner.actionUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  {banner.actionLabel || 'Open'}
                </a>
              ) : null}
              {banner.consentTools?.length && onAdjustTools ? (
                <button
                  type="button"
                  className="k-btn k-ghost k-notice-action"
                  title="Open the tools menu with these tools highlighted"
                  onClick={() => onAdjustTools(banner.consentTools as string[])}
                >
                  Turn off the tools that need it
                </button>
              ) : null}
            </div>
          ) : null}
          <button
            type="button"
            className="k-iconbtn k-borderless k-banner-dismiss"
            onClick={() => onDismiss(banner.id)}
            aria-label="Dismiss"
            title="Dismiss"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      ))}
    </div>
  )
}

export const BannerStrip = memo(BannerStripImpl)
