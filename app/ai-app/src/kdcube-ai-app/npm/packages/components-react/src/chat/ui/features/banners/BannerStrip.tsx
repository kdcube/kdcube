/** Inline notice strip for transient banners. Memoised so an unchanged
 *  banners array + stable `onDismiss` skips re-render during streaming. */
import { memo } from 'react'
import type { BannerTone } from '@kdcube/components-core/chat'
import type { Banner, ConnectionsConsentOpen } from '@kdcube/components-core/chat'

function BannerStripImpl({
  banners,
  onDismiss,
  onOpenConnections,
}: {
  banners: Banner[]
  onDismiss: (id: string) => void
  /** Routes a consent card's action through the host's connections surface
   *  (the `connections.hub.open` scene contract) instead of the plain link.
   *  Passed only when the host registered an `open-connections` handler. */
  onOpenConnections?: (consent: ConnectionsConsentOpen) => void
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
  return (
    <div className="flex flex-col gap-2">
      {banners.map((banner) => (
        <div key={banner.id} className={noticeClass(banner.tone)}>
          <div className="min-w-0 flex-1">{banner.text}</div>
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
          <button
            type="button"
            className="k-iconbtn k-borderless"
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
