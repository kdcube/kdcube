import { useEffect, useRef, useState } from 'react';
import { assertOk, callOperation, downloadBase64, memoryEntries } from '../store/apiClient';
import type { ExportPayload, MemoryPayload } from '../store/types';
import { fileToBase64, fmt } from './pageUtils';

interface MemoryPageProps {
  memory?: MemoryPayload;
  reload: () => Promise<void>;
}

export function MemoryPage({ memory, reload }: MemoryPageProps) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [documentText, setDocumentText] = useState(memory?.document_text || '{}\n');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');

  useEffect(() => {
    setDocumentText(memory?.document_text || '{}\n');
  }, [memory?.document_text]);

  const entries = memoryEntries(memory);

  async function save() {
    setBusy(true);
    setError('');
    setStatus('');
    try {
      const result = await callOperation<MemoryPayload>('preferences_canvas_save', { document_text: documentText });
      assertOk(result, 'Save failed');
      setStatus('Saved');
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function exportExcel() {
    setBusy(true);
    setError('');
    try {
      const result = await callOperation<ExportPayload>('preferences_canvas_export_excel', {});
      assertOk(result, 'Export failed');
      downloadBase64(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function importExcel(file: File) {
    setBusy(true);
    setError('');
    try {
      const content = await fileToBase64(file);
      const result = await callOperation<MemoryPayload>('preferences_canvas_import_excel', { content_b64: content });
      assertOk(result, 'Import failed');
      setStatus('Imported');
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page page-wide">
      <div className="page-header">
        <div>
          <h1>Memory</h1>
          <p>{fmt(memory?.user_id)} · {entries.length} entries</p>
        </div>
        <div className="toolbar-actions">
          <button type="button" className="ghost-button" onClick={reload} disabled={busy}>Refresh</button>
          <button type="button" className="ghost-button" onClick={exportExcel} disabled={busy}>Export</button>
          <button type="button" className="ghost-button" onClick={() => fileRef.current?.click()} disabled={busy}>Import</button>
          <button type="button" className="primary-button" onClick={save} disabled={busy}>Save</button>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void importExcel(file);
              event.currentTarget.value = '';
            }}
          />
        </div>
      </div>
      {error && <div className="notice error">{error}</div>}
      {status && <div className="notice success">{status}</div>}
      <textarea
        className="memory-editor"
        value={documentText}
        onChange={(event) => setDocumentText(event.target.value)}
        spellCheck={false}
      />
      <div className="content-card list-card">
        {entries.map((entry, index) => (
          <article className="list-row memory-row" key={`${entry.key || 'entry'}-${index}`}>
            <div className="row-main">
              <strong>{entry.key || '(unnamed)'}</strong>
              <span>{String(entry.value ?? '')}</span>
            </div>
            <small>{fmt(entry.origin || entry.updated_at || entry.captured_at)}</small>
          </article>
        ))}
        {entries.length === 0 && <div className="empty-state">No memory entries.</div>}
      </div>
    </section>
  );
}
