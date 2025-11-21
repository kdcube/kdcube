// src/types.ts
export type V1Status = "started" | "running" | "completed" | "error" | "skipped";

export interface ConversationInfo {
    session_id: string;
    conversation_id: string;
    turn_id: string;
}

export interface V1BaseEnvelope {
    type: "chat.start" | "chat.step" | "chat.delta" | "chat.complete" | "chat.error";
    timestamp: string;
    service: { request_id: string; tenant?: string | null; project?: string | null; user?: string | null };
    conversation: ConversationInfo;
    event: { agent?: string | null; step: string; status: V1Status; title?: string | null };
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
    delta: { text: string; marker: "thinking" | "answer" | string; index: number; completed?: boolean };
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
    data: { error: string; [k: string]: unknown };
}
export interface ConvStatusEnvelope {
    type: "conv.status";
    timestamp: string;
    service?: { request_id?: string | null; tenant?: string | null; project?: string | null; user?: string | null };
    conversation: ConversationInfo;
    event: { step: "conv.state"; status: "idle" | "in_progress" | "error" };
    data: { state: "idle" | "in_progress" | "error"; updated_at: string; current_turn_id?: string | null };
}

export type AnyEnvelope =
    | ChatStartEnvelope
    | ChatStepEnvelope
    | ChatDeltaEnvelope
    | ChatCompleteEnvelope
    | ChatErrorEnvelope
    | ConvStatusEnvelope;

export type SessionProfile = { session_id: string; user_type: string; roles?: string[] };
