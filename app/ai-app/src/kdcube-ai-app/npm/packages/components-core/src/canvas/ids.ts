const ALPHABET = '0123456789abcdefghijklmnopqrstuvwxyz'

function base36(value: number, width: number): string {
  let current = Math.max(0, Math.floor(value))
  let out = ''
  if (current === 0) out = '0'
  while (current > 0) {
    out = ALPHABET[current % 36] + out
    current = Math.floor(current / 36)
  }
  return out.padStart(width, '0').slice(-width)
}

function safePrefix(prefix: string): string {
  const safe = String(prefix || '').replace(/[^a-zA-Z0-9_-]/g, '_').replace(/^[_-]+|[_-]+$/g, '')
  return safe || 'id'
}

export function timestampSlugId(prefix: string, slugLen = 4): string {
  const date = new Date()
  const slug = base36(Math.floor(Math.random() * (36 ** Math.max(2, Math.min(8, slugLen)))), Math.max(2, Math.min(8, slugLen)))
  const timestamp = [
    date.getUTCFullYear(),
    String(date.getUTCMonth() + 1).padStart(2, '0'),
    String(date.getUTCDate()).padStart(2, '0'),
  ].join('-') + '-' + [
    String(date.getUTCHours()).padStart(2, '0'),
    String(date.getUTCMinutes()).padStart(2, '0'),
    String(date.getUTCSeconds()).padStart(2, '0'),
  ].join('-')
  return `${safePrefix(prefix)}_${timestamp}_${slug}`
}
