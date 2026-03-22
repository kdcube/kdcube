import {ConversationInfo} from "../chatController/chatBase.ts";
import {NewNotification} from "../popupNotifications/types.ts";

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
    total_token_remaining: number | null;
    usage_percentage: number | null;

    retry_after_sec: number | null;
    retry_scope: "hour" | "day" | "month" | "total" | null;

    retry_after_hours?: number | null;
    reset_text?: string | null;
    user_message?: string | null;
    notification_type?: "info" | "warning" | "error" | null;

    snapshot?: RateLimitSnapshot;
    reason?: string | null;
}

export type ServiceEventType = "rate_limit.warning" | "rate_limit.denied"
    | "rate_limit.project_exhausted" | "rate_limit.no_funding" | "rate_limit.subscription_exhausted"
    | "rate_limit.snapshot" | "rate_limit.attachment_failure"
    | "rate_limit.lane_switch" | "economics.user_underfunded_absorbed";

export interface ChatServiceEnvelope {
    type: ServiceEventType | string;
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

export interface ChatServiceMessageTrait {
    type: string;
    data?: unknown;
}

export const PopupShowType = "popup.show"

export interface PopupShow extends ChatServiceMessageTrait {
    type: typeof PopupShowType;
    data: NewNotification;
}

export const UserInputLockTraitType = "user_input.lock"

export interface UserInputLockTrait extends ChatServiceMessageTrait {
    type: typeof UserInputLockTraitType;
}

export const UserInputAttachmentRejectedType = "user_input.attachment_rejected"

export interface UserInputAttachmentRejected extends ChatServiceMessageTrait {
    type: typeof UserInputAttachmentRejectedType;
}