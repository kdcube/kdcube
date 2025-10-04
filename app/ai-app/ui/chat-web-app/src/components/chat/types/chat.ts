/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

// types/chat.ts
export interface ModelInfo {
    id: string;
    name: string;
    provider: string;
    description: string;
    has_classifier: boolean;
}

export interface EmbedderInfo {
    id: string;
    provider: string;
    model: string;
    dimension: number;
    description: string;
}

export interface EmbeddingProvider {
    name: string;
    description: string;
    requires_api_key: boolean;
    requires_endpoint: boolean;
}

export interface BundleInfo {
    id: string;
    name?: string;
    description?: string;
    path: string;
    module?: string;
    singleton?: boolean;
}

export interface StepUpdate {
    step: string;
    title?: string | null;
    turn_id?: string;
    status: 'started' | 'completed' | 'error' | 'running';
    timestamp: Date;
    error?: string;
    data?: StepData;
    markdown?: string;   // ← from event.markdown
    agent?: string;      // ← from event.agent (optional, useful for UI)
}

interface StepData {
    message?: string;
    markdown?: string; // some backends might still put it here; we prefer top-level
    [key: string]: unknown;
}

export interface WithTurnID {
    getTurnId(): string | null | undefined;
}

export interface ChatMessageData {
    id: number;
    sender: "user" | "assistant";
    text: string;
    timestamp: Date;
    isError?: boolean;
    isGreeting?: boolean; //only relevant for assistant message
    attachments?: File[] //only relevant for user message
    metadata?: ChatMessageMetadata
}

/** minimal metadata used by grouping code */
interface ChatMessageMetadata {
    turn_id?: string;
}

export class ChatMessage implements WithTurnID {
    id: number;
    text: string;
    timestamp: Date;
    isError?: boolean;
    metadata?: ChatMessageMetadata;

    constructor(id: number, text: string, timestamp: Date, metadata?: ChatMessageMetadata) {
        this.id = id;
        this.text = text;
        this.timestamp = timestamp;
        this.metadata = metadata;
    }

    getTurnId(): string | null | undefined {
        return this.metadata?.turn_id
    }

    getPrettyID(): string {
        return `chat-message-${this.id}`;
    }

}

export class UserChatMessage extends ChatMessage {
    attachments?: File[];

    constructor(id: number, text: string, timestamp: Date, metadata?: ChatMessageMetadata, attachments?: File[]) {
        super(id, text, timestamp, metadata);
        this.attachments = attachments;
    }

    getPrettyID(): string {
        return `user-message-${this.id}`;
    }
}

export class AssistantChatMessage extends ChatMessage {
    isGreeting?: boolean;
    isError?: boolean;

    constructor(id: number, text: string, timestamp: Date, metadata?: ChatMessageMetadata, isError?: boolean, isGreeting?: boolean) {
        super(id, text, timestamp, metadata);
        this.isGreeting = isGreeting;
        this.isError = isError;
    }

    getPrettyID(): string {
        return `assistant-message-${this.id}`;
    }
}

export interface AssistantAnswerEvent {
    text: string;
    index: number;
    completed: boolean;
    timestamp: Date;
}

interface ChatMessageInput {
    id: number;
    sender: 'user' | 'assistant';
    text: string;
    timestamp: Date;
    isError?: boolean;
    isGreeting?: boolean;
    metadata?: ChatMessageMetadata;
    attachments?: File[];
}

export const createChatMessage = (input: ChatMessageInput): UserChatMessage | AssistantChatMessage => {
    const {id, text, timestamp, metadata, sender, isError, attachments, isGreeting} = input;
    return sender === 'user'
        ? new UserChatMessage(id, text, timestamp, metadata, attachments)
        : new AssistantChatMessage(id, text, timestamp, metadata, isError, isGreeting);
};

export const createAssistantChatStep = (input: StepUpdate): AssistantChatStep => {
    const {step, status, timestamp, error, data, title, markdown, agent, turn_id} = input;

    return new AssistantChatStep(step, status, timestamp, error, data, title, markdown, agent, turn_id);
};

type AssistantChatStepStatus = 'started' | 'completed' | 'error' | 'running'

export class AssistantChatStep implements WithTurnID {
    step: string;
    title?: string | null;
    status: AssistantChatStepStatus;
    timestamp: Date;
    error?: string;
    data?: StepData;
    markdown?: string;
    agent?: string;
    turn_id?: string;

    constructor(
        step: string,
        status: AssistantChatStepStatus,
        timestamp: Date,
        error?: string,
        data?: StepData,
        title?: string | null,
        markdown?: string,
        agent?: string,
        turn_id?: string,
    ) {
        this.step = step;
        this.timestamp = timestamp;
        this.status = status;
        this.error = error;
        this.data = data;
        this.title = title;
        this.markdown = markdown;
        this.agent = agent;
        this.turn_id = turn_id;
    }

    getMarkdown() {
        return this.markdown || this.data?.markdown || '';
    }

    getTurnId(): string | null | undefined {
        return this.turn_id;
    }
}

/** Per-agent timing info */
export interface AgentTiming {
    startedAt: Date;
    endedAt?: Date;  // ← set only when the server sends completed: true
    active: boolean; // ← not used for display; keeps internal state
}

export interface AssistantThinkingEvent {
    index: number;
    timestamp: Date;
    completed: boolean;
    agent: string;
    text: string;
}

/** Thinking holder that supports multiple agent rows + per-agent timings */
export class AssistantThinkingItem implements WithTurnID {
    id: number;
    timestamp: Date;
    turn_id: string;
    active: boolean;
    endedAt?: Date;
    /** Map of agent -> markdown text */
    agents: Record<string, string>;
    /** Map of agent -> timing */
    agentTimes: Record<string, AgentTiming>;

    constructor(
        id: number,
        timestamp: Date,
        turn_id: string,
        active: boolean = true,
        endedAt?: Date,
        agents?: Record<string, string>,
        agentTimes?: Record<string, AgentTiming>,
    ) {
        this.id = id;
        this.timestamp = timestamp;
        this.turn_id = turn_id;
        this.active = active;
        this.endedAt = endedAt;
        this.agents = agents ?? {};
        this.agentTimes = agentTimes ?? {};
    }

    getTurnId(): string | null | undefined {
        return this.turn_id
    }
}

export class StepDerivedItem implements WithTurnID {
    turnId?: string;

    constructor(turnId?: string) {
        this.turnId = turnId;
    }

    getTurnId(): string | null | undefined {
        return this.turnId;
    }
}

export class DownloadItem extends StepDerivedItem {
    filename: string;
    rn: string;
    timestamp: Date;
    mimeType?: string;

    constructor(filename: string, rn: string, timestamp: Date, mimeType?: string, turnId?: string) {
        super(turnId)
        this.filename = filename;
        this.rn = rn;
        this.timestamp = timestamp;
        this.mimeType = mimeType;
        this.turnId = turnId;
    }
}

export const createDownloadItem = (input: StepUpdate): DownloadItem => {
    const {data, turn_id, timestamp} = input;
    return new DownloadItem(data?.filename as string, data?.rn, timestamp, data?.mime, turn_id)
};

export class RichLink {
    url: string;
    title?: string;
    body?: string;

    constructor(url: string, title?: string, body?: string) {
        this.url = url;
        this.title = title;
        this.body = body;
    }
}

export class SourceLinks extends StepDerivedItem {
    links: RichLink[];
    timestamp: Date;
    turnId?: string;

    constructor(links: RichLink[], timestamp: Date, turnId?: string) {
        super(turnId)
        this.links = links;
        this.timestamp = timestamp;
        this.turnId = turnId;
    }
}

export const createSourceLinks = (input: StepUpdate): SourceLinks => {
    const {data, turn_id, timestamp} = input;
    return new SourceLinks(data?.items?.map((item) => {
        return {url: item.url, title: item.title, body: item.body}
    }), timestamp, turn_id)
};

export type ChatLogItem =
    UserChatMessage
    | AssistantChatMessage
    | AssistantChatStep
    | AssistantThinkingItem
    | DownloadItem
    | SourceLinks;


