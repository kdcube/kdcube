import {ISOTimestamped, Timestamped} from "../../types/common.ts";
import {getClientTimezone} from "../../utils/dateTimeUtils.ts";

export type EventStatus = "started" | "running" | "completed" | "error" | "skipped";

export interface ConversationInfo {
    session_id: string;
    conversation_id: string;
    turn_id: string;
}

export interface BaseEnvelope extends ISOTimestamped {
    type: "chat.start" | "chat.step" | "chat.delta" | "chat.complete" | "chat.error";
    service: {
        request_id: string;
        tenant?: string | null;
        project?: string | null;
        user?: string | null;
    };
    conversation: ConversationInfo;
    event: {
        agent?: string | null;
        step: "files" | "citations" | string;
        status: EventStatus;
        title?: string | null;
        markdown?: string;
    };
    data?: Record<string, unknown>;
}

export interface ChatStartEnvelope extends BaseEnvelope {
    type: "chat.start";
    data: { message: string; queue_stats?: Record<string, unknown> };
}

export interface ChatStepEnvelope extends BaseEnvelope {
    type: "chat.step";
    data: Record<string, unknown>;
}

export interface RNFile extends Timestamped {
    filename: string,
    rn: string,
    mime?: string | null,
    description?: string | null,
}

export interface FilesStepEnvelope extends BaseEnvelope {
    type: "chat.step";
    data?: {
        items?: RNFile[] | null;
    };
}

export interface RichLink {
    url: string,
    title?: string | null,
    body?: string | null,
    favicon?: string | null,
}

export interface CitationsStepEnvelope extends BaseEnvelope {
    type: "chat.step";
    data?: {
        items?: RichLink[] | null;
    };
}

export interface ChatDeltaEnvelope extends BaseEnvelope {
    type: "chat.delta";
    delta: {
        text: string;
        marker: "thinking" | "answer" | "tool" | string;
        index: number,
        completed?: boolean;
    };
    extra: Record<string, unknown>;
}

export interface ChatCompleteEnvelope extends BaseEnvelope {
    type: "chat.complete";
    data: {
        final_answer: string;
        followups?: string[];
        selected_model?: string;
        config_info?: Record<string, unknown>;
        [k: string]: unknown;
    };
}

export interface ConvStatusEnvelope {
    type: "conv.status";
    timestamp: string;
    service?: { request_id?: string | null; tenant?: string | null; project?: string | null; user?: string | null };
    conversation: ConversationInfo;
    event: { step: "conv.state"; status: "idle" | "in_progress" | "error" };
    data: { state: "idle" | "in_progress" | "error"; updated_at: string; current_turn_id?: string | null };
}

export type RateLimitEventStep = "rate_limit.warning"
    | "rate_limit.denied"
    | "rate_limit.snapshot"; // optional, if you want a periodic push

export interface RateLimitLimits {
    requests_per_day?: number | null;
    requests_per_month?: number | null;
    total_requests?: number | null;
    tokens_per_hour?: number | null;
    tokens_per_day?: number | null;
    tokens_per_month?: number | null;
    max_concurrent?: number | null;
}

export type RateLimitRemaining = RateLimitLimits

export interface RateLimitSnapshot {
    req_hour?: number;
    req_day?: number;
    req_month?: number;
    req_total?: number;
    tok_hour?: number;
    tok_day?: number;
    tok_month?: number;
    in_flight?: number;
}

export interface RateLimitPayload {
    bundle_id: string;
    subject_id: string;
    user_type: string;

    limits: RateLimitLimits;
    remaining: RateLimitRemaining;

    violations: string[];
    messages_remaining: number | null;

    retry_after_sec: number | null;
    retry_scope: "hour" | "day" | "month" | "total" | null;

    retry_after_hours?: number | null;

    snapshot?: RateLimitSnapshot;
    reason?: string | null;
}

export interface ChatServiceEnvelope {
    type: "chat.service";
    conversation?: ConversationInfo | null;
    event: {
        step: RateLimitEventStep;        // 👈 kind of service event
        status: "started" | "running" | "completed" | "error" | "skipped";               // "running" | "error" | ...
        title?: string | null;
        agent?: string | null;          // e.g. "bundle.rate_limiter"
        scope?: "user" | "project" | "tenant" | "bundle";
    };
    data: {
        rate_limit: RateLimitPayload;
        [k: string]: unknown;
    };
}

export interface ChatErrorEnvelope extends BaseEnvelope {
    type: "chat.error";
    data: { error: string; [k: string]: unknown };
}

export interface ChatMessage {
    role: "user" | "assistant";
    content: string;
    timestamp?: string;
    id: number;
}

export interface ChatRequest {
    message: string;
    chat_history?: ChatMessage[] | null;
    project?: string;
    tenant?: string;
    // we forward this to the server for routing
    turn_id?: string;
    bundle_id?: string;
}

interface StepData {
    message?: string;

    [key: string]: unknown;
}

export interface TurnStep extends Timestamped {
    step: string;
    title?: string | null;
    status: EventStatus;
    error?: string;
    data?: StepData;
    markdown?: string;
    agent?: string | null;
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
    onConvStatus?: (env: ConvStatusEnvelope) => void;
    onSessionInfo?: (info: SessionInfo) => void;
    onChatService?: (env: ChatServiceEnvelope) => void;
}

export interface ChatOptions {
    project: string | undefined | null;
    tenant: string | undefined | null;
    eventHandlers?: ChatEventHandlers;
    autoReconnect?: boolean;
    authToken?: string | null;
    idToken?: string | null;
    idHeaderName?: string | null;
}

export abstract class ChatBase {
    protected _project: string | undefined | null;
    protected _tenant: string | undefined | null;
    protected _eventHandlers?: ChatEventHandlers;
    protected _autoReconnect?: boolean;
    protected _authToken?: string | null;
    protected _idToken?: string | null;
    protected _sessionId?: string | null;
    protected _idHeaderName?: string | null;

    protected constructor(options: ChatOptions) {
        this._project = options.project;
        this._tenant = options.tenant;
        this._eventHandlers = options.eventHandlers;
        this._autoReconnect = options.autoReconnect;
        this._authToken = options.authToken;
        this._idToken = options.idToken;
        this._idHeaderName = options.idHeaderName;
    }

    public get sessionId(): string | null | undefined {
        return this._sessionId
    }

    public set sessionId(value: string | null | undefined) {
        this._sessionId = value;
    }

    public set authToken(authToken: string | null | undefined) {
        this._authToken = authToken;
    }

    public set idToken(idToken: string | null | undefined) {
        this._idToken = idToken;
    }

    public set tenant(tenant: string | undefined) {
        this._tenant = tenant;
    }

    public set project(project: string | undefined) {
        this._project = project;
    }

    public set eventHandlers(eventHandlers: ChatEventHandlers) {
        this._eventHandlers = eventHandlers;
    }

    public disconnect() {
        throw new Error("Method not implemented.");
    }

    public get connected(): boolean {
        throw new Error("Method not implemented.");
    }

    // @ts-expect-error because it's an abstract class
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    public async sendChatMessage(conversationId: string, req: ChatRequest, attachments?: File[] | null) {
        throw new Error("Method not implemented.");
    }

    // @ts-expect-error because it's an abstract class
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    public connect(sessionId?: string | null) {
        throw new Error("Method not implemented.");
    }

    // @ts-expect-error because it's an abstract class
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    public async requestConvStatus(conversationId: string) {
        throw new Error("Method not implemented.");
    }

    protected addAuthHeader(base?: HeadersInit): Headers {
        const h = new Headers(base);
        if (this._authToken) h.set("Authorization", `Bearer ${this._authToken}`);
        return h;
    };

    protected addIdHeader(base?: HeadersInit): Headers {
        const h = new Headers(base);
        if (this._idToken && this._idHeaderName) h.set("id_token", this._idHeaderName);
        return h;
    };

    protected addTZHeader(base?: HeadersInit): Headers {
        const h = new Headers(base);
        const tz = getClientTimezone()
        if (tz.tz) h.set("X-User-Timezone", tz.tz);
        h.set("X-User-UTC-Offset", String(tz.utcOffsetMin));
        return h;
    };

}