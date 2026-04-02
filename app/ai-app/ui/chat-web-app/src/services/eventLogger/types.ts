export type EventBase = {
    event_type: "log"
    tenant: string
    project: string
    user_id: string | null
    session_id: string | null
    conversation_id: string | null
    timestamp: string
    timezone: string
}

export type ExternalLogEvent = EventBase & {
    level: "error" | "warning" | "info"
    message: string
    args: unknown[]
}