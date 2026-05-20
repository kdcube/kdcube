import {createSlice, PayloadAction, WritableDraft} from "@reduxjs/toolkit";
import {RootState} from "../../app/store.ts";
import {
    ChatCompleteEnvelope,
    ChatCompactionEnvelope,
    ChatDeltaEnvelope,
    ChatStartEnvelope,
    ChatStepEnvelope,
    CitationsStepEnvelope,
    ConvStatusEnvelope,
    FilesStepEnvelope
} from "../chatController/chatBase.ts";
import {v4 as uuidv4} from "uuid";
import {
    AgentTiming,
    AssistantMessage,
    AnswerEvent,
    CanvasEvent,
    ChatTurn,
    ChatState,
    CompactionEvent,
    CitationArtifact,
    ConversationState,
    FileArtifact,
    NewChatTurnRequest,
    SubsystemEvent,
    SubsystemEventData,
    ThinkingArtifact,
    ThinkingEvent,
    TimelineTextEvent,
    TurnError,
    UserMessage,
    UserAttachmentDescription
} from "./chatTypes.ts";
import {
    CodeExecArtifact,
    CodeExecArtifactType,
    CodeExecCodeSubsystemEventData,
    CodeExecCodeSubsystemEventDataSubtype,
    CodeExecContractSubsystemEventData,
    CodeExecContractSubsystemEventDataSubtype,
    CodeExecData,
    CodeExecEvent,
    CodeExecEventSubtypes,
    CodeExecMetaEventData,
    CodeExecObjectiveSubsystemEventData,
    CodeExecObjectiveSubsystemEventDataSubtype,
    CodeExecProgramNameSubsystemEventData,
    CodeExecProgramNameSubsystemEventDataSubtype,
    CodeExecStatusSubsystemEventDataSubtype
} from "../logExtensions/codeExec/types.ts";
import {
    WebSearchArtifact,
    WebSearchArtifactType,
    WebSearchData,
    WebSearchEvent,
    WebSearchEventSubtypes,
    WebSearchFilteredResultsSubsystemEventDataSubtype,
    WebSearchHTMLViewSubsystemEventDataSubtype,
    WebSearchSubsystemEventData
} from "../logExtensions/webSearch/types.ts";
import {CanvasArtifact, CanvasArtifactType} from "../logExtensions/canvas/types.ts";
import {TimelineTextArtifact, TimelineTextArtifactType} from "../logExtensions/timelineText/types.ts";
import {
    ConversationStatusArtifact,
    ConversationStatusArtifactType,
    ConversationStatusSubsystemEventData,
    ConversationStatusSubsystemEventDataSubtype
} from "../logExtensions/conversationStatus/types.ts";
import {
    WebFetchArtifact,
    WebFetchArtifactType,
    WebFetchSubsystemEventData,
    WebFetchSubsystemEventDataSubtype
} from "../logExtensions/webFetch/types.ts";
import {ServiceErrorArtifactType} from "../logExtensions/service/types.ts";
import {CompactionArtifact, CompactionArtifactType} from "../logExtensions/compaction/types.ts";
import {exampleConversationData} from "./debug.ts";
import {setCurrentBundle} from "../bundles/bundlesSlice.ts";

const userAttachmentMapping = new Map<string, File>();

export const getUserAttachmentFile = (key: string) => {
    return userAttachmentMapping.get(key)
}

const timestampValue = (value: unknown, fallback: number): number => {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim()) {
        const parsed = Date.parse(value);
        if (Number.isFinite(parsed)) return parsed;
    }
    return fallback;
}

const itemTimestamp = (item: unknown, fallback: number): number => {
    if (!item || typeof item !== "object") return fallback;
    const record = item as Record<string, unknown>;
    return timestampValue(record.timestamp ?? record.ts ?? record.timestamp_iso, fallback);
}

const reduceThinkingEvents = (events: ThinkingEvent[], prev?: ThinkingArtifact | null): ThinkingArtifact | null => {
    if (!events || !events.length)
        return null;

    const completionEvent = events.find(val => val.completed)
    const completedAt = completionEvent?.timestamp

    const agents: Record<string, string> = {}
    const agentTimes: Record<string, AgentTiming> = {}

    events.forEach(val => {
        agents[val.agent] = (agents[val.agent] ?? "") + val.data.text
        let timings = agentTimes[val.agent]
        if (timings) {
            timings.active = !val.completed
            timings.startedAt = val.timestamp
        } else {
            timings = {
                startedAt: val.timestamp,
                active: !val.completed,
                endedAt: val.completed ? val.timestamp : undefined,
            }
        }
        agentTimes[val.agent] = timings;
    })

    return {
        artifactType: "thinking",
        timestamp: events[0].timestamp,
        content: {
            agents,
            agentTimes,
            timestamp: prev ? prev.content.timestamp : new Date().getTime(),
            endedAt: completedAt
        }
    }
}

const optionalNumber = (value: unknown): number | null => {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim()) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
}

const reduceTimelineTextEvent = (events: TimelineTextEvent[], name: string) => {
    events = events.filter(ev => ev.data.name === name)
    return {
        artifactType: TimelineTextArtifactType,
        timestamp: events[0].timestamp,
        content: {
            name,
            text: events.map(ev => ev.data.text).join("")
        },
    } as TimelineTextArtifact
}

const cleanUpCanvasItemContent = (content: string | null | undefined, contentType: string) => {
    if (!content)
        return content;
    const removeCodeBlockQuotes = contentType !== "markdown"
    let result = content.trim()
    if (removeCodeBlockQuotes)
        result = result.replace(/^(```|~~~).*\r?\n/g, "")
    result = result.replaceAll(/<+(?:G|$)(?:E|$)(?:N|$)(?:E|$)(?:R|$)(?:A|$)(?:T|$)(?:I|$)(?:O|$)(?:N|$)(?: |$)(?:F|$)(?:I|$)(?:N|$)(?:I|$)(?:S|$)(?:H|$)(?:E|$)(?:D|$)(?:>* *(?:$|\r?\n)|$)/g, "")
        .trimEnd()
    if (removeCodeBlockQuotes)
        result = result.replace(/(```|~~~)$/g, "")
    return result;
}

const reduceCanvasEvents = (events: CanvasEvent[], name: string): CanvasArtifact => {
    const itemEvents = events.filter(val => val.data.name === name);
    if (itemEvents.length === 0) {
        throw new Error(`Can't find event with name "${name}"`);
    }

    const firstEvent = itemEvents[0]
    const contentType = firstEvent.data.contentType === "text" ? (firstEvent.data.content.startsWith('```') ? "markdown" : firstEvent.data.contentType) : firstEvent.data.contentType
    const complete = itemEvents.findIndex(item => item.completed) >= 0
    const content = itemEvents.map(entry => entry.data.content).join("")
    const title = firstEvent.data.title

    return {
        artifactType: CanvasArtifactType,
        timestamp: firstEvent.timestamp,
        complete,
        content: {
            name,
            content: cleanUpCanvasItemContent(content, contentType),
            title,
            contentType,
        },
        canCopy: true,
        canSave: true
    }
}

const reduceWebSearchEvents = (events: SubsystemEvent[], searchId: string): WebSearchArtifact => {
    const itemEvents = events.filter(val => WebSearchEventSubtypes.includes(val.data.subtype) && (val as WebSearchEvent).data.searchId === searchId);
    if (itemEvents.length === 0) {
        throw new Error(`Can't find event with searchId "${searchId}"`);
    }

    const filteredEvent = itemEvents.find(val => val.data.subtype === WebSearchFilteredResultsSubsystemEventDataSubtype)
    const reportEvent = itemEvents.find(val => val.data.subtype === WebSearchHTMLViewSubsystemEventDataSubtype)

    const timestamp = itemEvents.reduce((previousValue, currentValue) => {
        if (previousValue === null || currentValue.timestamp < previousValue) {
            return currentValue.timestamp
        }
        return previousValue
    }, null as number | null) ?? Date.now()

    const content: WebSearchData = {
        name: "Web Search",
        items: [],
        searchId
    };

    if (filteredEvent) {
        const d = JSON.parse(filteredEvent.data.text)
        content.name = filteredEvent.data.name
        content.title = filteredEvent.data.title
        content.items = d.results
        content.objective = d.objective
        content.queries = d.queries
    }

    if (reportEvent) {
        content.reportContent = reportEvent.data.text
    }

    return {
        artifactType: WebSearchArtifactType,
        timestamp,
        content,
        canCopy: !!reportEvent,
        canSave: !!reportEvent,
    }
}

const reduceCodeExecEvents = (events: SubsystemEvent[], executionId: string): CodeExecArtifact => {
    const itemEvents = events.filter(val => CodeExecEventSubtypes.includes(val.data.subtype) && (val as CodeExecEvent).data.executionId === executionId);
    if (itemEvents.length === 0) {
        throw new Error(`Can't find event with executionId "${executionId}"`);
    }

    const timestamp = itemEvents.reduce((previousValue, currentValue) => {
        if (previousValue === null || currentValue.timestamp < previousValue) {
            return currentValue.timestamp
        }
        return previousValue
    }, null as number | null) ?? Date.now()

    const content: CodeExecData = {
        executionId: executionId
    };

    const codeTextEvents: SubsystemEvent[] = []

    itemEvents.forEach(event => {
        switch (event.data.subtype) {
            case CodeExecCodeSubsystemEventDataSubtype:
                codeTextEvents.push(event)
                break

            case CodeExecProgramNameSubsystemEventDataSubtype:
                content.name = {
                    name: event.data.name,
                    title: event.data.title,
                    timestamp: event.timestamp,
                    content: (event.data as CodeExecProgramNameSubsystemEventData).text,
                }
                break
            case CodeExecObjectiveSubsystemEventDataSubtype:
                content.objective = {
                    name: event.data.name,
                    title: event.data.title,
                    timestamp: event.timestamp,
                    content: (event.data as CodeExecObjectiveSubsystemEventData).text,
                }
                break
            case CodeExecContractSubsystemEventDataSubtype:
                content.contract = {
                    name: event.data.name,
                    title: event.data.title,
                    timestamp: event.timestamp,
                    content: JSON.parse((event.data as CodeExecContractSubsystemEventData).text).contract,
                }
                break
            case CodeExecStatusSubsystemEventDataSubtype:
                content.status = {
                    name: event.data.name,
                    title: event.data.title,
                    timestamp: event.timestamp,
                    content: JSON.parse((event.data as CodeExecContractSubsystemEventData).text).status,
                }
                break
        }
    })

    if (codeTextEvents.length > 0) {
        codeTextEvents.sort((a, b) => {
            return a.index - b.index
        })
        const firstEvent = codeTextEvents[0]
        content.program = {
            name: firstEvent.data.name,
            title: firstEvent.data.title,
            timestamp: firstEvent.timestamp,
            language: (firstEvent.data as CodeExecCodeSubsystemEventData).language,
            content: codeTextEvents.map(event => {
                return (event.data as CodeExecCodeSubsystemEventData).text
            }).join("")
        }
    }

    return {
        artifactType: CodeExecArtifactType,
        timestamp,
        content
    }
}

const clearUserAttachmentsInternal = (state: WritableDraft<ChatState>) => {
    state.userAttachments.forEach(attachment => {
        userAttachmentMapping.delete(attachment.fileKey)
    })
    state.userAttachments = []
}

const startNewConversation = (state: WritableDraft<ChatState>) => {
    state.turns = {}
    state.turnOrder = []
    state.userInputLocked = false
    state.userInputLockMessage = null
    state.userMessage = ""
    state.userAttachments = []
    state.conversationId = null
    state.conversationTitle = null
    state.conversationBundleId = null
}

const createSyntheticTurn = (
    turnId: string,
    userMessage: string,
    timestamp: number,
): WritableDraft<ChatTurn> => {
    return {
        id: turnId,
        state: "new",
        userMessage: {
            text: userMessage,
            attachments: [],
            timestamp,
        },
        events: [],
        artifacts: [],
        followUpQuestions: [],
        steps: {},
    }
}

const splitAssistantAnswerAttempts = (events: AnswerEvent[]): AssistantMessage[] => {
    const attempts: AnswerEvent[][] = [];
    let current: AnswerEvent[] = [];

    events
        .slice()
        .sort((a, b) => a.timestamp - b.timestamp || a.index - b.index)
        .forEach((event) => {
            const prev = current[current.length - 1];
            const startsNewAttempt =
                current.length > 0 &&
                (
                    !!prev?.completed ||
                    event.index <= (prev?.index ?? -1)
                );

            if (startsNewAttempt) {
                attempts.push(current);
                current = [];
            }

            current.push(event);

            if (event.completed) {
                attempts.push(current);
                current = [];
            }
        });

    if (current.length > 0) {
        attempts.push(current);
    }

    return attempts
        .filter((attempt) => attempt.length > 0)
        .map((attempt) => ({
            text: attempt.map((event) => event.data.text).join(""),
            timestamp: attempt[0].timestamp,
        }));
}

const isUserOriginAttachmentItem = (item: unknown) => {
    if (!item || typeof item !== "object") return false;
    const data = item as Record<string, unknown>;
    const meta = data.meta && typeof data.meta === "object" ? data.meta as Record<string, unknown> : {};
    const role = String(data.role ?? meta.role ?? "").toLowerCase();
    const origin = String(data.origin ?? meta.origin ?? "").toLowerCase();
    return role === "user" || origin === "user";
}

const isUserOriginAttachmentStep = (stepId: string, data: Record<string, unknown> | undefined) => {
    if (stepId !== "attachments") return false;
    const items = Array.isArray(data?.items) ? data.items : [];
    return items.length > 0 && items.every((item) => isUserOriginAttachmentItem(item));
}

const appendUserMessageToTurn = (
    turn: WritableDraft<ChatTurn>,
    message: UserMessage,
) => {
    const continuationKind = String(message.continuationKind ?? "").toLowerCase();
    const isFollowupLike = continuationKind === "followup" || continuationKind === "steer";

    if (!isFollowupLike && !turn.userMessage.text) {
        turn.userMessage = message;
        return;
    }

    turn.additionalUserMessages = turn.additionalUserMessages ?? [];
    const alreadyExists = turn.additionalUserMessages.some((existing) => {
        if (message.sourceMessageId && existing.sourceMessageId === message.sourceMessageId) return true;
        return existing.timestamp === message.timestamp && existing.text === message.text;
    });
    if (!alreadyExists) {
        turn.additionalUserMessages.push(message);
    }
}

const chatStateSlice = createSlice({
    name: 'chatState',
    initialState: (): ChatState => {
        return {
            stayConnected: false,
            connected: false,
            conversationId: undefined,
            conversationBundleId: null,
            turns: {},
            turnOrder: [],
            userInputLocked: false,
            userInputLockMessage: null,
            userMessage: "",
            userAttachments: [],
            streamId: null
        }
    },
    reducers: {
        startConnecting: (state) => {
            state.stayConnected = true;
        },
        disconnect: (state) => {
            state.stayConnected = false;
        },
        setConversationId(state, action: PayloadAction<string | null>) {
            state.conversationId = action.payload
        },
        chatConnected: (state, action: PayloadAction<string | null>) => {
            state.connected = true
            state.streamId = action.payload
        },
        chatDisconnected: (state) => {
            state.connected = false
            state.streamId = null
        },
        chatStarted(state, action: PayloadAction<ChatStartEnvelope>) {
            const turnId = action.payload.conversation.turn_id;
            const startedAt = Date.parse(action.payload.timestamp) || new Date().getTime()

            Object.values(state.turns).forEach((turn) => {
                if (turn.id !== turnId && turn.state === "inProgress") {
                    turn.state = "finished"
                }
            })

            if (Object.hasOwn(state.turns, turnId)) {
                state.turns[turnId].state = "inProgress"
            } else {
                state.turns[turnId] = createSyntheticTurn(
                    turnId,
                    String(action.payload.data?.message || ""),
                    startedAt,
                )
                state.turns[turnId].state = "inProgress"
                state.turnOrder.push(turnId)
            }
        },
        chatCompleted(state, action: PayloadAction<ChatCompleteEnvelope>) {
            const turnId = action.payload.conversation.turn_id;
            const timestamp = Date.parse(action.payload.timestamp) || new Date().getTime();

            if (Object.hasOwn(state.turns, turnId)) {
                if (action.payload.data.error_message) {
                    state.turns[turnId].state = "error"
                } else {
                    state.turns[turnId].state = "finished"
                }
            } else {
                console.error("Received event for an unknown turn", action.payload);
                return
            }

            // The visible answer comes from streamed `marker="answer"` deltas, where citation
            // tokens [[S:n]] are replaced into resolved links live. The chat.complete envelope
            // may still carry `data.final_answer` in raw token form; reading it here would
            // arrive after the stream completes and clobber the rendered text. For replay /
            // reload, the persisted assistant.completion artifact is the source of truth.
        },
        setUserMessage(state, action: PayloadAction<string>) {
            state.userMessage = action.payload;
        },
        addUserAttachments(state, action: PayloadAction<File[]>) {
            action.payload.forEach((file: File) => {
                let key = uuidv4()
                while (userAttachmentMapping.has(key)) {
                    key = uuidv4()
                }
                userAttachmentMapping.set(key, file)
                state.userAttachments.push({
                    name: file.name,
                    size: file.size,
                    fileKey: key,
                })
            })
        },
        removeUserAttachment(state, action: PayloadAction<string>) {
            const item = state.userAttachments.find((item) => item.fileKey === action.payload)
            if (item) {
                state.userAttachments.splice(state.userAttachments.indexOf(item), 1)
                userAttachmentMapping.delete(item.fileKey)
            } else {
                console.warn("Received event for an unknown user attachment", action.payload)
            }
        },
        clearUserAttachments(state: WritableDraft<ChatState>) {
            clearUserAttachmentsInternal(state)
        },
        clearUserInput(state) {
            state.userMessage = ""
            clearUserAttachmentsInternal(state)
        },
        newTurn: (state, action: PayloadAction<NewChatTurnRequest>) => {
            const turnId = action.payload.id
            const authoritativeUserMessage = {
                text: action.payload.userMessage,
                attachments: action.payload.attachments,
                timestamp: new Date().getTime()
            }

            if (state.turnOrder.includes(turnId)) {
                const existing = state.turns[turnId]
                if (existing) {
                    existing.userMessage = authoritativeUserMessage
                }
                return
            }

            state.turns[turnId] = {
                id: turnId,
                state: "new",
                userMessage: authoritativeUserMessage,
                events: [],
                artifacts: [],
                followUpQuestions: [],
                steps: {}
            };
            state.turnOrder.push(turnId);
        },
        appendTurnUserMessage: (state, action: PayloadAction<{
            turnId: string;
            text: string;
            attachments: UserAttachmentDescription[];
            timestamp: number;
            continuationKind?: string;
            sourceMessageId?: string;
            artifactPath?: string;
            historyMeta?: Record<string, unknown>;
        }>) => {
            const turn = state.turns[action.payload.turnId];
            if (!turn) {
                console.warn("Received user message for an unknown turn", action.payload.turnId);
                return;
            }
            appendUserMessageToTurn(turn, {
                text: action.payload.text,
                attachments: action.payload.attachments,
                timestamp: action.payload.timestamp,
                continuationKind: action.payload.continuationKind,
                sourceMessageId: action.payload.sourceMessageId,
                artifactPath: action.payload.artifactPath,
                historyMeta: action.payload.historyMeta,
            });
        },
        turnError: (state, action: PayloadAction<TurnError>) => {
            const turn = state.turns[action.payload.turnId];
            turn.state = "error";
            turn.artifacts.push({
                timestamp: new Date().getTime(),
                artifactType: ServiceErrorArtifactType,
                content: {
                    message: action.payload.error instanceof Error ? action.payload.error.message : action.payload.error
                }
            })
        },
        conversationStatus: (state, action: PayloadAction<ConvStatusEnvelope>) => {
            const payload = action.payload;
            if (!payload.conversation.conversation_id || state.conversationId !== payload.conversation.conversation_id) {
                console.warn("received event for an unknown conversation id or no conversation id in payload", action.payload)
                return
            }

            const convState = action.payload.data.state;

            if (convState !== "in_progress") {
                Object.values(state.turns).forEach((turn) => {
                    turn.state = "finished"
                })
            }

            // if (convState === "error" || action.payload.data.)

            // const turnId = payload.data.current_turn_id ?? payload.conversation?.turn_id ?? null;
            // if (!Object.hasOwn(state.turns, turnId)) {
            //     return
            // }
        },
        stepUpdate: (state, action: PayloadAction<ChatStepEnvelope>) => {
            const env = action.payload;
            const turnId = env.conversation.turn_id;
            const turn = state.turns[turnId]
            const stepId = env.event.step
            const stepStatus = env.event.status

            if (!turn) {
                console.warn("Received step for an unknown turn", env);
                return;
            }

            if (isUserOriginAttachmentStep(stepId, env.data)) {
                return;
            }

            const existing = Object.hasOwn(state.turns[turnId].steps, stepId) ? state.turns[turnId].steps[stepId] : null;

            const eventTimestamp = Date.parse(env.timestamp) || new Date().getTime()
            const ts = existing ? existing.timestamp : eventTimestamp

            turn.steps[stepId] = {
                step: stepId,
                status: stepStatus,
                title: env.event?.title,
                timestamp: ts,
                error: env.data?.error as string,
                data: env.data,
                markdown: env.event.markdown,
                agent: env.event.agent
            };

            if (stepStatus === "completed") {
                switch (stepId) {
                    case "files": {
                        const filesEnv = env as FilesStepEnvelope
                        if (filesEnv.data?.items && filesEnv.data?.items?.length > 0) {
                            filesEnv.data.items.forEach(item => {
                                const i = turn.artifacts.findIndex(f => f.artifactType === "file" && (f as FileArtifact).content.rn === item.rn)
                                const timestamp = itemTimestamp(item, eventTimestamp)
                                if (i > -1) {
                                    turn.artifacts.splice(i, 1, {content: item, artifactType: "file", timestamp})
                                } else {
                                    turn.artifacts.push({content: item, artifactType: "file", timestamp})
                                }
                            })

                        }
                        break;
                    }
                    case "citations": {
                        const citationsEnv = env as CitationsStepEnvelope
                        if (citationsEnv.data?.items && citationsEnv.data?.items?.length > 0) {
                            citationsEnv.data.items.forEach(item => {
                                const i = turn.artifacts.findIndex(c => c.artifactType === "citation" && (c as CitationArtifact).content.url === item.url)
                                const timestamp = itemTimestamp(item, eventTimestamp)
                                if (i > -1) {
                                    turn.artifacts.splice(i, 1, {
                                        content: item,
                                        artifactType: "citation",
                                        timestamp
                                    })
                                } else {
                                    turn.artifacts.push({content: item, artifactType: "citation", timestamp})
                                }
                            })
                        }
                        break;
                    }
                    case "conversation_title":
                        state.conversationTitle = env.data?.title as string;
                        break;
                    case "followups":
                        turn.followUpQuestions = (env.data?.items ?? []) as string[];
                        break
                }
            }
        },
        chatDelta: (state, action: PayloadAction<ChatDeltaEnvelope>) => {
            const env = action.payload;
            const turnId = env.conversation.turn_id;
            const marker = env.delta?.marker ?? "answer";
            const timestamp = Date.parse(env.timestamp);
            const textDelta = env.delta.text;
            const index = env.delta.index;

            if (!env.event.agent) {
                console.warn("Event has no agent", env)
            }

            const agent = env.event?.agent ?? "unknown_agent";
            const completed = !!env.delta.completed;

            const turn = state.turns[turnId]
            if (!turn) {
                console.warn("Received delta for an unknown turn", env);
                return;
            }

            switch (marker) {
                case "thinking": {
                    const event: ThinkingEvent = {
                        eventType: "thinking",
                        index,
                        timestamp,
                        completed,
                        agent,
                        data: {
                            text: textDelta
                        }
                    }
                    turn.events.push(event);
                    const prevIdx = turn.artifacts.findIndex(f => f.artifactType === "thinking");
                    const prev = prevIdx >= 0 ? turn.artifacts[prevIdx] as ThinkingArtifact : null;
                    const turnEvents = turn.events.filter(ev => ev.eventType === "thinking") as ThinkingEvent[]
                    const item = reduceThinkingEvents(turnEvents, prev)
                    if (item) {
                        if (prev) {
                            turn.artifacts.splice(prevIdx, 1, item);
                        } else {
                            turn.artifacts.push(item)
                        }
                    }
                    break;
                }
                case "answer": {
                    const event: AnswerEvent = {
                        eventType: "answer",
                        index,
                        agent,
                        completed,
                        timestamp,
                        data: {
                            text: textDelta,
                        }
                    }
                    turn.events.push(event);
                    const turnEvents = turn.events.filter(ev => ev.eventType === "answer") as AnswerEvent[]
                    const attempts = splitAssistantAnswerAttempts(turnEvents);
                    turn.assistantMessages = attempts;
                    turn.answer = attempts.at(-1)?.text ?? "";
                    break;
                }
                case "canvas": {
                    const event: CanvasEvent = {
                        eventType: "canvas",
                        index,
                        agent,
                        completed,
                        timestamp,
                        data: {
                            name: env.extra.artifact_name as string,
                            title: env.extra.title as string,
                            content: textDelta ?? "",
                            contentType: env.extra.format as string,
                        }
                    }

                    if (index === 0) {
                        turn.events = turn.events.filter(ev => ev.eventType !== "canvas" || (ev as CanvasEvent).data.name !== event.data.name)
                    }

                    turn.events.push(event)
                    const turnEvents = turn.events.filter(ev => ev.eventType === "canvas") as CanvasEvent[]
                    const item = reduceCanvasEvents(turnEvents, env.extra.artifact_name as string)
                    const prevIdx = turn.artifacts.findIndex(c => c.artifactType === CanvasArtifactType && (c as CanvasArtifact).content.name === env.extra.artifact_name)
                    if (prevIdx > -1) {
                        turn.artifacts.splice(prevIdx, 1, item)
                    } else {
                        turn.artifacts.push(item)
                    }
                    break;
                }
                case "timeline_text": {
                    const event: TimelineTextEvent = {
                        eventType: "timeline_text",
                        index,
                        agent,
                        completed,
                        timestamp,
                        data: {
                            text: textDelta,
                            name: env.extra.artifact_name as string,
                        }
                    }
                    turn.events.push(event)

                    const turnEvents = turn.events.filter(ev => ev.eventType === "timeline_text") as TimelineTextEvent[]
                    const item = reduceTimelineTextEvent(turnEvents, env.extra.artifact_name as string)
                    const prevIdx = turn.artifacts.findIndex(c => c.artifactType === TimelineTextArtifactType && (c as TimelineTextArtifact).content.name === env.extra.artifact_name)
                    if (prevIdx > -1) {
                        turn.artifacts.splice(prevIdx, 1, item)
                    } else {
                        turn.artifacts.push(item)
                    }
                    break
                }
                case "subsystem": {
                    type reducerFunc = (events: SubsystemEvent[]) => void
                    let reducer: reducerFunc | null = null

                    const subtype = env.extra.sub_type as string;
                    const name = env.extra.artifact_name as string;
                    const title = env.extra.title as string;
                    let data: SubsystemEventData
                    switch (subtype) {
                        case WebSearchFilteredResultsSubsystemEventDataSubtype:
                        case WebSearchHTMLViewSubsystemEventDataSubtype:
                            reducer = (events) => {
                                const searchId = env.extra.search_id as string
                                const item = reduceWebSearchEvents(events, searchId)
                                const prevIdx = turn.artifacts.findIndex(c => c.artifactType === WebSearchArtifactType && (c as WebSearchArtifact).content.searchId === searchId)
                                if (prevIdx > -1) {
                                    turn.artifacts.splice(prevIdx, 1, item)
                                } else {
                                    turn.artifacts.push(item)
                                }
                            }
                            data = {
                                name,
                                subtype,
                                title,
                                searchId: env.extra.search_id as string,
                                text: textDelta,
                            } as WebSearchSubsystemEventData
                            break
                        case CodeExecCodeSubsystemEventDataSubtype:
                            reducer = (events: SubsystemEvent[]) => {
                                const executionId = env.extra.execution_id as string
                                const item = reduceCodeExecEvents(events, executionId)
                                const prevIdx = turn.artifacts.findIndex(c => c.artifactType === CodeExecArtifactType && (c as CodeExecArtifact).content.executionId === executionId)
                                if (prevIdx > -1) {
                                    turn.artifacts.splice(prevIdx, 1, item)
                                } else {
                                    turn.artifacts.push(item)
                                }
                            }
                            data = {
                                name,
                                subtype,
                                title,
                                language: env.extra.language as string,
                                executionId: env.extra.execution_id as string,
                                text: textDelta
                            } as CodeExecCodeSubsystemEventData
                            break
                        case CodeExecProgramNameSubsystemEventDataSubtype:
                        case CodeExecObjectiveSubsystemEventDataSubtype:
                        case CodeExecContractSubsystemEventDataSubtype:
                        case CodeExecStatusSubsystemEventDataSubtype:
                            reducer = (events: SubsystemEvent[]) => {
                                const executionId = env.extra.execution_id as string
                                const item = reduceCodeExecEvents(events, executionId)
                                const prevIdx = turn.artifacts.findIndex(c => c.artifactType === CodeExecArtifactType && (c as CodeExecArtifact).content.executionId === executionId)
                                if (prevIdx > -1) {
                                    turn.artifacts.splice(prevIdx, 1, item)
                                } else {
                                    turn.artifacts.push(item)
                                }
                            }
                            data = {
                                name,
                                subtype,
                                title,
                                executionId: env.extra.execution_id as string,
                                text: textDelta
                            } as CodeExecMetaEventData
                            break
                        case ConversationStatusSubsystemEventDataSubtype:
                            reducer = () => {
                                turn.artifacts = turn.artifacts.filter(c => c.artifactType !== ConversationStatusArtifactType)
                                const artifact: ConversationStatusArtifact = {
                                    artifactType: ConversationStatusArtifactType,
                                    timestamp,
                                    content: JSON.parse(textDelta),
                                }
                                turn.artifacts.push(artifact)
                            }
                            data = {
                                name,
                                subtype,
                                title,
                                text: textDelta
                            } as ConversationStatusSubsystemEventData
                            break
                        case WebFetchSubsystemEventDataSubtype:
                            reducer = () => {
                                const executionId = env.extra.execution_id as string
                                turn.artifacts = turn.artifacts.filter(c => c.artifactType !== WebFetchArtifactType || (c as WebFetchArtifact).content.executionId !== executionId)
                                const artifact: WebFetchArtifact = {
                                    artifactType: WebFetchArtifactType,
                                    timestamp,
                                    content: {
                                        name,
                                        executionId,
                                        title,
                                        items: JSON.parse(textDelta).urls
                                    },
                                }
                                turn.artifacts.push(artifact)
                            }
                            data = {
                                name,
                                subtype,
                                title,
                                text: textDelta,
                                executionId: env.extra.execution_id as string,
                            } as WebFetchSubsystemEventData
                            break
                        default:
                            console.warn("unknown subsystem event subtype", env)
                            data = {
                                name,
                                subtype,
                                title,
                                text: textDelta
                            } as SubsystemEventData
                            break
                    }

                    const event: SubsystemEvent = {
                        eventType: "subsystem",
                        index,
                        agent,
                        completed,
                        timestamp,
                        data
                    }

                    if (index === 0) {
                        turn.events = turn.events.filter(ev => ev.eventType !== "subsystem" || (ev as SubsystemEvent).data.name !== event.data.name)
                    }

                    turn.events.push(event)

                    if (reducer) {
                        const turnEvents = turn.events.filter(ev => ev.eventType === "subsystem") as SubsystemEvent[]
                        reducer(turnEvents)
                    }
                    break
                }
            }

            if (env.event?.step === "followups" && env.event?.status === "completed") {
                turn.followUpQuestions = env.data?.items as [] || [];
            }

            if (env.event?.step === "conversation_title" && env.event?.status === "completed") {
                state.conversationTitle = env.data?.title as string || null;
            }
        },
        chatCompaction: (state, action: PayloadAction<ChatCompactionEnvelope>) => {
            const env = action.payload;
            const turnId = env.conversation.turn_id;
            const turn = state.turns[turnId]
            if (!turn) {
                console.warn("Received compaction event for an unknown turn", env);
                return;
            }

            const data = env.data ?? {};
            const timestamp = Date.parse(env.timestamp) || new Date().getTime();
            const content = {
                status: (data.status as string | undefined) || env.event.status,
                title: env.event.title,
                kind: data.kind as string | undefined,
                reason: data.reason as string | undefined,
                triggerReason: data.trigger_reason as string | undefined,
                beforeTokens: optionalNumber(data.before_tokens),
                afterTokens: optionalNumber(data.after_tokens),
                compactedTokens: optionalNumber(data.compacted_tokens),
                inputTokensEstimate: optionalNumber(data.input_tokens_estimate),
                maxTokens: optionalNumber(data.max_tokens),
                thresholdTokens: optionalNumber(data.threshold_tokens),
                compactedBlocks: optionalNumber(data.compacted_blocks),
                beforeVisibleBlocks: optionalNumber(data.before_visible_blocks),
                afterVisibleBlocks: optionalNumber(data.after_visible_blocks),
                compactedVisibleBlocks: optionalNumber(data.compacted_visible_blocks),
                splitTurn: Boolean(data.split_turn),
                splitTurnId: data.split_turn_id as string | undefined,
                currentTurn: Boolean(data.current_turn),
            };
            const event: CompactionEvent = {
                eventType: "compaction",
                index: turn.events.filter(ev => ev.eventType === "compaction").length,
                agent: env.event.agent ?? "context.compaction",
                completed: content.status !== "started",
                timestamp,
                data: content,
            };
            turn.events.push(event)
            const artifact: CompactionArtifact = {
                artifactType: CompactionArtifactType,
                timestamp,
                content,
            }
            turn.artifacts.push(artifact)
        },
        newConversation: (state) => {
            startNewConversation(state)
        },
        loadConversation: (state, action: PayloadAction<ConversationState>) => {
            state.turnOrder = action.payload.turnOrder
            state.turns = action.payload.turns
            state.conversationId = action.payload.conversationId
            state.conversationTitle = action.payload.conversationTitle
            state.conversationBundleId = action.payload.conversationBundleId
        },
        loadExampleConversation: (state) => {
            state.turnOrder = exampleConversationData.turnOrder
            state.turns = exampleConversationData.turns
            state.conversationId = exampleConversationData.conversationId
            state.conversationTitle = exampleConversationData.conversationTitle
        },
        lockInput: (state, action: PayloadAction<string | null | undefined>) => {
            state.userInputLocked = true
            state.userInputLockMessage = action.payload ? action.payload : null
        },
        unlockInput: (state) => {
            state.userInputLocked = false
            state.userInputLockMessage = null
        }
    },
    extraReducers: (builder) => {
        builder.addCase(setCurrentBundle.type, (state, action) => {
            if (state.conversationId && state.conversationBundleId !== (action as PayloadAction<string>).payload) {
                startNewConversation(state)
            }
        })
    }
})

export const {
    startConnecting,
    disconnect,
    setConversationId,
    chatConnected,
    chatDisconnected,
    chatStarted,
    chatCompleted,
    newTurn,
    stepUpdate,
    chatCompaction,
    chatDelta,
    turnError,
    setUserMessage,
    addUserAttachments,
    removeUserAttachment,
    clearUserInput,
    appendTurnUserMessage,
    newConversation,
    loadConversation,
    loadExampleConversation,
    conversationStatus,
    lockInput,
    unlockInput,
    clearUserAttachments
} = chatStateSlice.actions
export const selectChatConnected = (state: RootState) => state.chatState.connected
export const selectChatStayConnected = (state: RootState) => state.chatState.stayConnected
export const selectUserInputLocked = (state: RootState) => state.chatState.userInputLocked
export const selectUserInputLockMessage = (state: RootState) => state.chatState.userInputLockMessage
export const selectCurrentTurn = (state: RootState) => {
    return Object.entries(state.chatState.turns).map(([_unused, v]) => v).find(t => t.state === "inProgress")
}
export const selectTurns = (state: RootState) => state.chatState.turns
export const selectTurnOrder = (state: RootState) => state.chatState.turnOrder
export const selectUserMessage = (state: RootState) => state.chatState.userMessage
export const selectUserAttachments = (state: RootState) => state.chatState.userAttachments
export const selectConversationId = (state: RootState) => state.chatState.conversationId
export const selectConversationTitle = (state: RootState) => state.chatState.conversationTitle
export const selectIsNewConversation = (state: RootState) => state.chatState.turnOrder.length === 0
export const selectStreamId = (state: RootState) => state.chatState.streamId


export default chatStateSlice.reducer
