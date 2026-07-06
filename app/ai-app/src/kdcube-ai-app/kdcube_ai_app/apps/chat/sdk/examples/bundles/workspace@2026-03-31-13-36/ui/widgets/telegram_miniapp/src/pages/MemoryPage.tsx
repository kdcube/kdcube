import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { settings } from '../store/settings';
import { installConfigHandshakeHost } from '../auth/configHandshakeHost';
import type { MemoryPayload } from '../store/types';

// Memory lives in the user-memories app. The Mini App loads it as a same-origin
// served-widget iframe (like the scene host does) — the whole tab IS the widget.
// The host answers the iframe's standard CONFIG_REQUEST with a CONFIG_RESPONSE
// that carries the host-owned authContext.headers; the widget promotes those
// onto its own requests without knowing Telegram.
//
// View model mirrors the scene's memory panel: the iframe is served in COMPACT
// mode with host_controls=1, so the widget suppresses its own header chrome and
// the host renders the compact header (title · count, expand/collapse).
// Expand/collapse is driven host-side by posting `kdcube-set-view` to the
// widget — no iframe reload — exactly as the scene does (ui/scene/src/main.tsx
// `syncMemoryWidgetView` + the memory-pane expand button).
const MEMORY_WIDGET_BUNDLE_ID = 'user-memories@2026-06-26';
const MEMORY_WIDGET_ALIAS = 'memories';
const MEMORY_WIDGET_IDENTITY = 'MEMORIES_WIDGET';
// Compact shows a short list; expand reveals the rest. Match the scene's small
// preview count so the compact tab stays a glanceable summary.
const COMPACT_LIMIT = '4';

// Iframe heights are driven as INLINE styles (not a bare CSS rule). Mobile
// Telegram's webview defers layout of a CSS-rule-sized iframe until a reflow
// (e.g. a tab switch fires visibilitychange), leaving the iframe at its ~16px
// intrinsic height on first mount. An inline style assignment + a forced reflow
// makes the webview lay it out immediately, with no tab switch or manual toggle.
const COMPACT_FRAME_HEIGHT = '320px';
const EXPANDED_FRAME_HEIGHT = '76vh';

// On-screen layout/handshake readout. Mobile Telegram has no devtools console,
// so the trace is rendered as a tiny dismissable overlay the maintainer can
// screenshot. Flip to false to remove it entirely.
const DEBUG = true;

interface DebugStats {
  iframeRectH: number;
  frameRectH: number;
  iframeComputedH: string;
  lastMessage: string;
}

interface MemoryPageProps {
  memory?: MemoryPayload;
  reload?: () => Promise<void>;
}

export function MemoryPage(_props: MemoryPageProps) {
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const frameWrapRef = useRef<HTMLDivElement | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [count, setCount] = useState<number | null>(null);
  // Keep `expanded` reachable inside the persistent message listener without
  // re-subscribing on every toggle (which would drop the listener mid-handshake).
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;
  // The widget's FIRST kdcube-memory-widget-status proves it has mounted and is
  // listening — the iframe `onLoad` fires earlier (document load), before the
  // React widget hydrates, so the set-view posted there can be dropped.
  const widgetReadyRef = useRef(false);

  const [debugStats, setDebugStats] = useState<DebugStats>({
    iframeRectH: -1, frameRectH: -1, iframeComputedH: '?', lastMessage: '—',
  });
  const [debugHidden, setDebugHidden] = useState(false);

  const sampleDebug = useCallback((lastMessage?: string) => {
    if (!DEBUG) return;
    const frame = frameRef.current;
    const wrap = frameWrapRef.current;
    setDebugStats((prev) => ({
      iframeRectH: frame ? Math.round(frame.getBoundingClientRect().height) : -1,
      frameRectH: wrap ? Math.round(wrap.getBoundingClientRect().height) : -1,
      iframeComputedH: frame ? getComputedStyle(frame).height : '?',
      lastMessage: lastMessage ?? prev.lastMessage,
    }));
  }, []);

  // Set the iframe height as an INLINE style for the current view and force the
  // webview to lay it out now (reading offsetHeight flushes a reflow). Without
  // this, mobile Telegram leaves the bare-CSS-sized iframe un-laid-out (~16px)
  // until a tab switch. Re-asserted on mount, on view change, on first widget
  // status, on iframe load, and on visibilitychange.
  const applyFrameHeight = useCallback((isExpanded: boolean) => {
    const frame = frameRef.current;
    if (!frame) return;
    frame.style.height = isExpanded ? EXPANDED_FRAME_HEIGHT : COMPACT_FRAME_HEIGHT;
    // Force a synchronous layout pass so the webview commits the height instead
    // of deferring it; a double rAF re-asserts after the next paint as a belt.
    void frame.offsetHeight;
    window.requestAnimationFrame(() => {
      const f = frameRef.current;
      if (!f) return;
      f.style.height = isExpanded ? EXPANDED_FRAME_HEIGHT : COMPACT_FRAME_HEIGHT;
      void f.offsetHeight;
    });
    sampleDebug();
  }, [sampleDebug]);

  const memoryWidgetSrc = useMemo(
    () => settings.widgetUrlForBundle(MEMORY_WIDGET_BUNDLE_ID, MEMORY_WIDGET_ALIAS, {
      view: 'compact',
      compact: '1',
      host_controls: '1',
      limit: COMPACT_LIMIT,
    }),
    [],
  );

  // Answer the memory iframe's standard CONFIG_REQUEST. Inside Telegram the
  // CONFIG_RESPONSE config also carries the host-owned authContext.headers. A
  // kdcube-auth-changed nudge re-triggers the handshake if proof lands after the
  // frame mounts.
  useEffect(
    () => installConfigHandshakeHost(frameRef.current, {
      identity: MEMORY_WIDGET_IDENTITY,
      bundleId: MEMORY_WIDGET_BUNDLE_ID,
    }),
    [memoryWidgetSrc],
  );

  // Tell the widget which view to render (compact/expanded). Posting on every
  // change keeps the widget in sync; it also re-posts on the iframe `onLoad`
  // and on the widget's first status (see below) so the initial view always
  // lands after the widget is mounted and listening.
  const syncView = useCallback((view: 'compact' | 'expanded') => {
    frameRef.current?.contentWindow?.postMessage({
      type: 'kdcube-set-view',
      widget: MEMORY_WIDGET_ALIAS,
      view,
    }, '*');
  }, []);

  // Apply the inline height (mount + every view change) and tell the widget
  // which view to render. The height assignment is what forces mobile Telegram
  // to lay the iframe out without a tab switch.
  useEffect(() => {
    applyFrameHeight(expanded);
    syncView(expanded ? 'expanded' : 'compact');
  }, [expanded, applyFrameHeight, syncView]);

  // Re-assert the height when the tab becomes visible. This matches the manual
  // "switch out and back" recovery the maintainer observed and is a cheap
  // safety net if the first layout pass was still deferred.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') applyFrameHeight(expandedRef.current);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, []);

  // Keep the on-screen readout live while DEBUG is on.
  useEffect(() => {
    if (!DEBUG) return undefined;
    sampleDebug('mount');
    const timer = window.setInterval(() => sampleDebug(), 500);
    return () => window.clearInterval(timer);
  }, [sampleDebug]);

  // The widget reports its record count + (with host_controls) view-change
  // requests from its own affordances. Reflect those host-side.
  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const data = event.data;
      if (!data || typeof data !== 'object' || data.widget !== MEMORY_WIDGET_ALIAS) return;
      if (data.type === 'kdcube-memory-widget-status') {
        const next = Number(data.count);
        const firstStatus = !widgetReadyRef.current;
        setCount(Number.isFinite(next) ? next : null);
        sampleDebug(`status${firstStatus ? ' (first)' : ''} count=${data.count} compact=${data.compact}`);
        // First status = the widget has hydrated and its message listener is
        // live. The set-view posted on iframe onLoad could have been dropped
        // (fired before hydration), so re-post the current view now.
        if (firstStatus) {
          widgetReadyRef.current = true;
          // Re-assert the inline height now that the widget content exists, so
          // the webview lays the iframe out against real content if it deferred
          // the mount-time pass.
          applyFrameHeight(expandedRef.current);
          syncView(expandedRef.current ? 'expanded' : 'compact');
        }
        return;
      }
      if (data.type === 'kdcube-widget-view') {
        sampleDebug(`widget-view ${data.view}`);
        setExpanded(data.view === 'expanded');
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [syncView, sampleDebug, applyFrameHeight]);

  return (
    <section className={`page page-wide memory-embed-page${expanded ? ' memory-expanded' : ''}`}>
      <header className="memory-pane-header">
        <span className="memory-pane-title">
          <strong>Memories</strong>
          {count !== null ? <small>{count} in scope</small> : null}
        </span>
        <div className="memory-pane-actions">
          <button
            type="button"
            className="ghost-button icon-only"
            onClick={() => setExpanded((value) => !value)}
            title={expanded ? 'Compact memories' : 'Enlarge memories'}
            aria-label={expanded ? 'Compact memories' : 'Enlarge memories'}
          >
            {expanded ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M8 3H3v5M21 8V3h-5M16 21h5v-5M3 16v5h5" />
                <path d="M3 3l7 7M21 3l-7 7M21 21l-7-7M3 21l7-7" />
              </svg>
            )}
          </button>
        </div>
      </header>
      <div className="memory-widget-frame" ref={frameWrapRef}>
        <iframe
          ref={frameRef}
          src={memoryWidgetSrc}
          title="Memories"
          className="memory-widget-iframe"
          onLoad={() => {
            sampleDebug('iframe onLoad');
            applyFrameHeight(expanded);
            syncView(expanded ? 'expanded' : 'compact');
          }}
        />
      </div>
      {DEBUG && !debugHidden ? (
        <div className="memory-debug-overlay" role="status">
          <button
            type="button"
            className="memory-debug-close"
            onClick={() => setDebugHidden(true)}
            aria-label="Hide debug readout"
          >
            ×
          </button>
          <div>iframe rect h: <strong>{debugStats.iframeRectH}</strong></div>
          <div>frame wrap h: <strong>{debugStats.frameRectH}</strong></div>
          <div>iframe computed h: <strong>{debugStats.iframeComputedH}</strong></div>
          <div>expanded: <strong>{String(expanded)}</strong> · count: <strong>{count ?? '—'}</strong></div>
          <div>last msg: <strong>{debugStats.lastMessage}</strong></div>
        </div>
      ) : null}
    </section>
  );
}
