import type { SceneRecord } from './types'
import type { SceneEventSubscriptionClaim } from './subscriptions'

export interface SceneEventBusEvent {
  source: string
  channel: string
  type: string
  envelope?: SceneRecord
  ts?: string
}

export interface SceneEventBusSnapshot {
  aliases: string[]
  subscriptions: Record<string, string[]>
}

export interface SceneEventBusOptions {
  getAliases: () => Iterable<string>
  isReady?: (alias: string) => boolean
  post: (alias: string, message: SceneRecord, event: SceneEventBusEvent, subscription: SceneEventSubscriptionClaim) => void
  queue?: (alias: string, message: SceneRecord, event: SceneEventBusEvent, subscription: SceneEventSubscriptionClaim) => void
  logger?: Pick<Console, 'info' | 'debug'>
  now?: () => string
  setTimeout?: (handler: () => void, timeout: number) => unknown
  clearTimeout?: (timer: unknown) => void
}

export interface SceneEventBus {
  normalizeEvent: (source: string, event: unknown, envelope: unknown) => SceneEventBusEvent
  register: (alias: string, subscriptions: SceneEventSubscriptionClaim[]) => void
  unregister: (alias: string) => void
  publish: (event: SceneEventBusEvent) => number
  reset: () => void
  snapshot: () => SceneEventBusSnapshot
}

function isRecord(value: unknown): value is SceneRecord {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function list(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => text(item)).filter(Boolean)
  const item = text(value)
  return item ? [item] : []
}

function envelopeEventType(envelope: unknown): string {
  if (!isRecord(envelope)) return ''
  if (text(envelope.type)) return text(envelope.type)
  const data = envelope.data
  if (isRecord(data)) {
    if (text(data.type)) return text(data.type)
    if (text(data.name)) return text(data.name)
    const nested = data.data ?? data.event ?? data.payload
    if (isRecord(nested)) {
      if (text(nested.type)) return text(nested.type)
      if (text(nested.name)) return text(nested.name)
    }
  }
  const event = envelope.event
  if (isRecord(event)) {
    if (text(event.type)) return text(event.type)
    if (text(event.name)) return text(event.name)
    if (event.step === 'accounting') return 'accounting.usage'
  }
  return ''
}

function cleanSubscription(input: SceneEventSubscriptionClaim, index: number): SceneEventSubscriptionClaim | null {
  if (!isRecord(input)) return null
  const id = text(input.id) || `subscription-${index}`
  const events = list(input.events)
  const channels = list(input.channels)
  if (!events.length && !channels.length) return null
  const debounceMs = Number(input.debounceMs || 0)
  return {
    id,
    source: text(input.source) || 'sse',
    events,
    channels,
    forwardType: text(input.forwardType) || 'kdcube-scene-event',
    reason: text(input.reason) || undefined,
    includeEnvelope: Boolean(input.includeEnvelope),
    debounceMs: Number.isFinite(debounceMs) && debounceMs > 0 ? debounceMs : undefined,
    forward: isRecord(input.forward) ? input.forward : undefined,
  }
}

function matches(subscription: SceneEventSubscriptionClaim, event: SceneEventBusEvent): boolean {
  const source = text(subscription.source)
  if (source && source !== '*' && source !== event.source) return false
  const channels = Array.isArray(subscription.channels) ? subscription.channels : []
  if (channels.length && !channels.includes(event.channel)) return false
  const events = Array.isArray(subscription.events) ? subscription.events : []
  if (events.length && !events.includes(event.type)) return false
  return true
}

function messageFor(subscription: SceneEventSubscriptionClaim, event: SceneEventBusEvent): SceneRecord {
  const message: SceneRecord = { ...(isRecord(subscription.forward) ? subscription.forward : {}) }
  message.type = text(message.type) || text(subscription.forwardType) || 'kdcube-scene-event'
  message.reason = text(message.reason) || text(subscription.reason) || event.type || event.channel
  message.scene_event = {
    source: event.source,
    channel: event.channel,
    type: event.type,
    ts: event.ts,
  }
  if (subscription.includeEnvelope) message.envelope = event.envelope || {}
  return message
}

export function createSceneEventBus(options: SceneEventBusOptions): SceneEventBus {
  const subscribers = new Map<string, SceneEventSubscriptionClaim[]>()
  const timers = new Map<string, unknown>()
  const logger = options.logger
  const now = options.now || (() => new Date().toISOString())
  const setTimer = options.setTimeout || ((handler, timeout) => globalThis.setTimeout(handler, timeout))
  const clearTimer = options.clearTimeout || ((timer) => globalThis.clearTimeout(timer as ReturnType<typeof setTimeout>))
  const isReady = options.isReady || (() => true)
  const queue = options.queue || ((alias, message, event, subscription) => options.post(alias, message, event, subscription))

  function logInfo(message: string, data: SceneRecord): void {
    logger?.info?.(`[kdc-scene] ${message}`, data)
  }

  function logDebug(message: string, data: SceneRecord): void {
    logger?.debug?.(`[kdc-scene] ${message}`, data)
  }

  function aliases(): string[] {
    return Array.from(options.getAliases()).map((alias) => text(alias)).filter(Boolean)
  }

  function subscriptionsFor(alias: string): SceneEventSubscriptionClaim[] {
    return subscribers.has(alias) ? subscribers.get(alias) || [] : []
  }

  function deliver(alias: string, subscription: SceneEventSubscriptionClaim, event: SceneEventBusEvent): void {
    const message = messageFor(subscription, event)
    if (isReady(alias)) {
      options.post(alias, message, event, subscription)
    } else {
      queue(alias, message, event, subscription)
    }
    logInfo('scene event dispatched', {
      alias,
      subscription: subscription.id,
      source: event.source,
      channel: event.channel,
      type: event.type,
      message_type: String(message.type || ''),
      ready: isReady(alias),
    })
  }

  function dispatch(alias: string, subscription: SceneEventSubscriptionClaim, event: SceneEventBusEvent): void {
    const key = `${alias}:${subscription.id}`
    const delay = Number(subscription.debounceMs || 0)
    if (delay > 0) {
      const previous = timers.get(key)
      if (previous) clearTimer(previous)
      timers.set(key, setTimer(() => {
        timers.delete(key)
        deliver(alias, subscription, event)
      }, delay))
      return
    }
    deliver(alias, subscription, event)
  }

  return {
    normalizeEvent(source: string, event: unknown, envelope: unknown): SceneEventBusEvent {
      const browserEvent = isRecord(event) ? event : {}
      const channel = text(browserEvent.type) || 'message'
      return {
        source: text(source) || 'sse',
        channel,
        type: envelopeEventType(envelope) || channel,
        envelope: isRecord(envelope) ? envelope : {},
        ts: now(),
      }
    },
    register(alias: string, subscriptions: SceneEventSubscriptionClaim[]): void {
      const key = text(alias)
      if (!key) return
      const cleaned = (Array.isArray(subscriptions) ? subscriptions : [])
        .map(cleanSubscription)
        .filter((item): item is SceneEventSubscriptionClaim => item !== null)
      subscribers.set(key, cleaned)
      logInfo('scene subscriber registered', {
        alias: key,
        subscriptions: cleaned.map((item) => item.id),
      })
    },
    unregister(alias: string): void {
      const key = text(alias)
      if (!key) return
      subscribers.delete(key)
      for (const timerKey of Array.from(timers.keys())) {
        if (timerKey.startsWith(`${key}:`)) {
          clearTimer(timers.get(timerKey))
          timers.delete(timerKey)
        }
      }
      logInfo('scene subscriber removed', { alias: key })
    },
    publish(event: SceneEventBusEvent): number {
      let matched = 0
      for (const alias of aliases()) {
        for (const subscription of subscriptionsFor(alias)) {
          if (!matches(subscription, event)) continue
          matched += 1
          dispatch(alias, subscription, event)
        }
      }
      logInfo('scene event received', {
        source: event.source,
        channel: event.channel,
        type: event.type,
        subscribers: matched,
      })
      if (!matched) {
        logDebug('scene event had no subscribers', {
          source: event.source,
          channel: event.channel,
          type: event.type,
        })
      }
      return matched
    },
    reset(): void {
      subscribers.clear()
      for (const timer of timers.values()) clearTimer(timer)
      timers.clear()
    },
    snapshot(): SceneEventBusSnapshot {
      const subscriptions: Record<string, string[]> = {}
      for (const [alias, claims] of subscribers.entries()) {
        subscriptions[alias] = claims.map((item) => item.id)
      }
      return {
        aliases: Array.from(subscribers.keys()),
        subscriptions,
      }
    },
  }
}
