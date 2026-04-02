export type { ExternalLogEvent, EventBase } from './types'
export { EventLoggerService } from './eventLoggerService'

/**
 * Initializes Event Logger service
 * Should be called once on application load
 */
export function initializeEventLogger(store: any) {
    return EventLoggerService.initialize(store);
}