import {
    ChatBase,
    ChatCompleteEnvelope,
    ChatDeltaEnvelope,
    ChatErrorEnvelope,
    ChatOptions,
    ChatRequest, ChatServiceEnvelope,
    ChatStartEnvelope,
    ChatStepEnvelope
} from "./chatBase.ts";
import {Manager, Socket} from "socket.io-client";

type EngineKey = string;
const managers = new Map<EngineKey, Manager>();

function getManager(baseUrl: string, path = "/socket.io", opts: Partial<Manager["opts"]> = {}): Manager {
    const key: EngineKey = `${baseUrl}|${path}`;
    let m = managers.get(key);
    if (!m) {
        m = new Manager(baseUrl, {
            path,
            transports: ["websocket", "polling"],
            upgrade: false,
            autoConnect: false,
            withCredentials: true,
            ...opts,
        });
        managers.set(key, m);
    } else {
        Object.assign(m.opts, opts);
    }
    return m;
}

interface SessionInfoEnvelope {
    session_id: string;
    user_type: string;
    roles: string[];
}

interface SocketIOChatOptions extends ChatOptions {
    baseUrl: string;
    timeout?: number;
    path?: string;
    namespace?: string;
}

class SocketIOChat extends ChatBase {
    private _manager: Manager;
    private _socket: Socket;
    private readonly _baseUrl: string;
    private readonly _path: string;
    private readonly _namespace: string;
    private readonly _timeout: number;
    private _connecting = false;

    constructor(options: SocketIOChatOptions) {
        super(options);
        this._baseUrl = options.baseUrl;
        this._path = options.path ?? "/socket.io";
        this._namespace = options.namespace ?? "";
        this._timeout = options.timeout === undefined ? 10000 : options.timeout;

        this._manager = getManager(this._baseUrl, this._path, {
            reconnectionAttempts: 1000,
            timeout: this._timeout,
        });
        this._socket = this._manager.socket(this._namespace, {auth: {}});
    }

    public override connect(sessionId?: string | null): void {
        new Promise<void>((resolve) => {
            if (this._socket.connected) {
                console.info("Already connected");
                return;
            }

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

            this._connecting = true;

            this._sessionId = sessionId;

            const authPayload: Record<string, unknown> = {
                user_session_id: this._sessionId,
                project: this._project,
                tenant: this._tenant,
            };
            if (this._authToken) {
                authPayload.bearer_token = this._authToken;
                authPayload.id_token = this._idToken;
            }

            this._socket.auth = authPayload;
            this._socket.io.opts.query = {...this._socket.io.opts?.query, ...authPayload};

            this.bindHandlers();

            const t = setTimeout(() => {
                if (this._connecting) {
                    this._connecting = false;
                    this._socket.disconnect();
                    console.error("Socket.IO connection timed out. Retrying");
                    this._eventHandlers?.onConnectError?.(new Error("Connection timed out"));
                }
            }, this._timeout);

            const ok = () => {
                clearTimeout(t);
                console.warn("ok");
                this._socket.off("connect_error", bad);
            };

            const bad = (e: Error) => {
                clearTimeout(t);
                console.error(e);
                this._socket.off("connect", ok);
            };

            this._socket.once("connect", ok);
            this._socket.once("connect_error", bad);

            this._socket.connect();
            resolve();
        }).catch((err) => {
            this._eventHandlers?.onConnectError?.(err instanceof Error ? err : new Error(String(err?.message || err)));
        })
    }

    public override disconnect() {
        this._socket.disconnect();
    }

    private bindHandlers() {
        const bind = <T>(event: string, cb?: (d: T) => void) => {
            if (!cb) return;
            this._socket.on(event, (d) => {
                cb(d)
            });
        };

        this._socket.off();

        this._socket.on("connect", () => {
            this._connecting = false;
            console.info("✅ Chat Socket.IO connected:", this._socket.id);
            this._eventHandlers?.onConnect?.();
        });
        this._socket.on("disconnect", (reason: string) => {
            console.info("⚠️ Chat Socket.IO disconnected:", reason);
            this._eventHandlers?.onDisconnect?.(reason);
        });
        this._socket.on("connect_error", (err: Error | { message: string }) => {
            this._connecting = false;
            console.info("❌ Chat Socket.IO connect error:", err);
            this._eventHandlers?.onConnectError?.(err instanceof Error ? err : new Error(String(err?.message || err)));
        });

        bind<ChatStartEnvelope>("chat_start", this._eventHandlers?.onChatStart);
        bind<ChatDeltaEnvelope>("chat_delta", this._eventHandlers?.onChatDelta);
        bind<ChatStepEnvelope>("chat_step", this._eventHandlers?.onChatStep);
        bind<ChatCompleteEnvelope>("chat_complete", this._eventHandlers?.onChatComplete);
        bind<ChatErrorEnvelope>("chat_error", this._eventHandlers?.onChatError);
        bind<ChatServiceEnvelope>("chat_service", this._eventHandlers?.onChatService);

        bind<SessionInfoEnvelope>("session_info", (info: SessionInfoEnvelope) => {
            if (info?.session_id) this._sessionId = info.session_id;
            this._eventHandlers?.onSessionInfo?.({
                session_id: info?.session_id,
                user_type: info?.user_type,
                roles: Array.isArray(info?.roles) ? info.roles : undefined,
            });
        });

        // before each automatic reconnect, refresh the auth payload
        bind<void>("reconnect_attempt", () => {
            try {
                const authPayload: Record<string, unknown> = {
                    user_session_id: this._sessionId,
                    project: this._project,
                    tenant: this._tenant,
                };
                if (this._authToken) {
                    authPayload.bearer_token = this._authToken;
                    authPayload.id_token = this._authToken;
                }
                this._socket.auth = authPayload;
                this._socket.io.opts.query = {
                    ...this._socket.io.opts?.query,
                    ...authPayload,
                };
            } catch {
                // ignore
            }
        });
    }

    public override async sendChatMessage(conversationId: string, req: ChatRequest, attachments?: File[]) {
        if (!this._socket.connected)
            throw new Error("Socket not connected. Call connect() first.");
        if (!conversationId)
            throw new Error("No ConversationId");

        const message = {...req, conversation_id: conversationId};
        attachments = attachments ? attachments : [];
        console.log("📤 Emitting chat_message:", message, attachments);

        const files = []

        for (const attachment of attachments) {
            const data = await attachment.arrayBuffer()
            files.push({name: attachment.name, data})
        }

        const attachment_meta = files.map(value => {
            return {filename: value.name}
        })

        const data = files.map(value => value.data)
        this._socket.emit("chat_message", {message, attachment_meta}, ...data);
    }

    public override async requestConvStatus(conversationId: string) {
        if (!this._socket.connected) throw new Error("Socket not connected. Call connect() first.");
        this._socket.emit("conv_status.get", {conversation_id: conversationId});
    }

}

export default SocketIOChat;