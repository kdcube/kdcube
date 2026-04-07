import { EventLoggerService } from './eventLoggerService'
import type { RootState } from '../../app/store'

export type { ExternalLogEvent, EventBase } from './types'
export { EventLoggerService } from './eventLoggerService'

/**
 * Initializes Event Logger service
 * Should be called once on application load
 */
export function initializeEventLogger(store: { getState(): RootState }) {
    return EventLoggerService.initialize(store);
}