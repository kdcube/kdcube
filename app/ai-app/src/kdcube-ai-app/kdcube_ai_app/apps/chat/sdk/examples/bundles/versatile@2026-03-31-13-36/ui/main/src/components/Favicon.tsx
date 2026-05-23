/**
 * FaviconImg + resolveFavicon — used wherever a result row shows a site
 * mark (Links tab, Overview web_search/web_fetch/citation rows, Chat tab
 * web search/fetch rows).
 *
 * Strategy: prefer the artifact-provided favicon URL; fall back to
 * Google's S2 service keyed by hostname so the row always has a
 * recognisable mark. Broken images hide themselves via `onError` so the
 * row still looks intentional.
 */

/** Resolve a usable favicon URL for a result item, or null when neither
 *  an explicit favicon nor a parseable URL is available. */
export function resolveFavicon(item: { favicon?: string | null; url?: string }): string | null {
  const explicit = (item.favicon || '').trim()
  if (explicit) return explicit
  const url = (item.url || '').trim()
  if (!url) return null
  try {
    const host = new URL(url).hostname
    if (!host) return null
    return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=32`
  } catch {
    return null
  }
}

export function FaviconImg({ url, favicon }: { url?: string; favicon?: string | null }) {
  const src = resolveFavicon({ url, favicon })
  if (!src) return <span className="k-result-favicon" aria-hidden="true" />
  return (
    <img
      className="k-result-favicon"
      src={src}
      alt=""
      width={16}
      height={16}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={(event) => {
        /* Hide broken favicon images so the row still looks intentional. */
        (event.currentTarget as HTMLImageElement).style.visibility = 'hidden'
      }}
    />
  )
}
