import {createSlice, PayloadAction, WritableDraft} from "@reduxjs/toolkit";
import {RootState} from "../../app/store.ts";
import {
    ChatCompleteEnvelope,
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
    AnswerEvent,
    CanvasEvent,
    ChatState,
    CitationArtifact,
    CodeExecCodeSubsystemEventData,
    CodeExecCodeSubsystemEventDataSubtype,
    CodeExecContractSubsystemEventData,
    CodeExecContractSubsystemEventDataSubtype,
    CodeExecEvent,
    CodeExecEventSubtypes,
    CodeExecMetaEventData,
    CodeExecObjectiveSubsystemEventData,
    CodeExecObjectiveSubsystemEventDataSubtype,
    CodeExecProgramNameSubsystemEventData,
    CodeExecProgramNameSubsystemEventDataSubtype,
    CodeExecStatusSubsystemEventDataSubtype,
    ConversationState,
    FileArtifact,
    NewChatTurnRequest,
    SubsystemEvent,
    SubsystemEventData,
    ThinkingArtifact,
    ThinkingEvent,
    TimelineTextEvent,
    TurnError,
    WebSearchEvent,
    WebSearchEventSubtypes,
    WebSearchFilteredResultsSubsystemEventDataSubtype,
    WebSearchHTMLViewSubsystemEventDataSubtype,
    WebSearchSubsystemEventData
} from "./chatTypes.ts";
import {CodeExecArtifact, CodeExecArtifactType, CodeExecData} from "../logExtensions/codeExec/types.ts";
import {WebSearchArtifact, WebSearchArtifactType, WebSearchData} from "../logExtensions/webSearch/types.ts";
import {CanvasArtifact, CanvasArtifactType} from "../logExtensions/canvas/types.ts";
import {TimelineTextArtifact, TimelineTextArtifactType} from "../logExtensions/timelineText/types.ts";

const userAttachmentMapping = new Map<string, File>();

export const getUserAttachmentFile = (key: string) => {
    return userAttachmentMapping.get(key)
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
    const subType = firstEvent.data.subType
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
            subType,
        }
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
        content
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

    itemEvents.forEach(event => {
        switch (event.data.subtype) {
            case CodeExecCodeSubsystemEventDataSubtype:
                content.program = {
                    name: event.data.name,
                    title: event.data.title,
                    timestamp: event.timestamp,
                    language: (event.data as CodeExecCodeSubsystemEventData).language,
                    content: (event.data as CodeExecCodeSubsystemEventData).text,
                }
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

const chatStateSlice = createSlice({
    name: 'chatState',
    initialState: (): ChatState => {
        return {
            stayConnected: false,
            connected: false,
            conversationId: undefined,
            turns: {},
            turnOrder: [],
            locked: false,
            userMessage: "",
            userAttachments: []
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
        chatConnected: (state) => {
            state.connected = true
        },
        chatDisconnected: (state) => {
            state.connected = false
        },
        chatStarted(state, action: PayloadAction<ChatStartEnvelope>) {
            const turnId = action.payload.conversation.turn_id;
            if (Object.hasOwn(state.turns, turnId)) {
                state.turns[turnId].state = "inProgress"
            } else {
                console.error("Received event for an unknown turn", action.payload);
            }
        },
        chatCompleted(state, action: PayloadAction<ChatCompleteEnvelope>) {
            const turnId = action.payload.conversation.turn_id;

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

            if (action.payload.data.final_answer) {
                state.turns[turnId].answer = action.payload.data.final_answer;
            }
            if (action.payload.data.followups) {
                state.turns[turnId].followUpQuestions = action.payload.data.followups;
            }
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
        clearUserInput(state) {
            state.userMessage = ""
            clearUserAttachmentsInternal(state)
        },
        newTurn: (state, action: PayloadAction<NewChatTurnRequest>) => {
            const turnId = action.payload.id

            if (state.turnOrder.includes(turnId)) {
                throw new Error(`Turn ${turnId} already exists`)
            }

            state.turns[turnId] = {
                id: turnId,
                state: "new",
                userMessage: {
                    text: action.payload.userMessage,
                    attachments: action.payload.attachments,
                    timestamp: new Date().getTime()
                },
                events: [],
                artifacts: [],
                followUpQuestions: [],
                steps: {}
            };
            state.turnOrder.push(turnId);
        },
        turnError: (state, action: PayloadAction<TurnError>) => {
            const turnId = action.payload.id
            state.turns[turnId].state = "error";
            state.turns[turnId].error = action.payload.error instanceof Error ? action.payload.error.message : action.payload.error;
        },
        conversationStatus: (state, action: PayloadAction<ConvStatusEnvelope>) => {
            const payload = action.payload;
            if (!payload.conversation.conversation_id || state.conversationId !== payload.conversation.conversation_id) {
                console.warn("received event for an unknown conversation id or no conversation id in patload", action.payload)
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

            const existing = Object.hasOwn(state.turns[turnId].steps, stepId) ? state.turns[turnId].steps[stepId] : null;

            const ts = existing ? existing.timestamp : (Date.parse(env.timestamp) || new Date().getTime())

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
                                if (i > -1) {
                                    turn.artifacts.splice(i, 1, {content: item, artifactType: "file", timestamp: ts})
                                } else {
                                    turn.artifacts.push({content: item, artifactType: "file", timestamp: ts})
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
                                if (i > -1) {
                                    turn.artifacts.splice(i, 1, {
                                        content: item,
                                        artifactType: "citation",
                                        timestamp: ts
                                    })
                                } else {
                                    turn.artifacts.push({content: item, artifactType: "citation", timestamp: ts})
                                }
                            })
                        }
                        break;
                    }
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
                    turn.answer = turnEvents.map(event => event.data.text).join("")
                    break;
                }
                case "canvas":
                case "tool": {
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
                            subType: marker === "tool" ? "webSearch" : null,
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
                        default:
                            console.warn("unknown subtype", env)
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
        newConversation: (state) => {
            state.turns = {}
            state.turnOrder = []
            state.locked = false
            state.userMessage = ""
            state.userAttachments = []
            state.conversationId = null
        },
        loadConversation: (state, action: PayloadAction<ConversationState>) => {
            state.turnOrder = action.payload.turnOrder
            state.turns = action.payload.turns
            state.conversationId = action.payload.conversationId
            state.conversationTitle = action.payload.conversationTitle
        }
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
    chatDelta,
    turnError,
    setUserMessage,
    addUserAttachments,
    removeUserAttachment,
    clearUserInput,
    newConversation,
    loadConversation,
    conversationStatus,
} = chatStateSlice.actions
export const selectChatConnected = (state: RootState) => state.chatState.connected
export const selectChatStayConnected = (state: RootState) => state.chatState.stayConnected
export const selectLocked = (state: RootState) => state.chatState.locked
export const selectCurrentTurn = (state: RootState) => {
    return Object.entries(state.chatState.turns).map(([_unused, v]) => v).find(t => t.state === "inProgress")
}
export const selectTurns = (state: RootState) => state.chatState.turns
export const selectTurnOrder = (state: RootState) => state.chatState.turnOrder
export const selectUserMessage = (state: RootState) => state.chatState.userMessage
export const selectUserAttachments = (state: RootState) => state.chatState.userAttachments
export const selectConversationId = (state: RootState) => state.chatState.conversationId


export default chatStateSlice.reducer