import { CHAT_CANVAS_INGRESS_MESSAGE } from '../../settings.ts'

export const CANVAS_INGRESS_MESSAGE_TYPE = CHAT_CANVAS_INGRESS_MESSAGE

const DURABLE_FI_REF = /^fi:conv_[^.]+\.turn_[^.]+\./
const NAMESPACE_REF = /^[a-z][a-z0-9_.-]*:/i
const BROWSER_SCHEMES = new Set(['blob:', 'data:', 'http:', 'https:', 'javascript:', 'mailto:'])

export function isDurableFiRef(ref: string): boolean {
  return DURABLE_FI_REF.test(ref)
}

export interface ChatFileDragInput {
  ref: string
  filename: string
  mime?: string | null
  preview?: string | null
  sourceKind: 'assistant.file' | 'user.attachment'
}

export function canonicalObjectRef(...refs: Array<string | null | undefined>): string {
  for (const raw of refs) {
    const ref = typeof raw === 'string' ? raw.trim() : ''
    if (!ref) continue
    if (ref.startsWith('fi:')) {
      if (isDurableFiRef(ref)) return ref
      continue
    }
    const scheme = (ref.match(NAMESPACE_REF)?.[0] || '').toLowerCase()
    if (scheme && !BROWSER_SCHEMES.has(scheme)) return ref
  }
  return ''
}

export function chatFileDragMessage(input: ChatFileDragInput) {
  return {
    type: CANVAS_INGRESS_MESSAGE_TYPE,
    payload: {
      kind: 'chat.artifact',
      ref: input.ref,
      mime: input.mime || 'application/octet-stream',
      filename: input.filename,
      preview: input.preview || undefined,
      source_kind: input.sourceKind,
      attachment_compatible: true,
    },
  }
}

export function setChatFileDragData(dataTransfer: DataTransfer, input: ChatFileDragInput): void {
  const message = chatFileDragMessage(input)
  dataTransfer.effectAllowed = 'copy'
  dataTransfer.setData('application/json', JSON.stringify(message))
  dataTransfer.setData('text/plain', input.filename)
  if (input.ref) {
    dataTransfer.setData('text/uri-list', input.ref)
  }
}
