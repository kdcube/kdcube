import { useState } from 'react';
import { MemoriesWidgetEmbed } from '@kdcube/memory-widget';
import { sendDataBusEcho, type DataBusEchoOutcome } from '../store/dataBusClient';
import type { MemoryPayload, TelegramWidgetCallOperation } from '../store/types';

interface MemoryPageProps {
  memory?: MemoryPayload;
  reload: () => Promise<void>;
  callOperation: TelegramWidgetCallOperation;
}

export function MemoryPage({ memory, reload, callOperation }: MemoryPageProps) {
  const count = Number(memory?.count || memory?.memories?.length || 0);
  const [echoStatus, setEchoStatus] = useState<'idle' | 'running' | 'ok' | 'accepted' | 'error'>('idle');
  const [echoMessage, setEchoMessage] = useState('');

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
      <div className="memory-widget-direct">
        <MemoriesWidgetEmbed callOperation={callOperation} />
      </div>
    </section>
  );
}
