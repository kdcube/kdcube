import type { ReactNode } from 'react';

interface AppShellProps {
  children: ReactNode;
  allowWrite: boolean;
  count: number;
  memoryUseEnabled: boolean;
  onCreate: () => void;
  onToggleMemoryUse: () => void;
  saving?: boolean;
}

export function AppShell({
  allowWrite,
  children,
  count,
  memoryUseEnabled,
  onCreate,
  onToggleMemoryUse,
  saving = false,
}: AppShellProps) {
  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <h1>Memory notes</h1>
          <p>{count} records in scope</p>
        </div>
        <label className="memory-use-toggle">
          <input
            type="checkbox"
            checked={memoryUseEnabled}
            onChange={onToggleMemoryUse}
            disabled={saving}
          />
          <span>Use my memory</span>
        </label>
        {allowWrite ? (
          <button type="button" className="primary-button" onClick={onCreate} disabled={!memoryUseEnabled}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginRight: 6 }}>
              <path d="M12 5v14M5 12h14" />
            </svg>
            New note
          </button>
        ) : null}
      </header>
      {children}
    </main>
  );
}
