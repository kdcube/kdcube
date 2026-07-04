type RecordLike = Record<string, unknown>

export interface NamespaceVisualStyle {
  label?: string
  color?: string
  ink?: string
  border?: string
  focus?: string
  background?: string
  icon?: string
  icon_svg?: string
  iconSvg?: string
}

export type NamespaceStyleMap = Record<string, unknown>
export type NamespaceStyleVars = Record<`--${string}`, string>

function record(value: unknown): RecordLike {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RecordLike : {}
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function safePresentationKey(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.:-]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

export function namespaceRootKey(value: string): string {
  return safePresentationKey(value.split(':', 1)[0] || '')
}

export function namespaceStyleKey(value: string): string {
  const clean = String(value || '').split(/[?#]/, 1)[0].trim().toLowerCase()
  if (!clean) return ''
  const parts = clean.split(':').filter(Boolean)
  if (parts[0] === 'conv' && parts[1]) return `conv:${safePresentationKey(parts[1])}`
  return namespaceRootKey(clean)
}

export function namespaceStyleKeyFromObjectRef(ref: string): string {
  const clean = String(ref || '').split(/[?#]/, 1)[0].trim()
  return clean.includes(':') ? namespaceStyleKey(clean) : ''
}

export function objectRefFromContext(value: unknown): string {
  const item = record(value)
  const data = record(item.data)
  const keys = ['ref', 'object_ref', 'objectRef', 'logicalPath', 'logical_path', 'hostedUri', 'hosted_uri', 'event_ref', 'uri', 'canonical_uri']
  for (const key of keys) {
    const current = text(item[key])
    if (current) return current
  }
  for (const key of keys) {
    const current = text(data[key])
    if (current) return current
  }
  return ''
}

export function namespacePresentationCandidates(value: unknown): string[] {
  const item = record(value)
  const data = record(item.data)
  const seen = new Set<string>()
  const out: string[] = []
  const add = (candidate: string): void => {
    const key = safePresentationKey(candidate)
    if (!key || seen.has(key)) return
    seen.add(key)
    out.push(key)
  }
  const addNamespace = (candidate: string): void => {
    const key = namespaceStyleKey(candidate)
    if (!key || seen.has(key)) return
    seen.add(key)
    out.push(key)
  }

  add(text(item.object_kind))
  add(text(item.objectKind))
  add(text(data.object_kind))
  add(text(data.objectKind))
  addNamespace(namespaceStyleKeyFromObjectRef(objectRefFromContext(item)))
  addNamespace(text(item.namespace))
  addNamespace(text(data.namespace))

  return out
}

export function styleFromNamespaceRaw(value: unknown): NamespaceVisualStyle | null {
  if (typeof value === 'string' && value.trim()) return { color: value.trim() }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as NamespaceVisualStyle
}

export function namespaceStyleForKey(
  namespace: string,
  namespaceStyles: NamespaceStyleMap = {},
): NamespaceVisualStyle | null {
  const exact = safePresentationKey(namespace)
  const key = namespaceStyleKey(namespace)
  const root = namespaceRootKey(namespace)
  if (!root) return null
  return (
    styleFromNamespaceRaw(namespaceStyles[exact]) ||
    (key !== exact ? styleFromNamespaceRaw(namespaceStyles[key]) : null) ||
    (root !== exact && root !== key ? styleFromNamespaceRaw(namespaceStyles[root]) : null)
  )
}

export function namespaceStyleForContext(
  value: unknown,
  namespaceStyles: NamespaceStyleMap = {},
): { key: string; style: NamespaceVisualStyle } | null {
  for (const key of namespacePresentationCandidates(value)) {
    const style = namespaceStyleForKey(key, namespaceStyles)
    if (style) return { key, style }
  }
  return null
}

export function namespaceStyleVars(
  namespace: string,
  namespaceStyles: NamespaceStyleMap = {},
): NamespaceStyleVars | undefined {
  const style = namespaceStyleForKey(namespace, namespaceStyles)
  if (!style) return undefined
  return namespaceVarsFromStyle(style)
}

export function namespaceVarsFromStyle(style: NamespaceVisualStyle): NamespaceStyleVars | undefined {
  const ink = style.ink || style.color
  const border = style.border || style.color
  const focus = style.focus || style.color
  const background = style.background
  const vars: NamespaceStyleVars = {}
  if (ink) {
    vars['--ctx-ink'] = ink
    vars['--ns-ink'] = ink
  }
  if (border) {
    vars['--ctx-border'] = border
    vars['--ns-border'] = border
  }
  if (focus) {
    vars['--ctx-focus'] = focus
    vars['--ns-focus'] = focus
  }
  if (background) {
    vars['--ctx-bg'] = background
    vars['--ns-bg'] = background
  }
  return Object.keys(vars).length ? vars : undefined
}
