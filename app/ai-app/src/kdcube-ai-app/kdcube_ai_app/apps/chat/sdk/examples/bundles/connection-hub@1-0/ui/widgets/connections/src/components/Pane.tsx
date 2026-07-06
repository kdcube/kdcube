/**
 * Pane system for Connection Hub tabs — the workspace-scene window idea
 * (ui/scene windows.tsx) scaled down to a widget: each tab shows its areas
 * as PINNED panes stacked in one viewport-high column, each pane scrolling
 * internally. A pane can be expanded to fill the column, unpinned into a
 * floating draggable/resizable window, and docked back. A splitter between
 * two docked panes adjusts their share. The page itself never scrolls.
 */

import { useCallback, useRef, useState, type ReactNode, type PointerEvent as ReactPointerEvent } from 'react';

const MIN_W = 320;
const MIN_H = 200;
const BASE_Z = 1000;

interface FloatRect {
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
}

interface PaneState {
  floating: boolean;
  expanded: boolean;
  rect: FloatRect;
}

export interface PaneDef {
  id: string;
  title: string;
  content: ReactNode;
}

const ICON_UNPIN = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M7 17 17 7M9 7h8v8" />
  </svg>
);
const ICON_DOCK = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M5 12h14M12 5l-7 7 7 7" />
  </svg>
);
const ICON_EXPAND = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M3 16v3a2 2 0 0 0 2 2h3" />
  </svg>
);
const ICON_COLLAPSE = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M8 3v3a2 2 0 0 1-2 2H3M16 3v3a2 2 0 0 0 2 2h3M21 16h-3a2 2 0 0 0-2 2v3M3 16h3a2 2 0 0 1 2 2v3" />
  </svg>
);

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function defaultRect(index: number, z: number): FloatRect {
  const offset = index * 26;
  const w = clamp(Math.round(window.innerWidth * 0.72), MIN_W, window.innerWidth - 24);
  const h = clamp(Math.round(window.innerHeight * 0.6), MIN_H, window.innerHeight - 60);
  return {
    x: clamp(Math.round((window.innerWidth - w) / 2) + offset, 8, Math.max(8, window.innerWidth - w - 8)),
    y: clamp(40 + offset, 8, Math.max(8, window.innerHeight - h - 8)),
    w,
    h,
    z,
  };
}

export function PaneGroup({ panes }: { panes: PaneDef[] }) {
  const [states, setStates] = useState<Record<string, PaneState>>({});
  // Share of the docked column taken by the FIRST docked pane (two-pane case).
  const [share, setShare] = useState(0.55);
  const zRef = useRef(BASE_Z);
  const columnRef = useRef<HTMLDivElement | null>(null);

  const stateOf = (id: string, index: number): PaneState =>
    states[id] ?? { floating: false, expanded: false, rect: defaultRect(index, BASE_Z) };

  const update = useCallback((id: string, index: number, patch: Partial<PaneState> | ((prev: PaneState) => PaneState)) => {
    setStates((current) => {
      const prev = current[id] ?? { floating: false, expanded: false, rect: defaultRect(index, BASE_Z) };
      const next = typeof patch === 'function' ? patch(prev) : { ...prev, ...patch };
      return { ...current, [id]: next };
    });
  }, []);

  const front = useCallback((id: string, index: number) => {
    zRef.current += 2;
    const z = zRef.current;
    update(id, index, (prev) => ({ ...prev, rect: { ...prev.rect, z } }));
  }, [update]);

  const startDrag = useCallback((id: string, index: number, event: ReactPointerEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest('button')) return;
    event.preventDefault();
    front(id, index);
    const startX = event.clientX;
    const startY = event.clientY;
    const origin = (states[id] ?? stateOf(id, index)).rect;
    const onMove = (move: PointerEvent) => {
      update(id, index, (prev) => ({
        ...prev,
        rect: {
          ...prev.rect,
          x: clamp(origin.x + move.clientX - startX, 4, Math.max(4, window.innerWidth - 80)),
          y: clamp(origin.y + move.clientY - startY, 4, Math.max(4, window.innerHeight - 48)),
        },
      }));
    };
    const finish = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', finish);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', finish);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [front, states, update]);

  const startResize = useCallback((id: string, index: number, event: ReactPointerEvent<HTMLElement>) => {
    event.preventDefault();
    front(id, index);
    const startX = event.clientX;
    const startY = event.clientY;
    const origin = (states[id] ?? stateOf(id, index)).rect;
    const onMove = (move: PointerEvent) => {
      update(id, index, (prev) => ({
        ...prev,
        rect: {
          ...prev.rect,
          w: clamp(origin.w + move.clientX - startX, MIN_W, window.innerWidth - 8),
          h: clamp(origin.h + move.clientY - startY, MIN_H, window.innerHeight - 8),
        },
      }));
    };
    const finish = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', finish);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', finish);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [front, states, update]);

  const startSplit = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    event.preventDefault();
    const column = columnRef.current;
    if (!column) return;
    const bounds = column.getBoundingClientRect();
    const onMove = (move: PointerEvent) => {
      const ratio = (move.clientY - bounds.top) / Math.max(1, bounds.height);
      setShare(clamp(ratio, 0.2, 0.8));
    };
    const finish = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', finish);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', finish);
  }, []);

  const resolved = panes.map((pane, index) => ({ pane, index, state: stateOf(pane.id, index) }));
  const docked = resolved.filter((item) => !item.state.floating);
  const expandedDocked = docked.find((item) => item.state.expanded);
  const visibleDocked = expandedDocked ? [expandedDocked] : docked;
  const showSplitter = !expandedDocked && docked.length === 2;

  const renderBar = (item: (typeof resolved)[number]) => {
    const { pane, index, state } = item;
    return (
      <header
        className="pane-bar"
        onPointerDown={state.floating ? (event) => startDrag(pane.id, index, event) : undefined}
      >
        <span className="pane-title">{pane.title}</span>
        <span className="pane-controls">
          {!state.floating ? (
            <button
              type="button"
              className="pane-btn"
              title={state.expanded ? 'Restore' : 'Expand'}
              aria-label={state.expanded ? 'Restore' : 'Expand'}
              onClick={() => update(pane.id, index, { expanded: !state.expanded })}
            >
              {state.expanded ? ICON_COLLAPSE : ICON_EXPAND}
            </button>
          ) : null}
          <button
            type="button"
            className="pane-btn"
            title={state.floating ? 'Pin back into the page' : `Pop out ${pane.title}`}
            aria-label={state.floating ? 'Pin back into the page' : `Pop out ${pane.title}`}
            onClick={() => {
              if (state.floating) {
                update(pane.id, index, { floating: false, expanded: false });
              } else {
                zRef.current += 2;
                const z = zRef.current;
                update(pane.id, index, (prev) => ({
                  ...prev,
                  floating: true,
                  expanded: false,
                  rect: { ...defaultRect(index, z), z },
                }));
              }
            }}
          >
            {state.floating ? ICON_DOCK : ICON_UNPIN}
          </button>
        </span>
      </header>
    );
  };

  return (
    <div className="pane-group" ref={columnRef}>
      {visibleDocked.map((item, position) => (
        <div
          key={item.pane.id}
          className="pane"
          style={
            expandedDocked || visibleDocked.length === 1
              ? { flex: '1 1 auto' }
              : { flex: `${position === 0 ? share : 1 - share} 1 0%` }
          }
        >
          {renderBar(item)}
          <div className="pane-body">{item.pane.content}</div>
          {showSplitter && position === 0 ? (
            <div
              className="pane-splitter"
              role="separator"
              aria-orientation="horizontal"
              title="Drag to resize"
              onPointerDown={startSplit}
            />
          ) : null}
        </div>
      ))}
      {resolved.filter((item) => item.state.floating).map((item) => (
        <section
          key={item.pane.id}
          className="pane pane--floating"
          style={{
            left: item.state.rect.x,
            top: item.state.rect.y,
            width: item.state.rect.w,
            height: item.state.rect.h,
            zIndex: item.state.rect.z,
          }}
          aria-label={item.pane.title}
          onPointerDownCapture={() => front(item.pane.id, item.index)}
        >
          {renderBar(item)}
          <div className="pane-body">{item.pane.content}</div>
          <button
            type="button"
            className="pane-grip"
            title="Resize"
            aria-label="Resize"
            onPointerDown={(event) => startResize(item.pane.id, item.index, event)}
          />
        </section>
      ))}
    </div>
  );
}
