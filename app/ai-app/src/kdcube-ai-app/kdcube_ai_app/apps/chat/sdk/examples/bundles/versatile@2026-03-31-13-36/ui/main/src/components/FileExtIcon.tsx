/**
 * Extension-aware file icon used wherever files are listed (Chat tab
 * file rows, Files tab list, future surfaces).
 *
 * `fileExtension(filename)` returns the lowercase extension.
 * `fileKind(ext)` buckets ~40 extensions into 12 icon families with a
 * short chip label. `<FileExtIcon kind={...} />` renders the icon (16px,
 * `currentColor` strokes so it inherits the row's text colour).
 */

/** Lowercase extension extractor for a filename (returns 'png', 'pdf', …
 *  or '' when the name has no dot). */
export function fileExtension(filename: string): string {
  if (!filename) return ''
  const idx = filename.lastIndexOf('.')
  if (idx < 0 || idx === filename.length - 1) return ''
  return filename.slice(idx + 1).toLowerCase()
}

/** Buckets a file extension into a coarse kind so we can pick an icon and
 *  a chip label without inventing a glyph per extension. */
export function fileKind(ext: string): {
  label: string
  icon: 'image' | 'pdf' | 'word' | 'sheet' | 'slides' | 'code' | 'data' | 'archive' | 'html' | 'video' | 'audio' | 'doc'
} {
  switch (ext) {
    case 'png':
    case 'jpg':
    case 'jpeg':
    case 'gif':
    case 'webp':
    case 'svg':
    case 'bmp':
    case 'ico':
      return { label: ext.toUpperCase(), icon: 'image' }
    case 'pdf':
      return { label: 'PDF', icon: 'pdf' }
    case 'doc':
    case 'docx':
    case 'rtf':
    case 'odt':
      return { label: ext.toUpperCase(), icon: 'word' }
    case 'xls':
    case 'xlsx':
    case 'csv':
    case 'tsv':
    case 'ods':
      return { label: ext.toUpperCase(), icon: 'sheet' }
    case 'ppt':
    case 'pptx':
    case 'odp':
    case 'key':
      return { label: ext.toUpperCase(), icon: 'slides' }
    case 'py':
    case 'js':
    case 'ts':
    case 'tsx':
    case 'jsx':
    case 'sh':
    case 'bash':
    case 'zsh':
    case 'rb':
    case 'go':
    case 'rs':
    case 'java':
    case 'c':
    case 'cpp':
    case 'cs':
    case 'php':
    case 'swift':
    case 'kt':
      return { label: ext.toUpperCase(), icon: 'code' }
    case 'json':
    case 'yaml':
    case 'yml':
    case 'xml':
    case 'toml':
    case 'ini':
      return { label: ext.toUpperCase(), icon: 'data' }
    case 'zip':
    case 'tar':
    case 'gz':
    case 'bz2':
    case '7z':
    case 'rar':
      return { label: ext.toUpperCase(), icon: 'archive' }
    case 'html':
    case 'htm':
      return { label: 'HTML', icon: 'html' }
    case 'mp4':
    case 'mov':
    case 'webm':
    case 'mkv':
    case 'avi':
      return { label: ext.toUpperCase(), icon: 'video' }
    case 'mp3':
    case 'wav':
    case 'ogg':
    case 'flac':
    case 'm4a':
      return { label: ext.toUpperCase(), icon: 'audio' }
    case 'md':
    case 'markdown':
      return { label: 'MD', icon: 'doc' }
    case 'txt':
    case 'log':
      return { label: ext.toUpperCase(), icon: 'doc' }
    default:
      return { label: ext ? ext.toUpperCase() : 'FILE', icon: 'doc' }
  }
}

/** Small extension-aware file icon. Strokes follow currentColor so the icon
 *  inherits the row's text colour. */
export function FileExtIcon({ kind }: { kind: ReturnType<typeof fileKind>['icon'] }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  }
  switch (kind) {
    case 'image':
      return (
        <svg {...common}>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <circle cx="9" cy="9" r="2" />
          <path d="m21 15-5-5L5 21" />
        </svg>
      )
    case 'pdf':
      return (
        <svg {...common}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M9 13v5M9 13h2a1.5 1.5 0 0 1 0 3H9M13 13v5M13 13h2v5h-2" />
        </svg>
      )
    case 'word':
      return (
        <svg {...common}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="m7 13 2 5 1.5-3.5L12 18l2-5" />
        </svg>
      )
    case 'sheet':
      return (
        <svg {...common}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M8 12h8M8 16h8M12 12v8" />
        </svg>
      )
    case 'slides':
      return (
        <svg {...common}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <rect x="8" y="12" width="8" height="5" />
        </svg>
      )
    case 'code':
      return (
        <svg {...common}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="m10 13-2 2 2 2M14 13l2 2-2 2" />
        </svg>
      )
    case 'data':
      return (
        <svg {...common}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M9 13a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2M15 13a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2" />
        </svg>
      )
    case 'archive':
      return (
        <svg {...common}>
          <rect x="3" y="3" width="18" height="6" />
          <path d="M5 9v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9" />
          <path d="M11 13h2v3h-2z" />
        </svg>
      )
    case 'html':
      return (
        <svg {...common}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="m9 14-2 2 2 2M15 14l2 2-2 2M13 13l-2 6" />
        </svg>
      )
    case 'video':
      return (
        <svg {...common}>
          <rect x="3" y="5" width="18" height="14" rx="2" />
          <polygon points="11 9 16 12 11 15" fill="currentColor" />
        </svg>
      )
    case 'audio':
      return (
        <svg {...common}>
          <path d="M9 18V6l11-2v12" />
          <circle cx="6" cy="18" r="3" />
          <circle cx="17" cy="16" r="3" />
        </svg>
      )
    case 'doc':
    default:
      return (
        <svg {...common}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M9 13h6M9 17h6" />
        </svg>
      )
  }
}
