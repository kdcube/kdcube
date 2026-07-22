/**
 * Loading / error / empty note for a load slot. Uses the shared `.k-notice`
 * banner vocabulary for the error tone; loading and empty stay quiet.
 */
import type { LoadStatus } from '@kdcube/components-core/apps-config';
import type { ReactNode } from 'react';

export interface StatusNoteProps {
  status: LoadStatus;
  error?: string | null;
  loadingLabel?: string;
  emptyLabel?: string;
  /** true when a `ready` slot has no rows to show. */
  empty?: boolean;
  children?: ReactNode;
}

/**
 * Renders the note for non-ready states (and empty ready states) and returns
 * `null` when there is real content to show — so callers can do:
 *   <StatusNote .../> ?? <RealContent/>
 * Simpler: render <StatusNote/> then guard content on status === 'ready' && !empty.
 */
export function StatusNote({
  status,
  error,
  loadingLabel = 'Loading…',
  emptyLabel = 'Nothing here.',
  empty,
}: StatusNoteProps): ReactNode {
  if (status === 'loading' || status === 'idle') {
    return <div className="ac-note ac-note--muted">{loadingLabel}</div>;
  }
  if (status === 'error') {
    return <div className="k-notice ac-note ac-note--error">{error || 'Failed to load.'}</div>;
  }
  if (empty) {
    return <div className="ac-note ac-note--muted">{emptyLabel}</div>;
  }
  return null;
}
