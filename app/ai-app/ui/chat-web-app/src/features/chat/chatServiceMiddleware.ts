import {Middleware, UnknownAction} from "@reduxjs/toolkit";
import {ChatBase, ChatEventHandlers, ChatMessage, ChatRequest} from "../chatController/chatBase.ts";
import {v4 as uuidv4} from "uuid";
import {AppStore, RootState} from "../../app/store.ts";
import SocketIOChat from "../chatController/socketIOChat.ts";
import {
    chatCompleted,
    chatConnected,
    chatDelta,
    chatDisconnected,
    chatStarted,
    clearUserInput,
    conversationStatus,
    disconnect,
    getUserAttachmentFile,
    newTurn, selectChatConnected, selectChatStayConnected, selectConversationId, selectTurnOrder, selectTurns,
    selectUserAttachments, selectUserMessage,
    setConversationId,
    startConnecting,
    stepUpdate,
    turnError
} from "./chatStateSlice.ts";
import {fetchUserProfile, selectUserProfile} from "../profile/profile.ts";
import SSEChat from "../chatController/sseChat.ts";
import {UserAttachmentDescription, UserMessageRequest} from "./chatTypes.ts";
import {selectAuthToken, selectIdToken, setCredentials} from "../auth/authSlice.ts";
import {selectProject, selectTenant} from "./chatSettingsSlice.ts";

type TransportType = "sse" | "websocket";

const CONNECT_CHAT = "chatService/connectChat"

interface ConnectChatAction extends UnknownAction {
    type: typeof CONNECT_CHAT;
}

export const connectChat = (): ConnectChatAction => {
    return {
        type: CONNECT_CHAT
    }
}

const DISCONNECT_CHAT = "chatService/disconnectChat"

interface DisconnectChatAction {
    type: typeof DISCONNECT_CHAT;
}

export const disconnectChat = (): DisconnectChatAction => {
    return {
        type: DISCONNECT_CHAT,
    }
}

const SEND_CHAT_MESSAGE = "chatService/sendMessage"

interface SendChatMessageAction extends UnknownAction {
    type: typeof SEND_CHAT_MESSAGE;
    payload: UserMessageRequest
}

export const sendChatMessage = (payload: UserMessageRequest): SendChatMessageAction => {
    return {
        type: SEND_CHAT_MESSAGE,
        payload
    }
}

const REQUEST_CONVERSATION_STATUS = "chatService/requestConversationStatus"

interface RequestConversationStatusAction extends UnknownAction {
    type: typeof REQUEST_CONVERSATION_STATUS;
    payload: string
}

export const requestConversationStatus = (payload: string): RequestConversationStatusAction => {
    return {
        type: REQUEST_CONVERSATION_STATUS,
        payload
    }
}

type ChatSettingsAction =
    | ReturnType<typeof startConnecting>
    | ReturnType<typeof disconnect>
    | ReturnType<typeof fetchUserProfile.fulfilled>

type ChatAction =
    ConnectChatAction
    | DisconnectChatAction
    | SendChatMessageAction
    | ChatSettingsAction
    | RequestConversationStatusAction

const getConversationHistory = (store: AppStore): ChatMessage[] => {
    const state = store.getState() as RootState;
    const turns = selectTurns(state);
    return selectTurnOrder(state).reduce((previousValue, currentValue) => {
        const turn = turns[currentValue];
        previousValue.push({
            role: "user",
            content: turn.userMessage.text,
            timestamp: new Date(turn.userMessage.timestamp).toISOString(),
            id: turn.userMessage.timestamp,
        });
        return previousValue;
    }, [] as ChatMessage[]);
}

export const chatServiceMiddleware = (transportType: TransportType): Middleware => {
    const sendChatHistory = true
    let transport: ChatBase;

    return (store) => (next) => (action) => {
        const dispatch = store.dispatch
        const createTransport = () => {
            console.debug("create transport", transportType);
            const state = store.getState() as RootState;
            const tenant = selectTenant(state)
            const project = selectProject(state)
            const baseUrl = window.location.origin
            switch (transportType) {
                case "sse":
                    transport = new SSEChat({
                        baseUrl,
                        tenant,
                        project,
                    })
                    break;
                case "websocket":
                    transport = new SocketIOChat({
                        baseUrl,
                        tenant,
                        project,
                    })
                    break;
                default:
                    throw new Error("Unknown transportType");
            }
        }

        const eventHandlers: ChatEventHandlers = {
            onConnect: () => {
                dispatch(chatConnected())
            },
            onDisconnect: () => {
                dispatch(chatDisconnected())
            },
            onChatStart: (env) => {
                dispatch(chatStarted(env))
            },
            onChatComplete: (env) => {
                dispatch(chatCompleted(env))
            },
            onChatDelta: (env) => {
                dispatch(chatDelta(env))
            },
            onChatStep: (env) => {
                dispatch(stepUpdate(env))
            },
            onChatError: (env) => {

            },
            onConvStatus: (env) => {
                dispatch(conversationStatus(env))
            },
            onSessionInfo: (info) => {

            },
            onConnectError: error => {
                console.error("Unable to connect to Chat's web socket. Retry in 5 sec", error)
                setTimeout(() => {
                    (store as AppStore).dispatch(fetchUserProfile())
                }, 5000)
            }
        }

        const tryConnect = (store: AppStore) => {
            const state = store.getState() as RootState;
            if (selectChatConnected(state) || !selectChatStayConnected(state)) {
                return;
            }
            const sessionId = selectUserProfile(state)?.sessionId
            if (!sessionId) {
                store.dispatch(fetchUserProfile())
                return
            }
            transport.eventHandlers = eventHandlers;
            transport.authToken = selectAuthToken(state);
            transport.idToken = selectIdToken(state);
            transport.connect(sessionId);
        }

        const actionHandlers = async (store: AppStore, action: ChatAction) => {
            const dispatch = store.dispatch
            switch (action.type) {
                case CONNECT_CHAT: {
                    dispatch(startConnecting())
                    if (!transport) createTransport()
                    tryConnect(store)
                    break;
                }
                case fetchUserProfile.fulfilled.type:
                    tryConnect(store)
                    break;
                case DISCONNECT_CHAT:
                    transport.disconnect()
                    break;
                case SEND_CHAT_MESSAGE: {
                    const state = store.getState() as RootState;
                    const turnId = `turn_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
                    const request = (action as SendChatMessageAction).payload

                    const message = request.message ?? selectUserMessage(state)

                    let files: File[] | null
                    let attachments: UserAttachmentDescription[]

                    if (request.files) {
                        files = request.files
                        attachments = request.files.map((file) => {
                            return {
                                name: file.name,
                                size: file.size,
                            }
                        })
                    } else if (selectUserAttachments(state)) {
                        files = selectUserAttachments(state).map((attachment) => {
                            const file = getUserAttachmentFile(attachment.fileKey)
                            if (!file) {
                                throw new Error(`no user attachment with this key: ${attachment.fileKey}`);
                            }
                            return file
                        })
                        attachments = selectUserAttachments(state)
                    } else {
                        files = null
                        attachments = []
                    }

                    if (!message && attachments.length === 0) {
                        //do not send empty message
                        return;
                    }

                    dispatch(newTurn({
                        id: turnId,
                        state: "new",
                        userMessage: message,
                        attachments
                    }))

                    let conversationId = selectConversationId(state)
                    if (!conversationId) {
                        conversationId = uuidv4()
                        dispatch(setConversationId(conversationId))
                    }

                    const chatRequest: ChatRequest = {
                        message,
                        chat_history: sendChatHistory ? getConversationHistory(store) : undefined,
                        project: selectProject(state),
                        tenant: selectTenant(state),
                        turn_id: turnId,
                        //bundle_id: "", //todo: add bundle
                    }

                    transport.sendChatMessage(conversationId, chatRequest, files).then(() => {
                        dispatch(clearUserInput())
                    }).catch(err => {
                        console.error(err)
                        dispatch(turnError(err))
                    })
                    break;
                }
                case REQUEST_CONVERSATION_STATUS:
                    transport.requestConvStatus((action as RequestConversationStatusAction).payload).catch(console.error)
                    break
                case setCredentials.type: {
                    const state = store.getState() as RootState;
                    if (transport) {
                        transport.authToken = selectAuthToken(state)
                        transport.idToken = selectIdToken(state)
                    }
                    break
                }
            }
        }

        next(action);
        actionHandlers(store as AppStore, action as ChatAction).catch(console.error);
    }
}