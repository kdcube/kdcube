import {Middleware, UnknownAction} from "@reduxjs/toolkit";
import {AppStore} from "../../app/store.ts";
import {
    chatConnected,
    loadConversation,
    newConversation,
    selectChatConnected,
    selectConversationId,
    setConversationId
} from "../chat/chatStateSlice.ts";
import {fetchConversation, getConversations, deleteConversation as deleteConversationAPI} from "./conversationsAPI.ts";
import {
    conversationStatusUpdateRequired, removeConversation,
    selectConversationDescriptorsLoading,
    selectConversationLoading,
    selectConversationStatusUpdateRequired,
    setConversationDescriptors,
    setConversationDescriptorsLoading,
    setConversationDescriptorsLoadingError,
    setConversationLoading
} from "./conversationsSlice.ts";
import {
    ArtifactStreamData,
    ArtifactStreamParser,
    AssistantFileData,
    CitationsData,
    ConversationDescriptor,
    FollowUpsData,
    ThinkingStreamData,
    TimelineTextStreamData
} from "./conversationsTypes.ts";
import {
    AgentTiming,
    AssistantMessage,
    ChatTurn,
    CitationArtifact,
    FileArtifact,
    ThinkingArtifact,
    UnknownArtifact,
    UserAttachmentDescription,
    UserMessage
} from "../chat/chatTypes.ts";
import {RichLink, RNFile} from "../chatController/chatBase.ts";
import {requestConversationStatus} from "../chat/chatServiceMiddleware.ts";
import {TimelineTextArtifact, TimelineTextArtifactType} from "../logExtensions/timelineText/types.ts";
import {selectProject, selectTenant} from "../chat/chatSettingsSlice.ts";
import {selectCurrentBundle} from "../bundles/bundlesSlice.ts";

const LOAD_CONVERSATION_LIST = "conversations/loadConversationList"

interface LoadConversationListAction extends UnknownAction {
    type: typeof LOAD_CONVERSATION_LIST;
}

export const loadConversationList = (): LoadConversationListAction => {
    return {
        type: LOAD_CONVERSATION_LIST,
    }
}

const LOAD_CONVERSATION = "conversations/loadConversation"

interface LoadConversationsAction extends UnknownAction {
    type: typeof LOAD_CONVERSATION;
    payload: string | null;
}

export const loadConversations = (conversationId: string | null): LoadConversationsAction => {
    return {
        type: LOAD_CONVERSATION,
        payload: conversationId
    }
}

const DELETE_CONVERSATION = "conversations/deleteConversation"

interface DeleteConversationAction extends UnknownAction {
    type: typeof DELETE_CONVERSATION;
    payload: string;
}

export const deleteConversation = (conversationId: string): DeleteConversationAction => {
    return {
        type: DELETE_CONVERSATION,
        payload: conversationId
    }
}

type ConversationURLAction = LoadConversationsAction
    | LoadConversationListAction
    | DeleteConversationAction
    | ReturnType<typeof setConversationId>
    | ReturnType<typeof chatConnected>;

const artifactStreamParsers: ArtifactStreamParser[] = [];

export const addArtifactStreamParsers = (...parsers: ArtifactStreamParser[]) => {
    artifactStreamParsers.push(...parsers);
}

const parseExternalAttachmentPath = (artifactPath?: string | null) => {
    if (!artifactPath || typeof artifactPath !== "string") return {};

    const externalMatch = artifactPath.match(/^fi:([^.]*)\.external\.([^.]+)\.attachments\/([^/]+)\/(.+)$/);
    if (externalMatch) {
        return {
            continuationKind: externalMatch[2],
            sourceMessageId: externalMatch[3],
        };
    }

    const legacyExternalMatch = artifactPath.match(/^fi:([^.]*)\.external\.([^.]+)\.([^.]+)\.attachments\/(.+)$/);
    if (legacyExternalMatch) {
        return {
            continuationKind: legacyExternalMatch[2],
            sourceMessageId: legacyExternalMatch[3],
        };
    }

    return {};
}

const parseExternalMessageIdFromPath = (path?: string | null) => {
    if (!path || typeof path !== "string") return undefined;
    const match = path.match(/^ar:([^.]*)\.external\.([^.]+)\.([^.]+)$/);
    return match?.[3];
}

const isExternalAttachmentArtifact = (artifactPath?: string | null) => {
    return !!artifactPath && (
        /^fi:([^.]*)\.external\.([^.]+)\.attachments\//.test(artifactPath) ||
        /^fi:([^.]*)\.external\.([^.]+)\.([^.]+)\.attachments\//.test(artifactPath)
    );
}

const getArtifactMeta = (data: unknown): Record<string, unknown> => {
    if (!data || typeof data !== "object") return {};
    const meta = (data as { meta?: unknown }).meta;
    return meta && typeof meta === "object" ? meta as Record<string, unknown> : {};
}

const getUserMessageIdentity = (message: UserMessage) => {
    if (message.sourceMessageId) return message.sourceMessageId;
    const meta = message.historyMeta;
    if (meta && typeof meta.message_id === "string") return meta.message_id;
    if (meta && typeof meta.path === "string") return parseExternalMessageIdFromPath(meta.path);
    if (message.artifactPath) return parseExternalMessageIdFromPath(message.artifactPath);
    return undefined;
}

const bindAttachmentsToUserMessages = (messages: UserMessage[], attachments: UserAttachmentDescription[]) => {
    if (!messages.length || !attachments.length) return;

    attachments.forEach((attachment) => {
        let target = messages.find((message) => {
            const sourceMessageId = attachment.sourceMessageId;
            return !!sourceMessageId && sourceMessageId === getUserMessageIdentity(message);
        });

        if (!target && attachment.continuationKind) {
            const attachmentTs = attachment.timestamp ?? Number.MAX_SAFE_INTEGER;
            const candidates = messages.filter((message) => message.continuationKind === attachment.continuationKind);
            target = candidates
                .filter((message) => message.timestamp <= attachmentTs)
                .sort((a, b) => b.timestamp - a.timestamp)[0] ?? candidates.at(-1);
        }

        if (!target) {
            const attachmentTs = attachment.timestamp ?? Number.MAX_SAFE_INTEGER;
            target = messages
                .filter((message) => message.timestamp <= attachmentTs)
                .sort((a, b) => b.timestamp - a.timestamp)[0] ?? messages.at(-1);
        }

        if (target) {
            target.attachments.push(attachment);
        }
    });
}

const conversationsMiddleware = (): Middleware => {
    const loadConversationList = (store: AppStore) => {
        const dispatch = store.dispatch
        const state = store.getState()
        if (selectConversationDescriptorsLoading(state)) return
        dispatch(setConversationDescriptorsLoading())
        getConversations(selectTenant(state), selectProject(state), selectCurrentBundle(state)).then((conversations) => {
            const list = conversations.map((it): ConversationDescriptor => {
                return {
                    id: it.conversation_id,
                    started: it.started_at ? Date.parse(it.started_at) : null,
                    lastActivity: it.last_activity_at ? Date.parse(it.last_activity_at) : null,
                    title: it.title
                }
            })
            dispatch(setConversationDescriptors(list))
        }).catch(error => {
            console.error(error)
            dispatch(setConversationDescriptorsLoadingError(error.message))
        })
    }

    const fetchConv = (store: AppStore, conversationId: string) => {
        const dispatch = store.dispatch
        const state = store.getState()
        fetchConversation(selectTenant(state), selectProject(state), conversationId).then((conversation) => {
            dispatch(loadConversation({
                conversationBundleId: conversation.bundle_id,
                turnOrder: conversation.turns.map(it => it.turn_id),
                turns: conversation.turns.reduce((previousValue, currentValue, i, arr) => {
                    let userMessage: UserMessage | null = null;
                    const additionalUserMessages: UserMessage[] = [];
                    const userMessages: UserMessage[] = [];
                    const userAttachments: UserAttachmentDescription[] = [];
                    const assistantMessages: AssistantMessage[] = [];
                    let answer: string | null = null;
                    const followUpQuestions: string[] = []
                    const turnArtifacts: UnknownArtifact[] = []

                    currentValue.artifacts.forEach(it => {
                        switch (it.type) {
                            case "chat:user": {
                                const meta = getArtifactMeta(it.data);
                                const artifactPath = typeof meta.path === "string" ? meta.path : undefined;
                                const continuationKind = it.data.continuation_kind ?? (typeof meta.continuation_kind === "string" ? meta.continuation_kind : undefined);
                                const sourceMessageId =
                                    (typeof meta.message_id === "string" ? meta.message_id : undefined) ??
                                    parseExternalMessageIdFromPath(artifactPath);
                                userMessages.push({
                                    text: it.data.text,
                                    timestamp: Date.parse(it.ts),
                                    attachments: [],
                                    continuationKind,
                                    sourceMessageId,
                                    artifactPath,
                                    historyMeta: meta,
                                });
                                break;
                            }
                            case "artifact:user.attachment": {
                                const dto = it.data as {
                                    payload?: {
                                        filename?: string;
                                        name?: string;
                                        size?: number;
                                        mime?: string | null;
                                        rn?: string | null;
                                        artifact_path?: string;
                                        message_id?: string;
                                        continuation_kind?: string;
                                        meta?: Record<string, unknown>;
                                    };
                                    meta?: Record<string, unknown>;
                                };
                                const payload = dto.payload ?? {};
                                const meta = getArtifactMeta(it.data);
                                const artifactPath = payload.artifact_path;
                                const parsedPath = parseExternalAttachmentPath(artifactPath);
                                userAttachments.push({
                                    name: payload.filename ?? payload.name ?? "file",
                                    size: payload.size ?? 0,
                                    timestamp: Date.parse(it.ts),
                                    mime: payload.mime,
                                    rn: payload.rn,
                                    artifactPath,
                                    sourceMessageId: payload.message_id ?? (typeof meta.message_id === "string" ? meta.message_id : undefined) ?? parsedPath.sourceMessageId,
                                    continuationKind: payload.continuation_kind ?? (typeof meta.continuation_kind === "string" ? meta.continuation_kind : undefined) ?? parsedPath.continuationKind,
                                    historyMeta: meta,
                                });
                                break
                            }
                            case "chat:assistant":
                                {
                                    const meta = getArtifactMeta(it.data);
                                    assistantMessages.push({
                                        text: it.data.text,
                                        timestamp: Date.parse(it.ts),
                                        artifactPath: typeof meta.path === "string" ? meta.path : undefined,
                                        historyMeta: meta,
                                    });
                                    answer = it.data.text
                                }
                                break
                            case "artifact:assistant.file": {
                                const dto = it.data as AssistantFileData;
                                if (isExternalAttachmentArtifact(dto.payload.artifact_path)) {
                                    break;
                                }
                                const tf: FileArtifact = {
                                    artifactType: "file",
                                    timestamp: Date.parse(it.ts),
                                    content: {
                                        filename: dto.payload.filename,
                                        rn: dto.payload.rn,
                                        mime: dto.payload.mime,
                                        description: dto.payload.description,
                                    } as RNFile
                                }
                                turnArtifacts.push(tf)
                                break
                            }
                            case "artifact:conv.thinking.stream": {
                                const dto = it.data as ThinkingStreamData;

                                const startTime = Math.min(...dto.payload.items.map(item => item.ts_first))
                                const finishTime = Math.min(...dto.payload.items.map(item => item.ts_last))

                                const agents: Record<string, string> = {}
                                const agentTimes: Record<string, AgentTiming> = {}

                                dto.payload.items.forEach(it => {
                                    agents[it.agent] = it.text
                                    agentTimes[it.agent] = {
                                        startedAt: it.ts_first,
                                        endedAt: it.ts_last,
                                        active: false,
                                    }
                                })
                                const r: ThinkingArtifact = {
                                    artifactType: "thinking",
                                    timestamp: startTime,
                                    content: {
                                        agentTimes,
                                        agents,
                                        timestamp: startTime,
                                        endedAt: finishTime,
                                    }
                                }
                                turnArtifacts.push(r)
                                break
                            }
                            case "artifact:solver.program.citables": {
                                const dto = it.data as CitationsData;
                                dto.payload.items.forEach(v => {
                                        const t: RichLink = {
                                            url: v.url,
                                            title: v.title,
                                            favicon: v.favicon,
                                            body: v.text
                                        }
                                        const r: CitationArtifact = {
                                            artifactType: "citation",
                                            content: t,
                                            timestamp: Date.parse(it.ts),
                                        }
                                        turnArtifacts.push(r)
                                    }
                                )
                                break
                            }
                            case "artifact:conv.artifacts.stream": {
                                const dto = it.data as ArtifactStreamData;
                                dto.payload.items.forEach(a => {
                                    let processed = false
                                    artifactStreamParsers.forEach(r => {
                                        processed = processed || r.process(a)
                                    })
                                    if (!processed) {
                                        console.warn("unknown artifact stream", a)
                                    }
                                })
                                break
                            }
                            case "artifact:conv.artifacts.events":
                                break
                            case "artifact:turn.log.reaction":
                                break
                            case "artifact:conv.user_shortcuts": {
                                const dto = it.data as FollowUpsData;
                                followUpQuestions.push(...dto.payload.items);
                                break
                            }
                            case "artifact:conv.timeline_text.stream": {
                                const dto = it.data as TimelineTextStreamData;
                                const artifacts = dto.payload.items.map(it => {
                                    const item: TimelineTextArtifact = {
                                        artifactType: TimelineTextArtifactType,
                                        timestamp: it.ts_first,
                                        content: {
                                            name: it.artifact_name,
                                            text: it.text
                                        }
                                    }
                                    return item
                                });
                                turnArtifacts.push(...artifacts)
                                break
                            }
                            default:
                                console.warn("unknown artifact type", it);
                        }
                    })

                    artifactStreamParsers.forEach(r => {
                        turnArtifacts.push(...r.flush())
                    })

                    userMessages.sort((a, b) => a.timestamp - b.timestamp);
                    userAttachments.sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
                    bindAttachmentsToUserMessages(userMessages, userAttachments);

                    const skipRestoredUserMessages =
                        userMessages.length > 0 &&
                        assistantMessages.length === 0 &&
                        userMessages.every((message) => message.continuationKind === "followup");

                    if (!skipRestoredUserMessages) {
                        for (const message of userMessages) {
                            const isFollowup = message.continuationKind === "followup";
                            if (isFollowup) {
                                additionalUserMessages.push(message);
                            } else if (!userMessage) {
                                userMessage = message;
                            } else {
                                additionalUserMessages.push(message);
                            }
                        }
                    }

                    assistantMessages.sort((a, b) => a.timestamp - b.timestamp);

                    turnArtifacts.forEach(a => a.historical = true)

                    previousValue[currentValue.turn_id] = {
                        id: currentValue.turn_id,
                        // Reloaded conversations are historical by definition — every
                        // turn is finished. If the server says the conversation is still
                        // in-progress, `requestConversationStatus` (dispatched right
                        // below) feeds back into the `conversationStatus` reducer, which
                        // is the single source of truth for flipping a turn back to
                        // "inProgress" when appropriate. The previous heuristic
                        // (`i < arr.length - 2 ? "finished" : "inProgress"`) marked the
                        // last two turns of every reload as in-progress, which made
                        // ChatLog render a phantom activity-carrier block alongside the
                        // real assistant message — two "Message/Steps/Sources" tabs per
                        // turn after reload.
                        state: "finished",
                        userMessage: userMessage ?? {text: "", attachments: [], timestamp: 0},
                        ...(additionalUserMessages.length > 0 ? {additionalUserMessages} : {}),
                        ...(assistantMessages.length > 0 ? {assistantMessages} : {}),
                        events: [],
                        artifacts: turnArtifacts,
                        steps: {},
                        followUpQuestions,
                        answer,
                        historical: true
                    }
                    return previousValue
                }, {} as Record<string, ChatTurn>),
                conversationId: conversation.conversation_id,
                conversationTitle:conversation.conversation_title
            }))
            dispatch(requestConversationStatus(conversation.conversation_id))
        }).catch(error => {
            console.error(error)
            dispatch(newConversation())
        })
    }

    return (store) => (next) => (action) => {
        const actionHandlers = async (store: AppStore, action: ConversationURLAction) => {
            const dispatch = store.dispatch
            switch (action.type) {
                case LOAD_CONVERSATION_LIST: {
                    loadConversationList(store)
                    break
                }
                case LOAD_CONVERSATION: {
                    const state = store.getState()
                    const conversationId = (action as LoadConversationsAction).payload;
                    if (conversationId === selectConversationId(state) || (conversationId !== selectConversationId(state) && selectConversationLoading(state)))
                        break;
                    if (conversationId === null) {
                        dispatch(newConversation())
                    } else {
                        dispatch(setConversationLoading(conversationId))
                        if (selectChatConnected(state)) {
                            fetchConv(store, conversationId)
                        } else {
                            dispatch(conversationStatusUpdateRequired())
                        }
                    }
                    break;
                }
                case DELETE_CONVERSATION: {
                    const conversationId = (action as DeleteConversationAction).payload;
                    dispatch(removeConversation(conversationId))
                    const state = store.getState()
                    if (conversationId === selectConversationId(state)) {
                        dispatch(newConversation())
                    }
                    deleteConversationAPI(selectTenant(state), selectProject(state), conversationId).catch(console.error)
                    break
                }
                case chatConnected.type: {
                    const state = store.getState()
                    const conversationLoading = selectConversationLoading(state)
                    if (conversationLoading && selectConversationStatusUpdateRequired(state)) {
                        fetchConv(store, conversationLoading)
                    }
                    break
                }
            }
        }

        actionHandlers(store as AppStore, action as ConversationURLAction).catch(console.error);
        next(action);
    }
}

export default conversationsMiddleware;
