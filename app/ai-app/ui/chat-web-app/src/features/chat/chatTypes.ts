import {Indexed, Timestamped} from "../../types/common.ts";
import {RichLink, RNFile, TurnStep} from "../chatController/chatBase.ts";
import {AuthConfig} from "../auth/authTypes.ts";
import {CookieOptions} from "../../utils/cookies.ts";


export interface ChatScope {
    tenant: string;
    project: string;
}

export interface ChatAuthSettings {
    auth: AuthConfig;
    useAuthCookies: boolean;
    authCookieName: string,
    idCookieName: string,
    authCookieOpts: CookieOptions
}

export interface ChatRoutingSettings {
    routesPrefix: string;
}

export interface ChatTransportSettings {
    streamIdHeaderName: string;
}

export interface ChatEventReportingSettings {
    eventReportingEnabled: boolean;
}

export interface ChatSettings extends ChatScope, ChatAuthSettings, ChatRoutingSettings, ChatTransportSettings, ChatEventReportingSettings {
}

export interface ChatSettingsState {
    isLoaded: boolean;
    isLoading: boolean;
    isLoadingError: boolean;
    settings: ChatSettings;
}

export interface UserAttachmentDescription {
    name: string;
    size: number;
    timestamp?: number;
    mime?: string | null;
    rn?: string | null;
    artifactPath?: string;
    sourceMessageId?: string;
    continuationKind?: string;
    historyMeta?: Record<string, unknown>;
}

export interface UserAttachment extends UserAttachmentDescription {
    fileKey: string;
}

export interface ConversationState {
    conversationId: string | null | undefined;
    turns: Record<string, ChatTurn>;
    turnOrder: string[]
    conversationTitle?: string | null;
    conversationBundleId: string | null,
}

export interface ChatState extends ConversationState {
    stayConnected: boolean;
    connected: boolean;
    userInputLocked: boolean;
    userInputLockMessage: string | null;
    userMessage: string;
    userAttachments: UserAttachment[];
    streamId: string | null;
}

export interface UserMessage extends Timestamped {
    text: string,
    attachments: UserAttachmentDescription[],
    continuationKind?: string;
    sourceMessageId?: string;
    artifactPath?: string;
    historyMeta?: Record<string, unknown>;
}

export interface AssistantMessage extends Timestamped {
    text: string;
    artifactPath?: string;
    historyMeta?: Record<string, unknown>;
}

export interface AgentTiming {
    startedAt: number;
    endedAt?: number;  // ← set only when the server sends completed: true
    active: boolean; // ← not used for display; keeps internal state
}

export interface TurnEvent<C> extends Timestamped, Indexed {
    eventType: string;
    agent: string;
    completed: boolean;
    data: C;
}

export interface ThinkingEventData {
    text: string;
}

export interface ThinkingEvent extends TurnEvent<ThinkingEventData> {
    eventType: "thinking";
}

export interface AnswerEventData {
    text: string;
}

export interface AnswerEvent extends TurnEvent<AnswerEventData> {
    eventType: "answer";
}

export interface CanvasEventData {
    name: string;
    title?: string | null;
    content: string;
    contentType: string;
}

export interface CanvasEvent extends TurnEvent<CanvasEventData> {
    eventType: "canvas";
}

export interface TimelineTextEventData {
    text: string;
    name: string;
}

export interface TimelineTextEvent extends TurnEvent<TimelineTextEventData> {
    eventType: "timeline_text";
}

export interface CompactionEventData {
    status: string;
    title?: string | null;
    kind?: string | null;
    reason?: string | null;
    beforeTokens?: number | null;
    afterTokens?: number | null;
    compactedTokens?: number | null;
    compactedBlocks?: number | null;
    splitTurn?: boolean;
    splitTurnId?: string | null;
    currentTurn?: boolean;
}

export interface CompactionEvent extends TurnEvent<CompactionEventData> {
    eventType: "compaction";
}

export interface SubsystemEventData {
    subtype: string;
    name: string;
    text: string;
    title?: string | null;
}

export interface SubsystemEvent extends TurnEvent<SubsystemEventData> {
    eventType: "subsystem";
}

export interface Artifact<C> extends Timestamped {
    content: C;
    artifactType: string;
    historical?: boolean;
    canCopy?: boolean;
    canSave?: boolean;
}

export type UnknownArtifact = Artifact<unknown>;

export interface ThinkingArtifactData extends Timestamped {
    endedAt?: number;
    agents: Record<string, string>;
    agentTimes: Record<string, AgentTiming>;
}

export interface ThinkingArtifact extends Artifact<ThinkingArtifactData> {
    artifactType: "thinking";
}

export interface CitationArtifact extends Artifact<RichLink> {
    artifactType: "citation";
}

export interface FileArtifact extends Artifact<RNFile> {
    artifactType: "file";
}

export interface UserMessageRequest {
    message?: string;
    files?: File[] | null;
    continuationKind?: "regular" | "followup" | "steer";
    targetTurnId?: string;
}

export type TurnState = "new" | "inProgress" | "finished" | "error"

export interface ChatTurn {
    id: string;
    state: TurnState;
    userMessage: UserMessage;
    additionalUserMessages?: UserMessage[];
    assistantMessages?: AssistantMessage[];
    answer?: string | null;
    events: TurnEvent<unknown>[];
    steps: Record<string, TurnStep>;
    artifacts: UnknownArtifact[];
    followUpQuestions: string[];
    historical?: boolean;
}

export interface NewChatTurnRequest {
    id: string;
    state: TurnState;
    userMessage: string;
    attachments: UserAttachmentDescription[];
}

export interface TurnError {
    turnId: string;
    error: string | Error;
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

    [key: string]: unknown;
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

interface ChatMessageMetadata {
    turn_id?: string;
}
