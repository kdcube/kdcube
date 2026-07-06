import { useMemo, useRef, useEffect } from 'react';
import { installConfigHandshakeHost } from '../auth/configHandshakeHost';
import { settings } from '../store/settings';

const CONNECTION_HUB_BUNDLE_ID = 'connection-hub@1-0';
const CONNECTIONS_WIDGET_ALIAS = 'connections_settings';
const CONNECTIONS_WIDGET_IDENTITY = 'CONNECTIONS_WIDGET';

export function ConnectionsPage() {
  const frameRef = useRef<HTMLIFrameElement | null>(null);

  const src = useMemo(
    () => settings.widgetUrlForBundle(CONNECTION_HUB_BUNDLE_ID, CONNECTIONS_WIDGET_ALIAS, {
      mode: 'telegram-miniapp',
      surface: 'telegram_miniapp',
      host: 'versatile',
    }),
    [],
  );

  useEffect(
    () => installConfigHandshakeHost(frameRef.current, {
      identity: CONNECTIONS_WIDGET_IDENTITY,
      bundleId: CONNECTION_HUB_BUNDLE_ID,
    }),
    [src],
  );

  return (
    <section className="page page-wide connections-embed-page">
      <div className="connection-widget-frame">
        <iframe
          ref={frameRef}
          src={src}
          title="Connections"
          className="connection-widget-iframe"
        />
      </div>
    </section>
  );
}
