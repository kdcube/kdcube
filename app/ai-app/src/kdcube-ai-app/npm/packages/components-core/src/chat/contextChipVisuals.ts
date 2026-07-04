import {
  namespacePresentationCandidates,
  namespaceRootKey,
  namespaceStyleForContext,
  namespaceStyleKey,
  namespaceStyleVars as sharedNamespaceStyleVars,
  namespaceVarsFromStyle,
  objectRefFromContext,
  safePresentationKey,
  type NamespaceStyleMap,
  type NamespaceStyleVars,
  type NamespaceVisualStyle,
} from '../shared/namespacePresentation.ts'

type ContextLike = Record<string, unknown>

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function safeClass(value: string): string {
  return safePresentationKey(value).replace(/:/g, '-')
}

function namespaceClassesFromRef(ref: string): string[] {
  const clean = ref.split(/[?#]/, 1)[0].trim().toLowerCase()
  if (!clean || !clean.includes(':')) return []
  const parts = clean.split(':').filter(Boolean).map(safeClass).filter(Boolean)
  if (!parts.length) return []
  const classes = [parts[0]]
  if (parts.length > 1) classes.push(`${parts[0]}-${parts[1]}`)
  return classes
}

export function contextNamespace(context: unknown): string {
  return namespacePresentationCandidates(context)[0] || ''
}

export function namespaceStyleVars(
  namespace: string,
  namespaceStyles: NamespaceStyleMap = {},
): NamespaceStyleVars | undefined {
  return sharedNamespaceStyleVars(namespace, namespaceStyles)
}

export function contextChipStyle(
  context: unknown,
  namespaceStyles: NamespaceStyleMap = {},
): NamespaceStyleVars | undefined {
  const resolved = namespaceStyleForContext(context, namespaceStyles)
  return resolved ? namespaceVarsFromStyle(resolved.style) : undefined
}

export function contextChipClass(context: unknown): string {
  const item = context && typeof context === 'object' && !Array.isArray(context) ? context as ContextLike : {}
  const data = item.data && typeof item.data === 'object' && !Array.isArray(item.data) ? item.data as ContextLike : {}
  const ref = objectRefFromContext(item)
  const classes = [
    text(item.kind),
    text(item.cardType),
    text(item.card_type),
    text(item.namespace),
    text(item.object_kind),
    text(data.namespace),
    text(data.object_kind),
    ...namespaceClassesFromRef(ref),
  ]
  return Array.from(new Set(classes.map(safeClass).filter(Boolean))).join(' ')
}

export type { NamespaceStyleMap, NamespaceStyleVars, NamespaceVisualStyle }
export { namespaceRootKey, namespaceStyleKey }
