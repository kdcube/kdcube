import { MemoriesWidgetEmbed } from '@kdcube/memory-widget';
import type { MemoryPayload, TelegramWidgetCallOperation } from '../store/types';

interface MemoryPageProps {
  memory?: MemoryPayload;
  reload: () => Promise<void>;
  callOperation: TelegramWidgetCallOperation;
}

export function MemoryPage({ memory, reload, callOperation }: MemoryPageProps) {
  const count = Number(memory?.count || memory?.memories?.length || 0);
  return (
    <section className="page page-wide memory-page">
      <div className="page-header">
        <div>
          <h1>Memory</h1>
          <p>{count} records in the current memory view</p>
        </div>
        <button type="button" className="ghost-button" onClick={() => void reload()}>Refresh</button>
      </div>
      {memory?.ok === false && (
        <div className="notice error">{memory.message || memory.error || 'Memory is unavailable.'}</div>
      )}
      <div className="memory-widget-direct">
        <MemoriesWidgetEmbed callOperation={callOperation} />
      </div>
    </section>
  );
}
