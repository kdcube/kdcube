import type { ExternalLogEvent } from "./types.ts";
import type { RootState } from "../../app/store.ts";
import { selectTenant, selectProject } from "../../features/chat/chatSettingsSlice.ts";
import { selectUserProfile } from "../../features/profile/profile.ts";
import { selectConversationId } from "../../features/chat/chatStateSlice.ts";

interface StoreInterface {
    getState(): RootState;
}

export class EventLoggerService {
    private static instance: EventLoggerService;

    private store: StoreInterface;
    private buffer: ExternalLogEvent[] = [];
    private flushTimer: number | null = null;
    private readonly BUFFER_SIZE = 50;
    private readonly FLUSH_INTERVAL = 5000;
    private readonly ENDPOINT = "/api/events/client";

    // Save original function
    private readonly originalConsoleError: typeof console.error;

    // Storage for deduplication - key: error hash, value: timestamp
    private recentErrors = new Map<string, number>();
    private readonly DEDUP_WINDOW = 5000; // 5 seconds

    private constructor(store: StoreInterface) {
        this.store = store;
        this.originalConsoleError = console.error;
    }

    /**
     * Initializes and returns singleton instance of the service
     */
    public static initialize(store: StoreInterface): EventLoggerService {
        if (!EventLoggerService.instance) {
            EventLoggerService.instance = new EventLoggerService(store);
            EventLoggerService.instance.setupInterceptors();
        }
        return EventLoggerService.instance;
    }

    /**
     * Sets up all event interceptors
     */
    private setupInterceptors(): void {
        this.interceptConsole("error");
        this.interceptConsole("warn");
        this.interceptConsole("info");
        this.interceptUnhandledRejection();
        this.interceptGlobalError();
    }

    /**
     * Intercepts console.error, console.warn, console.info
     */
    private interceptConsole(level: "error" | "warn" | "info"): void {
        const original = console[level];

        console[level] = (...args: unknown[]) => {
            try {
                // Convert level name for events
                const eventLevel = level === "warn" ? "warning" : level;
                this.captureEvent(eventLevel as "error" | "warning" | "info", args);
            } catch (err) {
                // Prevent service from creating infinite error loops
                original.apply(console, ["EventLogger error:", err]);
            }

            // Call original function
            original.apply(console, args);
        };
    }

    /**
     * Intercepts unhandled Promise rejections
     */
    private interceptUnhandledRejection(): void {
        window.addEventListener("unhandledrejection", (event) => {
            try {
                const message = this.stringifyError(event.reason);
                this.captureEvent("error", [message]);
            } catch (err) {
                this.originalConsoleError("EventLogger error:", err);
            }
        });
    }

    /**
     * Intercepts global runtime errors
     */
    private interceptGlobalError(): void {
        window.addEventListener("error", (event) => {
            try {
                const message = event.message || "Unknown error";
                const context = `${event.filename}:${event.lineno}:${event.colno}`;
                this.captureEvent("error", [message, context]);
            } catch (err) {
                this.originalConsoleError("EventLogger error:", err);
            }
        });
    }

    /**
     * Captures event, enriches with metadata and adds to buffer
     */
    private captureEvent(level: "error" | "warning" | "info", args: unknown[]): void {
        // Get current Redux state
        const state = this.store.getState();

        // Extract metadata
        const tenant = selectTenant(state);
        const project = selectProject(state);
        const userProfile = selectUserProfile(state);
        const conversationId = selectConversationId(state);

        const userId = userProfile?.userId ?? null;
        const sessionId = userProfile?.sessionId ?? null;

        // Create message and additional arguments
        const message = this.stringifyError(args[0]);
        const eventArgs = args.slice(1);

        // Add context information (URL and stack trace)
        eventArgs.push({
            url: window.location.href,
            stack: new Error().stack,
        });

        // Create deduplication key
        const dedupeKey = `${level}:${message}`;
        const now = Date.now();

        // Check for duplication
        if (this.isDuplicate(dedupeKey, now)) {
            return;
        }

        // Get timestamp and timezone
        const timestamp = new Date().toISOString();
        const timezone = this.getTimezone();

        // Create event
        const event: ExternalLogEvent = {
            event_type: "log",
            level,
            message,
            args: eventArgs,
            tenant,
            project,
            user_id: userId,
            session_id: sessionId,
            conversation_id: conversationId ?? null,
            timestamp,
            timezone,
        };

        // Add to buffer
        this.buffer.push(event);

        // Flush if buffer is full or this is a critical error
        if (level === "error" || this.buffer.length >= this.BUFFER_SIZE) {
            this.flush();
        } else if (!this.flushTimer) {
            // Set timer for periodic flush
            this.flushTimer = window.setTimeout(() => {
                this.flush();
            }, this.FLUSH_INTERVAL);
        }
    }

    /**
     * Checks if this is a duplicate of a recent error
     */
    private isDuplicate(dedupeKey: string, now: number): boolean {
        const lastTime = this.recentErrors.get(dedupeKey);

        if (lastTime && now - lastTime < this.DEDUP_WINDOW) {
            return true;
        }

        this.recentErrors.set(dedupeKey, now);

        // Clean up old entries
        if (this.recentErrors.size > 100) {
            const cutoff = now - this.DEDUP_WINDOW;
            for (const [key, time] of this.recentErrors.entries()) {
                if (time < cutoff) {
                    this.recentErrors.delete(key);
                }
            }
        }

        return false;
    }

    /**
     * Sends buffer of events to the backend
     */
    private flush(): void {
        if (this.flushTimer) {
            clearTimeout(this.flushTimer);
            this.flushTimer = null;
        }

        if (this.buffer.length === 0) {
            return;
        }

        const eventsToSend = [...this.buffer];
        this.buffer = [];

        // Fire-and-forget: don't wait for response, don't handle errors
        fetch(this.ENDPOINT, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(eventsToSend),
        }).catch((err) => {
            // Log send errors only with original console (avoid infinite loop)
            this.originalConsoleError("Failed to send events to collector:", err);
        });
    }

    /**
     * Converts error to string
     */
    private stringifyError(error: unknown): string {
        if (typeof error === "string") {
            return error;
        }
        if (error instanceof Error) {
            return error.message;
        }
        if (typeof error === "object" && error !== null) {
            try {
                return JSON.stringify(error);
            } catch {
                return String(error);
            }
        }
        return String(error);
    }

    /**
     * Gets current timezone
     */
    private getTimezone(): string {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch {
            return "UTC";
        }
    }

}