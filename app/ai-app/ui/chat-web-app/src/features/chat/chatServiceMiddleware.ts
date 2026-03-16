import {Middleware, UnknownAction} from "@reduxjs/toolkit";
import {
    ChatBase,
    ChatEventHandlers,
    ChatMessage,
    ChatRequest,
    ChatServiceEnvelope
} from "../chatController/chatBase.ts";
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
    newTurn,
    selectChatConnected,
    selectChatStayConnected,
    selectConversationId,
    selectTurnOrder,
    selectTurns,
    selectUserAttachments,
    selectUserMessage,
    setConversationId,
    startConnecting,
    stepUpdate,
    turnError
} from "./chatStateSlice.ts";
import {fetchUserProfile, selectUserProfile} from "../profile/profile.ts";
import SSEChat from "../chatController/sseChat.ts";
import {UserAttachmentDescription, UserMessageRequest} from "./chatTypes.ts";
import {selectAuthToken, selectIdToken, setCredentials} from "../auth/authSlice.ts";
import {selectIdTokenHeaderName, selectProject, selectTenant} from "./chatSettingsSlice.ts";
import {NotificationType} from "../popupNotifications/types.ts";
import {pushNotification} from "../popupNotifications/popupsSlice.ts";

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

        const handleServiceMessage = (env: ChatServiceEnvelope) => {
            const eventType = env.type;
            const data = env.data;
            const rateLimit = data?.rate_limit;

            switch (eventType) {
                case "rate_limit.warning": {
                    const messagesRemaining = rateLimit?.messages_remaining ?? null;
                    const tokenRemaining = rateLimit?.total_token_remaining ?? null;
                    const usagePercentage = rateLimit?.usage_percentage ?? null;


                    let message: string;
                    let notificationType: NotificationType = "info";

                    if (messagesRemaining !== null && messagesRemaining === 0) {
                        message = "You have no messages remaining in your current quota.";
                        notificationType = "error";
                    } else if (messagesRemaining !== null && messagesRemaining === 1) {
                        message = "You have 1 message remaining in your current quota.";
                        notificationType = "warning";
                    } else if (messagesRemaining !== null && messagesRemaining <= 5) {
                        message = `You have ${messagesRemaining} messages remaining in your current quota.`;
                        notificationType = "warning";
                    } else if (tokenRemaining !== null && tokenRemaining < 133_333) {
                        const tokensK = Math.floor(tokenRemaining / 1000);
                        message = `You're running low on tokens (~${tokensK}K remaining). Consider upgrading.`;
                        notificationType = "warning";
                    } else if (usagePercentage !== null && usagePercentage >= 80) {
                        message = `You've used ${Math.round(usagePercentage)}% of your quota.`;
                        if (usagePercentage >= 95) {
                            notificationType = "error";
                        }
                    } else {
                        message = "You're approaching your usage limit.";
                        notificationType = "warning";
                    }
                    dispatch(pushNotification({
                        type: notificationType,
                        text: message,
                    }))
                    break;
                }
                case "rate_limit.denied": {
                    const retryAfterHours = rateLimit?.retry_after_hours ?? null;
                    const reason = data.reason as string | undefined;

                    let message: string;

                    if (retryAfterHours && retryAfterHours > 0) {
                        const hourText = retryAfterHours === 1 ? "1 hour" : `${retryAfterHours} hours`;
                        message = `You've reached your usage limit. Try again in about ${hourText}.`;
                    } else if (reason === "concurrency" || reason?.includes("concurrent")) {
                        message = "You have too many requests running at once. Please wait for one to complete.";
                    } else if (reason === "quota_lock_timeout") {
                        message = "Too many requests are being processed right now. Please try again in a moment.";
                    } else if (reason?.includes("tokens")) {
                        message = "You've reached your token limit. Try again later or upgrade your plan.";
                    } else if (reason?.includes("requests")) {
                        message = "You've reached your request limit. Try again later or upgrade your plan.";
                    } else {
                        message = "You've reached your usage limit. Please try again later.";
                    }

                    dispatch(pushNotification({
                        type: "error",
                        text: message,
                    }))
                    break;
                }
                case "rate_limit.project_exhausted": {
                    const hasPersonalBudget = data.has_personal_budget ?? false;
                    const usdShort = data.usd_short as number | null | undefined;

                    let message: string;

                    if (hasPersonalBudget && usdShort && usdShort > 0) {
                        message = `Project budget exhausted. You need $${usdShort.toFixed(2)} more in personal credits to run this request.`;
                    } else if (!hasPersonalBudget) {
                        message = "Project budget exhausted. Please contact your administrator to add funds.";
                    } else {
                        message = "Project budget exhausted. Unable to process this request.";
                    }

                    dispatch(pushNotification({
                        type: "error",
                        text: message,
                    }))
                    break;
                }
                default:
                    console.warn("unknown eventType", env);
            }
        }
        //
        //     // ========================================================================
        //     // 4) LANE SWITCH - Informational (don't show to user, just log)
        //     // ========================================================================
        //     if (eventType === "rate_limit.lane_switch") {
        //         const laneFrom = data.lane_from;
        //         const laneTo = data.lane_to;
        //         const reason = data.reason;
        //
        //         console.info(`[Economics] Lane switch: ${laneFrom} → ${laneTo} (${reason})`);
        //
        //         // Optional: Show very subtle info notification
        //         // Uncomment if you want users to be aware they're using personal credits:
        //         /*
        //         if (laneTo === "paid") {
        //             setRateLimitNotice({
        //                 kind: "info",
        //                 message: "Using your personal credits for this request.",
        //                 retryAfterHours: null,
        //                 messagesRemaining: null,
        //             });
        //
        //             // Auto-dismiss after 5 seconds
        //             setTimeout(() => {
        //                 setRateLimitNotice(null);
        //             }, 5000);
        //         }
        //         */
        //
        //         return;
        //     }
        //
        //     // ========================================================================
        //     // 5) USER UNDERFUNDED - Internal event (don't show to user)
        //     // ========================================================================
        //     if (eventType === "economics.user_underfunded_absorbed") {
        //         const uncoveredTokens = data.user_uncovered_tokens;
        //         const uncoveredUsd = data.user_uncovered_usd;
        //
        //         console.info(
        //             `[Economics] User underfunded: ${uncoveredTokens} tokens (~$${uncoveredUsd?.toFixed(4)}) absorbed by project`
        //         );
        //         return;
        //     }
        //
        //     // ========================================================================
        //     // 6) SNAPSHOT - Just log (debugging)
        //     // ========================================================================
        //     if (eventType === "rate_limit.snapshot") {
        //         if (logChatUpdates()) {
        //             console.debug("[Economics] Rate limit snapshot:", rl || data);
        //         }
        //         return;
        //     }
        //     // ========================================================================
        //     // X) AI SERVICES QUOTA (provider / platform throttling)
        //     // ========================================================================
        //     if (eventType === "rate_limit.ai_services_quota") {
        //         // const msg = String(data.message ?? "AI services are temporarily at capacity. Please try again later.");
        //         const msg = "We are a bit overloaded temporarily. Please try again later."
        //
        //         setRateLimitNotice({
        //             kind: "error",
        //             message: msg,
        //             retryAfterHours: null,
        //             messagesRemaining: null,
        //         });
        //
        //         // attach to current turn; backend sets show_in_timeline=false
        //         attachTurnError(msg, data.show_in_timeline ?? false);
        //         return;
        //     }
        //     // ========================================================================
        //     // X) ATTACHMENT FAILURE (upload rejected)
        //     // ========================================================================
        //     if (eventType === "rate_limit.attachment_failure") {
        //         const msg = String(data.message ?? "Attachment was rejected.");
        //
        //         attachUserTurnError(new UserAttachmentError(msg));
        //         return;
        //     }
        //     // ========================================================================
        //     // Fallback: Unknown economics event
        //     // ========================================================================
        //     if (eventType?.startsWith("rate_limit.") || eventType?.startsWith("economics.")) {
        //         console.warn("[Economics] Unhandled event:", eventType, data);
        //     }
        // }

        //move parsers and other logic here
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
                dispatch(turnError({turnId: env.conversation.turn_id, error: env.data.error}))
            },
            onConvStatus: (env) => {
                dispatch(conversationStatus(env))
            },
            // onSessionInfo: (info) => {
            //
            // },
            onChatService: (env) => {
                handleServiceMessage(env)
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
            transport.idHeaderName = selectIdTokenHeaderName(state);
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

                    console.info(
                        "[chat.send] sending chat message",
                        {
                            conversationId,
                            turnId,
                            hasMessage: Boolean(message),
                            attachments: attachments.length,
                            ts: new Date().toISOString(),
                        }
                    )

                    transport.sendChatMessage(conversationId, chatRequest, files).then(() => {
                        dispatch(clearUserInput())
                    }).catch(error => {
                        console.error(error)
                        dispatch(turnError({turnId, error}))
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
