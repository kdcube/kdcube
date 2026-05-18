import type { ReactNode } from 'react';

interface AppShellProps {
  children: ReactNode;
  allowWrite: boolean;
  onCreate: () => void;
}

export function AppShell({ allowWrite, children, onCreate }: AppShellProps) {
  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <span className="eyebrow">User memory</span>
          <h1>Your Memory Notes</h1>
          <p>Durable guidance available to this application.</p>
        </div>
        {allowWrite ? <button type="button" className="primary-button" onClick={onCreate}>New Note</button> : null}
      </header>
      {children}
    </main>
  );
}
