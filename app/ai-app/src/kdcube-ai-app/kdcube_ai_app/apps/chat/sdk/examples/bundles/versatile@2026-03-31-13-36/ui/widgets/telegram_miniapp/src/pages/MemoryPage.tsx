import { useEffect, useMemo, useRef, useState } from 'react';
import { sendDataBusEcho, type DataBusEchoOutcome } from '../store/dataBusClient';
import { settings } from '../store/settings';
import { installConfigHandshakeHost } from '../auth/configHandshakeHost';
import type { MemoryPayload } from '../store/types';

// Memory was consolidated into the user-memories app; the Mini App loads it as
// a same-origin served-widget iframe (like the scene host does) instead of
// embedding the widget bundle directly. The host answers the iframe's standard
// CONFIG_REQUEST with a CONFIG_RESPONSE that — inside Telegram — also carries
// telegramInitData and authConnectionId, so the widget authenticates the same
// way it does under the scene/website hosts (cookies/Bearer there,
// X-Telegram-Init-Data + X-KDCube-Auth-Connection-ID here).
const MEMORY_WIDGET_BUNDLE_ID = 'user-memories@2026-06-26';
const MEMORY_WIDGET_ALIAS = 'memories';
const MEMORY_WIDGET_IDENTITY = 'MEMORIES_WIDGET';

interface MemoryPageProps {
  memory?: MemoryPayload;
  reload: () => Promise<void>;
}

export function MemoryPage({ memory, reload }: MemoryPageProps) {
  const count = Number(memory?.count || memory?.memories?.length || 0);
  const [echoStatus, setEchoStatus] = useState<'idle' | 'running' | 'ok' | 'accepted' | 'error'>('idle');
  const [echoMessage, setEchoMessage] = useState('');
  const frameRef = useRef<HTMLIFrameElement | null>(null);

  const memoryWidgetSrc = useMemo(
    () => settings.widgetUrlForBundle(MEMORY_WIDGET_BUNDLE_ID, MEMORY_WIDGET_ALIAS, { view: 'expanded' }),
    [],
  );

  // Answer the memory iframe's standard CONFIG_REQUEST. Inside Telegram the
  // CONFIG_RESPONSE config also carries telegramInitData and authConnectionId,
  // which the widget attaches as selector headers. A kdcube-auth-changed nudge
  // re-triggers the handshake if initData lands after the frame mounts.
  useEffect(
    () => installConfigHandshakeHost(frameRef.current, { identity: MEMORY_WIDGET_IDENTITY }),
    [memoryWidgetSrc],
  );

  async function runEchoProbe() {
    setEchoStatus('running');
    setEchoMessage('');
    try {
      const outcome: DataBusEchoOutcome = await sendDataBusEcho({
        memory_count: count,
        scope: memory?.scope || {},
        sent_at: new Date().toISOString(),
      });
      const ackStatus = String(outcome.ack.status || 'unknown');
      if (outcome.event) {
        setEchoStatus('ok');
        setEchoMessage(`Handled ${outcome.messageId} · ack=${ackStatus}`);
      } else {
        setEchoStatus('accepted');
        setEchoMessage(`Accepted ${outcome.messageId}; no handler reply arrived before timeout.`);
      }
    } catch (error) {
      setEchoStatus('error');
      setEchoMessage(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <section className="page page-wide memory-embed-page">
      <div className="page-header">
        <div>
          <h1>Memory</h1>
          <p>{count} durable notes · shared memory widget</p>
        </div>
        <div className="toolbar-actions">
          <button type="button" className="ghost-button" disabled={echoStatus === 'running'} onClick={() => void runEchoProbe()}>
            {echoStatus === 'running' ? 'Sending echo' : 'Data Bus echo'}
          </button>
          <button type="button" className="ghost-button" onClick={() => void reload()}>Refresh</button>
        </div>
      </div>
      {echoMessage && (
        <div className={`notice ${echoStatus === 'error' ? 'error' : 'success'}`}>{echoMessage}</div>
      )}
      {memory?.ok === false && (
        <div className="notice error">{memory.message || memory.error || 'Memory is unavailable.'}</div>
      )}
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
