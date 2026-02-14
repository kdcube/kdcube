import {Indexed, Timestamped} from "../../types/common.ts";
import {RichLink, RNFile, TurnStep} from "../chatController/chatBase.ts";
import {AuthConfig} from "../auth/authTypes.ts";



export interface ChatSettings {
    auth: AuthConfig;
    tenant: string;
    project: string;
    routesPrefix: string;
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
}

export interface UserAttachment extends UserAttachmentDescription {
    fileKey: string;
}

export interface ConversationState {
    conversationId: string | null | undefined;
    turns: Record<string, ChatTurn>;
    turnOrder: string[]
    conversationTitle?: string | null;
}

export interface ChatState extends ConversationState {
    stayConnected: boolean;
    connected: boolean;
    locked: boolean;
    userMessage: string;
    userAttachments: UserAttachment[];
}

export interface UserMessage extends Timestamped {
    text: string,
    attachments: UserAttachmentDescription[],
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
    subType: string | null;
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

export interface SubsystemEventData {
    subtype: string;
    name: string;
    text: string;
    title?: string | null;
}

export interface WebSearchSubsystemEventData extends SubsystemEventData {
    searchId: string;
}

export const WebSearchFilteredResultsSubsystemEventDataSubtype = "web_search.filtered_results"

export interface WebSearchFilteredResultsSubsystemEventData extends WebSearchSubsystemEventData {
    subtype: typeof WebSearchFilteredResultsSubsystemEventDataSubtype
}

export const WebSearchHTMLViewSubsystemEventDataSubtype = "web_search.html_view"

export interface WebSearchHTMLViewSubsystemEventData extends WebSearchSubsystemEventData {
    subtype: typeof WebSearchHTMLViewSubsystemEventDataSubtype
}

export const WebSearchEventSubtypes = [WebSearchFilteredResultsSubsystemEventDataSubtype, WebSearchHTMLViewSubsystemEventDataSubtype]

export interface CodeExecSubsystemEventData extends SubsystemEventData {
    executionId: string;
}

export type WebSearchMetaEventData = WebSearchFilteredResultsSubsystemEventData | WebSearchHTMLViewSubsystemEventData

export type WebSearchEvent = TurnEvent<WebSearchMetaEventData>

export const CodeExecCodeSubsystemEventDataSubtype = "code_exec.code"

export interface CodeExecCodeSubsystemEventData extends CodeExecSubsystemEventData {
    subtype: typeof CodeExecCodeSubsystemEventDataSubtype
    language: string;
}

export const CodeExecProgramNameSubsystemEventDataSubtype = "code_exec.program.name"

export interface CodeExecProgramNameSubsystemEventData extends CodeExecSubsystemEventData {
    subtype: typeof CodeExecProgramNameSubsystemEventDataSubtype
}

export const CodeExecObjectiveSubsystemEventDataSubtype = "code_exec.objective"

export interface CodeExecObjectiveSubsystemEventData extends CodeExecSubsystemEventData {
    subtype: typeof CodeExecObjectiveSubsystemEventDataSubtype
}

export const CodeExecContractSubsystemEventDataSubtype = "code_exec.contract"

export interface CodeExecContractSubsystemEventData extends CodeExecSubsystemEventData {
    subtype: typeof CodeExecContractSubsystemEventDataSubtype
}

export const CodeExecStatusSubsystemEventDataSubtype = "code_exec.status"

export interface CodeExecStatusSubsystemEventData extends CodeExecSubsystemEventData {
    subtype: typeof CodeExecStatusSubsystemEventDataSubtype
}

export const CodeExecEventSubtypes = [CodeExecCodeSubsystemEventDataSubtype, CodeExecProgramNameSubsystemEventDataSubtype,
    CodeExecObjectiveSubsystemEventDataSubtype, CodeExecContractSubsystemEventDataSubtype, CodeExecStatusSubsystemEventDataSubtype]

export type CodeExecMetaEventData = CodeExecCodeSubsystemEventData
    | CodeExecProgramNameSubsystemEventData
    | CodeExecObjectiveSubsystemEventData
    | CodeExecStatusSubsystemEventData
    | CodeExecContractSubsystemEventData

export type CodeExecEvent = TurnEvent<CodeExecMetaEventData>

export interface SubsystemEvent extends TurnEvent<SubsystemEventData> {
    eventType: "subsystem";
}

export interface Artifact<C> extends Timestamped {
    content: C;
    artifactType: string;
    historical?: boolean;
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
}

export type TurnState = "new" | "inProgress" | "finished" | "error"

export interface ChatTurn {
    id: string;
    state: TurnState;
    userMessage: UserMessage;
    answer?: string | null;
    events: TurnEvent<unknown>[];
    steps: Record<string, TurnStep>;
    artifacts: UnknownArtifact[];
    error?: string | null;
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
    id: string;
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