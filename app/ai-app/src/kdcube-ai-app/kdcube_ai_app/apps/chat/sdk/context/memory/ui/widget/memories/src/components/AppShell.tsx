import type { DragEventHandler, ReactNode } from 'react';

interface AppShellProps {
  children: ReactNode;
  allowWrite: boolean;
  count: number;
  memoryUseEnabled: boolean;
  onCreate: () => void;
  onExpand?: () => void;
  onToggleMemoryUse: () => void;
  onDragLeave?: DragEventHandler<HTMLElement>;
  onDragOver?: DragEventHandler<HTMLElement>;
  onDrop?: DragEventHandler<HTMLElement>;
  compact?: boolean;
  dropActive?: boolean;
  hostControls?: boolean;
  saving?: boolean;
}

function notifyHostWidgetFocus(): void {
  try {
    if (typeof window === 'undefined' || window.parent === window) return;
    window.parent.postMessage({ type: 'kdcube-widget-focus', widget: 'memories' }, '*');
  } catch {
    // Focus promotion is a host-scene affordance only.
  }
}

export function AppShell({
  allowWrite,
  children,
  count,
  memoryUseEnabled,
  onCreate,
  onDragLeave,
  onDragOver,
  onDrop,
  onExpand,
  onToggleMemoryUse,
  compact = false,
  dropActive = false,
  hostControls = false,
  saving = false,
}: AppShellProps) {
  return (
    <main
      className={`app-shell ${compact ? 'compact-shell' : 'expanded-shell'} ${compact && hostControls ? 'host-controlled-shell' : ''} ${dropActive ? 'memory-drop-active' : ''}`}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onPointerDownCapture={notifyHostWidgetFocus}
    >
      {compact && hostControls ? null : <header className="app-header">
        <div className="app-title-block">
          {compact ? null : <h1>Memory notes</h1>}
          <p>{compact ? `${count} in scope` : `${count} records in scope`}</p>
        </div>
        <label className={`memory-use-toggle ${compact ? 'sr-only' : ''}`}>
          <input
            type="checkbox"
            checked={memoryUseEnabled}
            onChange={onToggleMemoryUse}
            disabled={saving}
          />
          <span>Use my memory</span>
        </label>
        {allowWrite && !compact ? (
          <button type="button" className="primary-button" onClick={onCreate} disabled={!memoryUseEnabled}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginRight: 6 }}>
              <path d="M12 5v14M5 12h14" />
            </svg>
            New note
          </button>
        ) : null}
        {allowWrite && compact ? (
          <button
            type="button"
            className="icon-button compact-add-button"
            onClick={onCreate}
            disabled={!memoryUseEnabled}
            aria-label="Add memory"
            title="Add memory"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
        ) : null}
        {compact && onExpand ? (
          <button
            type="button"
            className="icon-button compact-expand-button"
            onClick={onExpand}
            aria-label="Expand memories"
            title="Expand memories"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M8 3H3v5M16 3h5v5M21 16v5h-5M3 16v5h5" />
              <path d="M3 3l7 7M21 3l-7 7M21 21l-7-7M3 21l7-7" />
            </svg>
          </button>
        ) : null}
      </header>}
      {children}
    </main>
  );
}
