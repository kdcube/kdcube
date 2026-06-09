export function durableHistoricalObjectRef(value: unknown, conversationId?: string): string | null {
  const ref = typeof value === 'string' ? value.trim() : ''
  if (!ref) return null
  const conv = String(conversationId || '').trim()
  if (ref.startsWith('fi:turn_') && conv && !/[./\\]/.test(conv)) {
    return `fi:conv_${conv}.${ref.slice('fi:'.length)}`
  }
  return ref
}
