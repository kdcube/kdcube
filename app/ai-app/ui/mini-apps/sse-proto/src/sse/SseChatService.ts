// src/sse/SseChatService.ts
import { v4 as uuidv4 } from "uuid";
import type {
    ChatStartEnvelope,
    ChatStepEnvelope,
    ChatDeltaEnvelope,
    ChatCompleteEnvelope,
    ChatErrorEnvelope,
    ConvStatusEnvelope,
    SessionProfile,
} from "../types";

type EventHandlers = {
    onReady?: (payload: any) => void;

    onChatStart?: (env: ChatStartEnvelope) => void;
    onChatStep?: (env: ChatStepEnvelope) => void;
    onChatDelta?: (env: ChatDeltaEnvelope) => void;
    onChatComplete?: (env: ChatCompleteEnvelope) => void;
    onChatError?: (env: ChatErrorEnvelope) => void;
    onConvStatus?: (env: ConvStatusEnvelope) => void;

    onError?: (e: any) => void;
};

export type WireChatMessage = {
    role: "user" | "assistant";
    content: string;
    timestamp?: string;
    id: number;
};

export type ChatRequest = {
    message: string; // text
    chat_history: WireChatMessage[];
    project?: string;
    tenant?: string;
    turn_id?: string;
    conversation_id?: string;
    bundle_id?: string;
};

export class SseChatService {
    private baseUrl: string;
    private tenant: string;
    private project: string;
    private authToken?: string;
    private idToken?: string;

    private sessionId?: string;
    private streamId?: string;
    private es?: EventSource;

    constructor(opts?: {
        baseUrl?: string;
        tenant?: string;
        project?: string;
        authToken?: string;
        idToken?: string;
    }) {
        this.baseUrl = opts?.baseUrl || import.meta.env.VITE_BASE_URL;
        this.tenant = opts?.tenant || import.meta.env.VITE_TENANT;
        this.project = opts?.project || import.meta.env.VITE_PROJECT;
        this.authToken = opts?.authToken || import.meta.env.VITE_AUTH_TOKEN;
        this.idToken = opts?.idToken || import.meta.env.VITE_ID_TOKEN;
        console.log(`SseChatService configured:`, this.baseUrl);
    }

    getSessionId() {
        return this.sessionId;
    }

    getStreamId() {
        return this.streamId;
    }

    async fetchProfile(): Promise<SessionProfile> {
        const headers: HeadersInit = {};
        if (this.authToken) headers["Authorization"] = `Bearer ${this.authToken}`;

        const res = await fetch(`${this.baseUrl}/profile`, {
            method: "GET",
            headers,
            credentials: "include", // cookies for session
        });
        if (!res.ok) throw new Error(`/profile failed (${res.status})`);
        const j = await res.json();
        if (!j.session_id) throw new Error("No session_id in /profile response");
        this.sessionId = j.session_id;
        return j as SessionProfile;
    }

    async connect(handlers: EventHandlers = {}): Promise<void> {
        if (!this.sessionId) await this.fetchProfile();
        this.streamId = uuidv4();

        const url = new URL(`${this.baseUrl}/sse/stream`);
        url.searchParams.set("user_session_id", this.sessionId!);
        url.searchParams.set("stream_id", this.streamId!);
        url.searchParams.set("tenant", this.tenant);
        url.searchParams.set("project", this.project);
        if (this.authToken) url.searchParams.set("bearer_token", this.authToken);
        if (this.idToken) url.searchParams.set("id_token", this.idToken);

        // Important: withCredentials so cookies flow if same-domain CORS permits
        this.es = new EventSource(url.toString(), { withCredentials: true });

        const bind = <T = any>(event: string, cb?: (d: T) => void) => {
            if (!cb) return;
            this.es!.addEventListener(event, (e: MessageEvent) => {
                try {
                    cb(JSON.parse(e.data));
                } catch {
                    // ignore malformed
                }
            });
        };

        bind("ready", handlers.onReady);
        bind<ChatStartEnvelope>("chat_start", handlers.onChatStart);
        bind<ChatStepEnvelope>("chat_step", handlers.onChatStep);
        bind<ChatDeltaEnvelope>("chat_delta", handlers.onChatDelta);
        bind<ChatCompleteEnvelope>("chat_complete", handlers.onChatComplete);
        bind<ChatErrorEnvelope>("chat_error", handlers.onChatError);
        bind<ConvStatusEnvelope>("conv_status", handlers.onConvStatus);

        this.es.onerror = (e) => handlers.onError?.(e);

        // Wait until we receive the “open” (ready) server event or the connection opens
        await new Promise<void>((resolve, reject) => {
            let resolved = false;
            const onOpen = () => {
                if (!resolved) {
                    resolved = true;
                    resolve();
                }
            };
            const t = setTimeout(() => {
                if (!resolved) {
                    reject(new Error("SSE open timeout"));
                }
            }, 8000);

            // Native EventSource doesn’t fire a named ‘open’ event we can attach.
            // We infer readiness when the first 'ready' event arrives OR if no error for a brief window.
            this.es!.addEventListener("ready", () => {
                clearTimeout(t);
                onOpen();
            });
            // Fallback: consider it connected after 500ms if no error
            setTimeout(() => {
                if (!resolved) onOpen();
            }, 500);
        });
    }

    disconnect() {
        if (this.es) {
            this.es.close();
            this.es = undefined;
        }
    }

    async sendChatMessage(req: ChatRequest, attachments?: File[]) {
        if (!this.streamId) throw new Error("Connect first (no streamId).");

        const payload = {
            message: {
                message: req.message,
                chat_history: req.chat_history || [],
                project: req.project || this.project,
                tenant: req.tenant || this.tenant,
                turn_id: req.turn_id || `turn_${Date.now()}`,
                conversation_id: req.conversation_id,
                ...(req.bundle_id ? { bundle_id: req.bundle_id } : {}),
            },
            attachment_meta: [] as { filename: string }[],
        };

        const url = new URL(`${this.baseUrl}/sse/chat`);
        url.searchParams.set("stream_id", this.streamId);

        const headers: HeadersInit = {};
        if (this.authToken) headers["Authorization"] = `Bearer ${this.authToken}`;
        // cookies included automatically when same-origin + CORS allows

        if (attachments && attachments.length) {
            const form = new FormData();
            form.set("message", JSON.stringify(payload));
            const meta = attachments.map((f) => ({ filename: f.name }));
            form.set("attachment_meta", JSON.stringify(meta));
            attachments.forEach((f) => form.append("files", f, f.name));

            const res = await fetch(url, {
                method: "POST",
                headers,
                body: form,
                credentials: "include",
            });
            if (!res.ok) {
                throw new Error(`sse/chat failed (${res.status}) ${await res.text()}`);
            }
            return res.json();
        } else {
            headers["Content-Type"] = "application/json";
            const res = await fetch(url, {
                method: "POST",
                headers,
                body: JSON.stringify(payload),
                credentials: "include",
            });
            if (!res.ok) {
                throw new Error(`sse/chat failed (${res.status}) ${await res.text()}`);
            }
            return res.json();
        }
    }
}
