import type { ChatServiceEnvelope, ChatStepEnvelope } from './protocol.ts'

const USER_FACING_SERVICE_STEP_TYPES = new Set([
  'react.tool.call',
  'react.tool.result',
  'react.tool.rejected',
])

export function projectServiceEventToChatStep(env: ChatServiceEnvelope): ChatStepEnvelope | null {
  if (!USER_FACING_SERVICE_STEP_TYPES.has(env.type)) return null
  if (!env.event?.step || !env.conversation?.turn_id) return null
  return {
    ...env,
    type: 'chat.step',
    data: {
      ...(env.data || {}),
      service_event_type: env.type,
    },
  }
}
