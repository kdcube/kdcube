import { useEffect, useMemo, useRef } from 'react';
import { settings } from '../store/settings';
import { installConfigHandshakeHost } from '../auth/configHandshakeHost';
import type { MemoryPayload } from '../store/types';

// Memory lives in the user-memories app. The Mini App loads it as a same-origin
// served-widget iframe (like the scene host does) — the whole tab IS the widget.
// The host answers the iframe's standard CONFIG_REQUEST with a CONFIG_RESPONSE
// that carries the host-owned auth proof (telegramInitData); the widget promotes
// it onto its own requests without knowing Telegram. No host-side memory chrome:
// the count/summary, data-bus probe, and maintenance controls all live inside
// the widget itself.
const MEMORY_WIDGET_BUNDLE_ID = 'user-memories@2026-06-26';
const MEMORY_WIDGET_ALIAS = 'memories';
const MEMORY_WIDGET_IDENTITY = 'MEMORIES_WIDGET';

interface MemoryPageProps {
  memory?: MemoryPayload;
  reload?: () => Promise<void>;
}

export function MemoryPage(_props: MemoryPageProps) {
  const frameRef = useRef<HTMLIFrameElement | null>(null);

  const memoryWidgetSrc = useMemo(
    () => settings.widgetUrlForBundle(MEMORY_WIDGET_BUNDLE_ID, MEMORY_WIDGET_ALIAS, { view: 'expanded' }),
    [],
  );

  // Answer the memory iframe's standard CONFIG_REQUEST. Inside Telegram the
  // CONFIG_RESPONSE config also carries the host-owned authContext (telegram
  // initData). A kdcube-auth-changed nudge re-triggers the handshake if initData
  // lands after the frame mounts.
  useEffect(
    () => installConfigHandshakeHost(frameRef.current, { identity: MEMORY_WIDGET_IDENTITY }),
    [memoryWidgetSrc],
  );

  return (
    <section className="page page-wide memory-embed-page">
      <div className="memory-widget-frame">
        <iframe
          ref={frameRef}
          src={memoryWidgetSrc}
          title="Memories"
          className="memory-widget-iframe"
        />
      </div>
    </section>
  );
}
