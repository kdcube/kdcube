import {ISOTimestamped, Timestamped} from "../../types/common.ts";
import {getClientTimezone} from "../../utils/dateTimeUtils.ts";
import {ChatServiceEnvelope} from "../chat/serviceEventTypes.ts";
import {createClientTurnId} from "../../utils/clientIds.ts";

export type EventStatus = "started" | "running" | "completed" | "error" | "skipped";

export interface ConversationInfo {
    session_id: string;
    conversation_id: string;
    turn_id: string;
}

export interface BaseEnvelope extends ISOTimestamped {
    type: "chat.start" | "chat.step" | "chat.delta" | "chat.complete" | "chat.error" | "chat.compaction";
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

export interface ChatCompactionEnvelope extends BaseEnvelope {
    type: "chat.compaction";
    data: {
        compaction_id?: string;
        status?: EventStatus;
        kind?: string;
        reason?: string;
        before_tokens?: number;
        after_tokens?: number;
        compacted_tokens?: number;
        compacted_blocks?: number;
        inserted_blocks?: number;
        split_turn?: boolean;
        split_turn_id?: string;
        current_turn?: boolean;
        [k: string]: unknown;
    };
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

export interface ChatErrorEnvelope extends BaseEnvelope {
    type: "chat.error";
    data: { error: string; [k: string]: unknown };
}

export interface ChatMessageSendResponse {
    ok?: boolean;
    status: string;
    task_id: string;
    session_id: string;
    conversation_id: string;
    turn_id?: string;
    active_turn_id?: string | null;
    target_turn_id?: string | null;
    queued_turn_id?: string | null;
    event_id?: string | null;
    external_event_sequence?: number | null;
    live_owner_detected?: boolean | null;
    conversation_created: number;
    user_type: string;
    is_continuation?: boolean | null;
    message: string;
}

export interface ChatSubmitErrorPayload {
    status?: number | string | null;
    error_type?: string | null;
    error?: string | null;
    reason?: string | null;
    retry_after?: number | null;
    queue_stats?: Record<string, unknown> | null;
    detail?: unknown;
    [k: string]: unknown;
}

const asRecord = (value: unknown): Record<string, unknown> | null => (
    value !== null && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : null
);

const optionalString = (value: unknown): string | null => (
    typeof value === "string" && value.trim() ? value : null
);

const optionalNumber = (value: unknown): number | null => {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim()) {
        const parsed = Number(value);
        if (Number.isFinite(parsed)) return parsed;
    }
    return null;
};

export const chatSubmitErrorMessage = (payload: ChatSubmitErrorPayload, fallback = "Chat request failed"): string => {
    const detail = asRecord(payload.detail);
    const source = detail || payload;
    const errorType = optionalString(source.error_type) || optionalString(payload.error_type);
    const serverError = optionalString(source.error) || optionalString(payload.error);
    const reason = optionalString(source.reason) || optionalString(payload.reason);

    if (errorType === "queue.enqueue_rejected") {
        if (serverError) return serverError;
        return reason
            ? `System under pressure - request rejected (${reason})`
            : "System under pressure - request rejected";
    }

    if (serverError) return serverError;
    if (typeof payload.detail === "string" && payload.detail.trim()) return payload.detail;
    return fallback;
};

export class ChatSubmitError extends Error {
    public readonly httpStatus?: number;
    public readonly errorType?: string | null;
    public readonly reason?: string | null;
    public readonly retryAfter?: number | null;
    public readonly queueStats?: Record<string, unknown> | null;
    public readonly raw: unknown;

    constructor(payload: ChatSubmitErrorPayload, fallback?: string) {
        super(chatSubmitErrorMessage(payload, fallback));
        this.name = "ChatSubmitError";
        const detail = asRecord(payload.detail);
        const source = detail || payload;
        this.httpStatus = optionalNumber(source.status) || optionalNumber(payload.status) || undefined;
        this.errorType = optionalString(source.error_type) || optionalString(payload.error_type);
        this.reason = optionalString(source.reason) || optionalString(payload.reason);
        this.retryAfter = optionalNumber(source.retry_after) || optionalNumber(payload.retry_after);
        this.queueStats = asRecord(source.queue_stats) || asRecord(payload.queue_stats);
        this.raw = payload;
    }
}

export const chatSubmitErrorFromResponse = async (res: Response): Promise<ChatSubmitError> => {
    const text = await res.text();
    let parsed: unknown = null;
    if (text) {
        try {
            parsed = JSON.parse(text);
        } catch {
            parsed = text;
        }
    }

    const body = asRecord(parsed);
    const detail = body?.detail ?? parsed;
    const detailRecord = asRecord(detail);
    const payload: ChatSubmitErrorPayload = detailRecord
        ? {...detailRecord, status: res.status}
        : {detail, error: typeof detail === "string" ? detail : null, status: res.status};

    return new ChatSubmitError(payload, `sse/chat failed (${res.status})`);
};

export const assertChatSubmitAccepted = (ack: unknown): ChatMessageSendResponse => {
    const payload = asRecord(ack);
    if (payload?.ok === false) {
        throw new ChatSubmitError(payload as ChatSubmitErrorPayload, "Chat request was rejected");
    }
    return ack as ChatMessageSendResponse;
};

export interface DataBusMessageInput {
    message_id?: string;
    subject: string;
    object_ref?: string;
    idempotency_key?: string;
    payload: Record<string, unknown>;
    client?: Record<string, unknown>;
    trace?: Record<string, unknown>;
    created_at?: string;
}

export interface DataBusPublishRequest {
    bundle_id: string;
    messages: DataBusMessageInput[];
}

export interface DataBusPublishAck {
    schema?: string;
    status: "accepted" | "partial" | "rejected" | string;
    accepted?: Array<Record<string, unknown>>;
    rejected?: Array<Record<string, unknown>>;
}

export interface ChatMessage {
    role: "user" | "assistant";
    content: string;
    timestamp?: string;
    id: number;
}

export interface ChatTarget {
    agent_id?: string;
    agent?: string;
    story_id?: string;
    story_kind?: string;
    surface?: string;
    [key: string]: unknown;
}

export interface ExternalEvent {
    event_id?: string;
    type?: string;
    event_source_id: string;
    logical_path?: string;
    hosted_uri?: string;
    reactive?: boolean;
    story_id?: string;
    agent_id?: string;
    payload?: {
        mime?: string;
        event?: unknown;
        event_ref?: string;
        iteration_credit?: number;
        [key: string]: unknown;
    };
    [key: string]: unknown;
}

export interface ChatRequest {
    message: string;
    chat_history?: ChatMessage[] | null;
    project?: string;
    tenant?: string;
    // we forward this to the server for routing
    turn_id?: string;
    bundle_id?: string;
    reactiveEventType?: string;
    active_turn_id?: string;
    target_turn_id?: string;
    payload?: Record<string, unknown>;
    target?: ChatTarget;
    external_events?: ExternalEvent[];
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
    onChatCompaction?: (env: ChatCompactionEnvelope) => void;
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
    streamIdHeaderName?: string | null;
}

export abstract class ChatBase {
    protected _project: string | undefined | null;
    protected _tenant: string | undefined | null;
    protected _eventHandlers?: ChatEventHandlers;
    protected _autoReconnect?: boolean;
    protected _authToken?: string | null;
    protected _idToken?: string | null;
    protected _sessionId?: string | null;
    protected _streamId: string | null = null;
    protected _streamIdHeaderName?: string | null;
    protected _idHeaderName?: string | null;

    protected constructor(options: ChatOptions) {
        this._project = options.project;
        this._tenant = options.tenant;
        this._eventHandlers = options.eventHandlers;
        this._autoReconnect = options.autoReconnect;
        this._authToken = options.authToken;
        this._idToken = options.idToken;
        this._idHeaderName = options.idHeaderName;
        this._streamIdHeaderName = options.streamIdHeaderName
    }

    public get sessionId(): string | null | undefined {
        return this._sessionId
    }

    public set sessionId(value: string | null | undefined) {
        this._sessionId = value;
    }

    public get streamId(): string | null {
        return this._streamId
    }

    public set streamId(value: string | null) {
        this._streamId = value;
    }

    get streamIdHeaderName(): string | null | undefined {
        return this._streamIdHeaderName;
    }

    set streamIdHeaderName(value: string | null) {
        this._streamIdHeaderName = value;
    }

    public set authToken(authToken: string | null | undefined) {
        this._authToken = authToken;
    }

    public set idToken(idToken: string | null | undefined) {
        this._idToken = idToken;
    }

    public get idHeaderName(): string | null | undefined {
        return this._idHeaderName;
    }

    public set idHeaderName(value: string | null) {
        this._idHeaderName = value;
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
    public async sendChatMessage(req: ChatRequest, attachments?: File[] | null, conversationId?: string | null): Promise<ChatMessageSendResponse> {
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

    // @ts-expect-error because it's an abstract class
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    public async publishDataBus(request: DataBusPublishRequest): Promise<DataBusPublishAck> {
        throw new Error("Method not implemented.");
    }

    protected addAuthHeader(base?: HeadersInit): Headers {
        const h = new Headers(base);
        if (this._authToken) h.set("Authorization", `Bearer ${this._authToken}`);
        return h;
    };

    protected addIdHeader(base?: HeadersInit): Headers {
        const h = new Headers(base);
        if (this._idToken && this._idHeaderName) h.set(this._idHeaderName, this._idToken);
        return h;
    };

    protected addCredentialsHeader(base?: HeadersInit): Headers {
        return this.addIdHeader(this.addAuthHeader(base));
    }

    protected addTZHeader(base?: HeadersInit): Headers {
        const h = new Headers(base);
        const tz = getClientTimezone()
        if (tz.tz) h.set("X-User-Timezone", tz.tz);
        h.set("X-User-UTC-Offset", String(tz.utcOffsetMin));
        return h;
    };

    protected addStreamIdHeader(base?: HeadersInit): Headers {
        const h = new Headers(base);
        if (this._streamIdHeaderName && this._streamId) {
            h.set(this._streamIdHeaderName, this._streamId);
        }
        return h;
    };

    protected buildEventSubmission(req: ChatRequest, attachments?: File[] | null, conversationId?: string | null): Record<string, unknown> {
        const reactiveEventType = req.reactiveEventType || "event.user.prompt";
        const events: ExternalEvent[] = [];
        const text = String(req.message || "").trim();
        const hasAuthoredEvents = Boolean(req.external_events?.length);
        if ((text || reactiveEventType === "event.user.steer") && !hasAuthoredEvents) {
            const source =
                reactiveEventType === "event.user.steer"
                    ? "chat.steer"
                    : reactiveEventType === "event.user.followup"
                        ? "chat.followup"
                        : "chat.message";
            events.push({
                event_id: crypto.randomUUID ? crypto.randomUUID() : `evt_${Date.now()}_${Math.random().toString(16).slice(2)}`,
                type: reactiveEventType,
                event_source_id: source,
                reactive: true,
                agent_id: req.target?.agent_id || req.target?.agent,
                story_id: req.target?.story_id as string | undefined,
                payload: {
                    mime: "text/plain",
                    event: {text},
                },
            });
        }
        (attachments || []).forEach((file, index) => {
            events.push({
                event_id: crypto.randomUUID ? crypto.randomUUID() : `evt_${Date.now()}_${index}_${Math.random().toString(16).slice(2)}`,
                type: "event.user.attachment.file",
                event_source_id: "chat.attachment",
                reactive: true,
                agent_id: req.target?.agent_id || req.target?.agent,
                story_id: req.target?.story_id as string | undefined,
                payload: {
                    mime: file.type || "application/octet-stream",
                    event: {
                        filename: file.name,
                        size: file.size,
                        mime: file.type || "application/octet-stream",
                        file_index: index,
                    },
                },
            });
        });
        events.push(...(req.external_events || []));
        return {
            external_events: events,
            chat_history: req.chat_history || [],
            project: req.project || this.project,
            tenant: req.tenant || this.tenant,
            turn_id: req.turn_id || createClientTurnId(),
            ...(conversationId ? {conversation_id: conversationId} : {}),
            ...(req.bundle_id ? {bundle_id: req.bundle_id} : {}),
            ...(req.active_turn_id ? {active_turn_id: req.active_turn_id} : {}),
            ...(req.target_turn_id ? {target_turn_id: req.target_turn_id} : {}),
            ...(req.target ? {target: req.target} : {}),
            ...(req.payload ? {payload: req.payload} : {}),
        };
    }

}
