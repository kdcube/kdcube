import {
    ChatBase,
    ChatCompleteEnvelope,
    ChatDeltaEnvelope,
    ChatErrorEnvelope,
    ChatOptions,
    ChatRequest,
    ChatStartEnvelope,
    ChatStepEnvelope,
    ConvStatusEnvelope
} from "./chatBase.ts";

import {v4 as uuidv4} from "uuid";

interface SSEChatOptions extends ChatOptions {
    baseUrl: string;
}

class SSEChat extends ChatBase {
    private _eventSource: EventSource | null = null;
    private readonly _baseUrl: string;
    private _streamId: string | null = null;
    _connecting = false;

    constructor(options: SSEChatOptions) {
        super(options);
        this._baseUrl = options.baseUrl;
    }

    private constructEventSource(): void {
        this._streamId = uuidv4();

        const url = new URL(`${this._baseUrl}/sse/stream`);
        url.searchParams.set("user_session_id", this._sessionId ?? this._sessionId!);
        url.searchParams.set("stream_id", this._streamId!);
        if (this.tenant) url.searchParams.set("tenant", this.tenant);
        if (this.project) url.searchParams.set("project", this.project);

        if (this._authToken) url.searchParams.set("bearer_token", this._authToken);
        if (this._idToken) url.searchParams.set("id_token", this._idToken);

        this._eventSource = new EventSource(url.toString(), {
            withCredentials: true,
        });

        this._eventSource.addEventListener("open", () => {
            this._connecting = false;
            console.info("[sse.stream] opened", {
                sessionId: this._sessionId,
                streamId: this._streamId,
                ts: new Date().toISOString(),
            })
            this._eventHandlers?.onConnect?.()
        })

        this._eventSource.addEventListener("error", (ev) => {
            if (this._connecting) {
                this._eventHandlers?.onConnectError?.(new Error(String(ev)))
            } else {
                this._eventHandlers?.onDisconnect?.(String(ev))
            }
        })

        const bind = <T = unknown>(event: string, cb?: (d: T) => void) => {
            if (!cb) return;
            this._eventSource!.addEventListener(event, (e: MessageEvent) => {
                try {
                    cb(JSON.parse(e.data));
                } catch (e) {
                    console.error("Malformed event", e);
                }
            });
        };

        bind<ChatStartEnvelope>("chat_start", this._eventHandlers?.onChatStart);
        bind<ChatStepEnvelope>("chat_step", this._eventHandlers?.onChatStep);
        bind<ChatDeltaEnvelope>("chat_delta", this._eventHandlers?.onChatDelta);
        bind<ChatCompleteEnvelope>("chat_complete", this._eventHandlers?.onChatComplete);
        bind<ChatErrorEnvelope>("chat_error", this._eventHandlers?.onChatError);
        bind<ConvStatusEnvelope>("conv_status", this._eventHandlers?.onConvStatus);
    }

    public override connect(sessionId?: string | null): void {
        new Promise<void>(resolve => {

            if (this._connecting) {
                console.info("Already connecting");
                return;
            }

            sessionId = sessionId ?? this._sessionId

            if (!sessionId) {
                this._eventHandlers?.onConnectError?.(new Error("No sessionId provided"));
                this._connecting = false;
                return;
            }

            this._sessionId = sessionId;

            this.constructEventSource()

            resolve();
        }).catch((err)=>{
            this._eventHandlers?.onConnectError?.(err);
            this._connecting = false;
        })
    }

    public override async sendChatMessage(conversationId: string, req: ChatRequest, attachments?: File[] | null): Promise<void> {
        if (!this._streamId) throw new Error("no streamId provided");
        console.info("[sse.chat] send", {
            sessionId: this._sessionId,
            streamId: this._streamId,
            conversationId,
            turnId: req.turn_id,
            attachments: attachments?.length || 0,
            ts: new Date().toISOString(),
        })

        const payload = {
            message: {
                message: req.message,
                chat_history: req.chat_history || [],
                project: req.project || this.project,
                tenant: req.tenant || this.tenant,
                turn_id: req.turn_id || `turn_${Date.now()}`,
                conversation_id: conversationId,
                ...(req.bundle_id ? {bundle_id: req.bundle_id} : {}),
            },
            attachment_meta: [] as { filename: string }[],
        };

        const baseForUrl = this._baseUrl || window.location.origin;
        const url = new URL(`${baseForUrl}/sse/chat`);
        url.searchParams.set("stream_id", this._streamId);

        const makeHeaders = (base?: HeadersInit): Headers => {
            return this.addTZHeader(this.addCredentialsHeader(base));
        };

        if (attachments && attachments.length) {
            const form = new FormData();
            form.set("message", JSON.stringify(payload));
            const meta = attachments.map((f) => ({filename: f.name}));
            form.set("attachment_meta", JSON.stringify(meta));
            attachments.forEach((f) => form.append("files", f, f.name));

            const headers = makeHeaders(); // auth headers, no content-type for FormData

            const res = await fetch(url, {method: "POST", headers, body: form, credentials: "include"});
            if (!res.ok) throw new Error(`sse/chat failed (${res.status}) ${await res.text()}`);
            return res.json();
        } else {
            const headers = makeHeaders({"Content-Type": "application/json"});
            const res = await fetch(url, {
                method: "POST",
                headers,
                body: JSON.stringify(payload),
                credentials: "include"
            });
            if (!res.ok) throw new Error(`sse/chat failed (${res.status}) ${await res.text()}`);
            return res.json();
        }
    }

    public async requestConvStatus(conversationId: string) {
        const baseForUrl = this._baseUrl || window.location.origin;
        const url = new URL(`${baseForUrl}/sse/conv_status.get`);

        const res = await fetch(url, {
            method: "POST",
            headers: this.addCredentialsHeader([["Content-Type", "application/json"]]),
            credentials: "include",
            body: JSON.stringify({conversation_id: conversationId, stream_id: this._streamId }),
        });
        if (!res.ok) throw new Error(`/sse/conv_status.get failed (${res.status})`);
        return res.json();
    }
}

export default SSEChat;
