/**
 * A collapsible key/value tree for arbitrary config — the completeness fallback,
 * so the viewer shows the whole app config, not a hand-picked subset. Objects and
 * arrays are expandable branches; primitives are leaves. Long strings wrap.
 */
import { useState } from 'react';

function isObj(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function Primitive({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="ac-tree__null">null</span>;
  if (typeof value === 'boolean') return <span className="ac-tree__bool">{String(value)}</span>;
  if (typeof value === 'number') return <span className="ac-tree__num">{value}</span>;
  return <span className="ac-tree__str">{String(value)}</span>;
}

function Node({
  label,
  value,
  depth,
}: {
  label?: string;
  value: unknown;
  depth: number;
}) {
  const branch = isObj(value) || Array.isArray(value);
  const [open, setOpen] = useState(depth < 1);

  if (!branch) {
    return (
      <div className="ac-tree__leaf">
        {label !== undefined && <span className="ac-tree__key">{label}</span>}
        <Primitive value={value} />
      </div>
    );
  }

  const entries: [string, unknown][] = Array.isArray(value)
    ? value.map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>);

  return (
    <div className="ac-tree__branch">
      <button type="button" className="ac-tree__toggle" onClick={() => setOpen((o) => !o)}>
        <span className="ac-tree__caret" aria-hidden>{open ? '▾' : '▸'}</span>
        {label !== undefined && <span className="ac-tree__key">{label}</span>}
        <span className="ac-tree__meta">
          {Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`}
        </span>
      </button>
      {open && (
        <div className="ac-tree__children">
          {entries.map(([k, v]) => (
            <Node key={k} label={k} value={v} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

export function ConfigTree({ value, omitKeys }: { value: unknown; omitKeys?: string[] }) {
  const obj = isObj(value) ? value : {};
  const entries = Object.entries(obj).filter(([k]) => !omitKeys?.includes(k));
  if (entries.length === 0) {
    return <p className="ac-note ac-note--muted">No further configuration.</p>;
  }
  return (
    <div className="ac-tree">
      {entries.map(([k, v]) => (
        <Node key={k} label={k} value={v} depth={0} />
      ))}
    </div>
  );
}
