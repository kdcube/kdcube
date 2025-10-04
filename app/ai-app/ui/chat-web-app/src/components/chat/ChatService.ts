/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

// ChatService.ts
import {Manager, Socket} from "socket.io-client";
import {getChatBaseAddress} from "../../AppConfig.ts";
import {v4 as uuidv4} from "uuid";
import type {AuthContextValue} from "../auth/AuthManager.tsx";

let __chatSingleton: SocketChatService | null = null;

export function getChatServiceSingleton(opts: SocketChatOptions): SocketChatService {
    if (!__chatSingleton) __chatSingleton = new SocketChatService(opts);
    else __chatSingleton.updateOptions(opts);
    (window as any).__chatSvc = __chatSingleton; // handy for debugging
    return __chatSingleton;
}

/* =========================
   v1 Socket Envelope Types
   ========================= */

export type V1Status = "started" | "running" | "completed" | "error" | "skipped";

export interface ConversationInfo {
    session_id: string;
    conversation_id: string;
    turn_id: string;
}

export interface V1BaseEnvelope {
    type: "chat.start" | "chat.step" | "chat.delta" | "chat.complete" | "chat.error";
    timestamp: string; // ISO-8601
    service: {
        request_id: string;
        tenant?: string | null;
        project?: string | null;
        user?: string | null;
    };
    conversation: ConversationInfo;
    event: {
        agent?: string | null;
        step: string;
        status: V1Status;
        title?: string | null;
    };
    data?: Record<string, unknown>;
}

export interface ChatStartEnvelope extends V1BaseEnvelope {
    type: "chat.start";
    data: { message: string; queue_stats?: Record<string, unknown> };
}

export interface ChatStepEnvelope extends V1BaseEnvelope {
    type: "chat.step";
    data: Record<string, unknown>;
}

export interface ChatDeltaEnvelope extends V1BaseEnvelope {
    type: "chat.delta";
    delta: {
        text: string;
        marker: "thinking" | "answer" | string;
        index: number,
        completed?: boolean;
    };
}

export interface ChatCompleteEnvelope extends V1BaseEnvelope {
    type: "chat.complete";
    data: {
        final_answer: string;
        followups?: string[];
        selected_model?: string;
        config_info?: Record<string, any>;
        [k: string]: any;
    };
}

export interface ChatErrorEnvelope extends V1BaseEnvelope {
    type: "chat.error";
    data: { error: string; [k: string]: any };
}

/* ================
   Chat UI helpers
   ================ */

export interface ChatMessage {
    role: "user" | "assistant";
    content: string;
    timestamp?: string;
    id: number;
}

export type WireChatMessage = ChatMessage;

export interface ChatRequest {
    message: string;
    chat_history: WireChatMessage[];
    project?: string;
    tenant?: string;
    // we forward this to the server for routing
    turn_id?: string;
    bundle_id?: string;
}

/* ==================
   Event handler API
   ================== */

export interface SessionInfo {
    session_id: string;
    user_type: string;
    roles?: string[];
}

export interface ChatEventHandlers {
    onConnect?: () => void;
    onDisconnect?: (reason: string) => void;
    onConnectError?: (error: Error) => void;

    onChatStart?: (env: ChatStartEnvelope) => void;
    onChatDelta?: (env: ChatDeltaEnvelope) => void;
    onChatStep?: (env: ChatStepEnvelope) => void;
    onChatComplete?: (env: ChatCompleteEnvelope) => void;
    onChatError?: (env: ChatErrorEnvelope) => void;

    onPong?: (data: { timestamp: Date }) => void;
    onSessionInfo?: (info: SessionInfo) => void;
}

/* ==================
   Connection options
   ================== */

export interface SocketChatOptions {
    baseUrl: string; // origin only (e.g. http://localhost:5005)
    path?: string; // defaults to '/socket.io'
    reconnectionAttempts?: number;
    timeout?: number;
    project?: string;
    tenant?: string;
    namespace?: string; // defaults to '/'
    authContext: AuthContextValue;
}

/* ===========
   IO Manager
   =========== */

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

/* ===============
   Chat Service
   =============== */

export class SocketChatService {
    private readonly baseUrl: string;
    private options: Required<SocketChatOptions>;
    private manager: Manager;
    private socket: Socket;
    private isConnecting = false;
    private isConnected = false;
    private eventHandlers: ChatEventHandlers = {};
    private currentSessionId?: string;
    private currentUserRoles: string[] = [];
    private connectingPromise: Promise<Socket> | null = null;
    private conversationID: string = uuidv4();

    constructor(options: SocketChatOptions) {
        this.baseUrl = options.baseUrl;
        this.options = {
            baseUrl: this.baseUrl,
            path: options.path ?? "/socket.io",
            reconnectionAttempts: options.reconnectionAttempts ?? 10,
            timeout: options.timeout ?? 10000,
            project: options.project,
            tenant: options.tenant,
            namespace: options.namespace ?? "/",
            authContext: options.authContext,
        } as Required<SocketChatOptions>;
        this.manager = getManager(this.baseUrl, this.options.path, {
            reconnectionAttempts: this.options.reconnectionAttempts,
            timeout: this.options.timeout,
        });
        this.socket = this.manager.socket(this.options.namespace, {auth: {}});
    }

    public newConversation() {
        this.conversationID = uuidv4();
    }

    public updateOptions(next: SocketChatOptions) {
        this.options = {
            ...this.options,
            ...next,
            path: next.path ?? this.options.path,
            reconnectionAttempts: next.reconnectionAttempts ?? this.options.reconnectionAttempts,
            timeout: next.timeout ?? this.options.timeout,
            namespace: next.namespace ?? this.options.namespace,
            authContext: next.authContext ?? this.options.authContext,
        } as Required<SocketChatOptions>;
        this.manager = getManager(this.baseUrl, this.options.path, {
            reconnectionAttempts: this.options.reconnectionAttempts,
            timeout: this.options.timeout,
        });
        if (this.socket.nsp !== this.options.namespace) {
            this.socket.off();
            this.socket.disconnect();
            this.socket = this.manager.socket(this.options.namespace, {auth: {}});
        }
    }

    private bindHandlers() {
        this.socket.off();

        this.socket.on("connect", () => {
            this.isConnected = true;
            this.isConnecting = false;
            console.log("✅ Chat Socket.IO connected:", this.socket.id);
            this.eventHandlers.onConnect?.();
        });
        this.socket.on("disconnect", (reason: string) => {
            this.isConnected = false;
            console.log("⚠️ Chat Socket.IO disconnected:", reason);
            this.eventHandlers.onDisconnect?.(reason);
        });
        this.socket.on("connect_error", (err: any) => {
            this.isConnecting = false;
            console.log("❌ Chat Socket.IO connect error:", err);
            this.eventHandlers.onConnectError?.(err instanceof Error ? err : new Error(String(err?.message || err)));
        });

        // v1 envelopes — pass through as-is
        this.socket.on("chat_start", (env: ChatStartEnvelope) => this.eventHandlers.onChatStart?.(env));
        this.socket.on("chat_delta", (env: ChatDeltaEnvelope) => this.eventHandlers.onChatDelta?.(env));
        this.socket.on("chat_step", (env: ChatStepEnvelope) => this.eventHandlers.onChatStep?.(env));
        this.socket.on("chat_complete", (env: ChatCompleteEnvelope) => this.eventHandlers.onChatComplete?.(env));
        this.socket.on("chat_error", (env: ChatErrorEnvelope) => this.eventHandlers.onChatError?.(env));

        this.socket.on("pong", (d) => this.eventHandlers.onPong?.(d));
        this.socket.on("session_info", (info: any) => {
            if (info?.session_id) this.currentSessionId = info.session_id;
            if (Array.isArray(info?.roles)) this.currentUserRoles = info.roles;
            this.eventHandlers.onSessionInfo?.({
                session_id: info?.session_id,
                user_type: info?.user_type,
                roles: Array.isArray(info?.roles) ? info.roles : undefined,
            });
        });

        // before each automatic reconnect, refresh the auth payload
        this.socket.io.on("reconnect_attempt", () => {
            try {
                const ctx = this.options.authContext;
                const authPayload: any = {
                    user_session_id: this.currentSessionId,
                    project: this.options.project,
                    tenant: this.options.tenant,
                };
                if (ctx && ctx.getUserAuthToken()) {
                    authPayload.bearer_token = ctx.getUserAuthToken();
                    authPayload.id_token = ctx.getUserIdToken();
                }
                this.socket.auth = authPayload;
                (this.socket.io as any).opts.query = {
                    ...(this.socket.io as any).opts?.query,
                    ...authPayload,
                };
            } catch {
                // ignore
            }
        });
    }

    private async fetchProfile(authContext: AuthContextValue): Promise<{
        session_id: string;
        user_type: string;
        roles: string[];
    }> {
        const headers: HeadersInit = [["Content-Type", "application/json"]];
        authContext.appendAuthHeader(headers);
        const r = await fetch(`${getChatBaseAddress()}/profile`, {
            headers,
            credentials: "include" as RequestCredentials,
        });
        if (!r.ok) throw new Error(`Profile fetch failed (${r.status})`);
        const j = await r.json();
        if (!j.session_id) throw new Error("Profile missing session_id");
        this.currentSessionId = j.session_id;
        const roles = Array.isArray(j.roles) ? j.roles : [];
        this.currentUserRoles = roles;
        return {session_id: j.session_id, user_type: j.user_type, roles};
    }

    public async connect(handlers: ChatEventHandlers = {}, authContext?: AuthContextValue): Promise<Socket> {
        if (this.socket.connected) {
            this.eventHandlers = handlers;
            this.bindHandlers();
            return this.socket;
        }
        if (this.connectingPromise) {
            this.eventHandlers = handlers;
            this.bindHandlers();
            return this.connectingPromise;
        }

        this.isConnecting = true;
        this.eventHandlers = handlers;

        this.connectingPromise = (async () => {
            try {
                const ctx = authContext ?? this.options.authContext;
                const {session_id} = await this.fetchProfile(ctx);

                const authPayload: any = {
                    user_session_id: session_id,
                    project: this.options.project,
                    tenant: this.options.tenant,
                };
                if (ctx && ctx.getUserAuthToken()) {
                    authPayload.bearer_token = ctx.getUserAuthToken();
                    authPayload.id_token = ctx.getUserIdToken();
                }

                this.socket.auth = authPayload;
                (this.socket.io as any).opts.query = {...(this.socket.io as any).opts?.query, ...authPayload};

                this.bindHandlers();
                this.socket.connect();

                const t = setTimeout(() => {
                    if (this.isConnecting) {
                        this.isConnecting = false;
                        this.socket.disconnect();
                    }
                }, this.options.timeout);

                await new Promise<void>((resolve, reject) => {
                    const ok = () => {
                        clearTimeout(t);
                        this.socket.off("connect_error", bad);
                        resolve();
                    };
                    const bad = (e: any) => {
                        clearTimeout(t);
                        this.socket.off("connect", ok);
                        reject(e instanceof Error ? e : new Error(String(e?.message || e)));
                    };
                    this.socket.once("connect", ok);
                    this.socket.once("connect_error", bad);
                });

                // this.connectingPromise = null;
                return this.socket;
            } finally {
                // allow follow-up attempts after success or error
                this.connectingPromise = null;
            }
        })();

        return this.connectingPromise;
    }

    public disconnect() {
        this.socket.off();
        this.socket.disconnect();
        this.isConnected = false;
        this.isConnecting = false;
        this.connectingPromise = null;
    }

    public sendChatMessage(req: {
        message: string;
        chat_history: any[];
        project?: string;
        tenant?: string;
        turn_id?: string;
        bundle_id?: string
    }, attachments?: File[]) {
        if (!this.socket.connected) throw new Error("Socket not connected. Call connect() first.");
        const message = {...req, conversation_id: this.conversationID}
        attachments = attachments ? attachments : [];
        console.log("📤 Emitting chat_message:", message, attachments);

        Promise.all(attachments.map(async value => {
            const data = await value.arrayBuffer()
            return {name: value.name, data}
        })).then(values => {
            const attachment_meta = values.map(value => {return {filename: value.name}})
            const data = values.map(value => value.data)
            this.socket.emit("chat_message", {message, attachment_meta}, ...data);
        }).catch((e: unknown) => {
            console.error(e);
        })

    }

    public ping() {
        if (!this.socket.connected) throw new Error("Socket not connected. Call connect() first.");
        this.socket.emit("ping", {timestamp: new Date().toISOString()});
    }

    public get connected() {
        return this.isConnected && this.socket.connected;
    }

    public get socketId() {
        return this.socket.id;
    }

    public get sessionId(): string | undefined {
        return this.currentSessionId;
    }

    public get userRoles(): string[] {
        return this.currentUserRoles;
    }
}

/* =======================
   Suggested questions API
   ======================= */

const makeHeaders = (authContext?: AuthContextValue, base?: HeadersInit): Headers => {
    const h = new Headers(base as any);
    if (authContext) authContext.appendAuthHeader(h);
    return h;
};

export const getSuggestedQuestions = async (tenant: string, project: string, authContext: AuthContextValue, bundleId?: string) => {
    const headers = makeHeaders(authContext, {"Content-Type": "application/json"});
    console.log(Array.from(headers.entries()));
    const res = await fetch(
        `${getChatBaseAddress()}/integrations/bundles/${tenant}/${project}/operations/suggestions`,
        {method: "POST", headers, body: JSON.stringify({"bundle_id": bundleId})}
    );
    if (!res.ok) {
        throw new Error("Failed to get suggested questions");
    }
    const data = await res.json();
    return data.suggestions;
};

export const getResourceByRN = async (rn: string, authContext: AuthContextValue) => {
    const headers = makeHeaders(authContext, {"Content-Type": "application/json"});
    const res = await fetch(
        `${getChatBaseAddress()}/api/cb/resources/by-rn`,
        {method: "POST", headers, body: JSON.stringify({rn: rn})}
    );
    if (!res.ok) {
        throw new Error("Failed to get resource by rn");
    }
    return await res.json();
};

export const downloadBlob = async (path: string, authContext: AuthContextValue) => {
    const headers = makeHeaders(authContext, {});
    const res = await fetch(
        `${getChatBaseAddress()}${path}`,
        {headers}
    );
    if (!res.ok) {
        throw new Error("Failed to get resource by rn");
    }
    return await res.blob()
};

/* =======================
   Hook return type (used by Chat.tsx)
   ======================= */

export interface UseSocketChatReturn {
    isConnected: boolean;
    isConnecting: boolean;
    socketId?: string;
    connect: (handlers: ChatEventHandlers, authContext?: AuthContextValue) => Promise<void>;
    disconnect: () => void;
    sendMessage: (request: ChatRequest, attachments?: File[]) => void;
    ping: () => void;
    connectionError: string | null;
}
