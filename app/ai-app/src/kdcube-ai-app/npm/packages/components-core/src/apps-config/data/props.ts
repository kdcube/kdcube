/** Prop-tree helpers used by normalize + http-source (defaults←props merge, path reads). */

/** Deep-merge objects; arrays and primitives from `over` replace those in `base`. */
export function deepMerge<T = unknown>(base: unknown, over: unknown): T {
  if (over === undefined || over === null) return base as T;
  if (typeof base !== 'object' || base === null || Array.isArray(base)) return over as T;
  if (typeof over !== 'object' || Array.isArray(over)) return over as T;
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const k of Object.keys(over as Record<string, unknown>)) {
    out[k] = deepMerge((base as Record<string, unknown>)[k], (over as Record<string, unknown>)[k]);
  }
  return out as T;
}

/** Read a dotted path (`surfaces.as_consumer.agents`) defensively. */
export function getPath(obj: unknown, path: string): unknown {
  return path
    .split('.')
    .reduce<unknown>((acc, k) => (acc == null ? undefined : (acc as Record<string, unknown>)[k]), obj);
}
