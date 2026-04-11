import {Middleware, UnknownAction} from "@reduxjs/toolkit";
import {ChatBase, ChatEventHandlers, ChatMessage, ChatRequest,} from "../chatController/chatBase.ts";
import {v4 as uuidv4} from "uuid";
import {AppDispatch, AppStore, RootState} from "../../app/store.ts";
import SocketIOChat from "../chatController/socketIOChat.ts";
import {
    chatCompleted,
    chatConnected,
    chatDelta,
    chatDisconnected,
    chatStarted,
    clearUserAttachments,
    clearUserInput,
    conversationStatus,
    disconnect,
    getUserAttachmentFile,
    lockInput,
    newTurn,
    selectChatConnected,
    selectChatStayConnected,
    selectConversationId,
    selectCurrentTurn,
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
import {
    selectIdTokenHeaderName,
    selectProject,
    selectStreamIdHeaderName,
    selectTenant,
    selectUseAuthCookies
} from "./chatSettingsSlice.ts";
import {pushNotification} from "../popupNotifications/popupsSlice.ts";
import {NotificationType} from "../popupNotifications/types.ts";
import {
    ChatServiceEnvelope,
    ChatServiceMessageTrait,
    PopupShow,
    PopupShowType,
    RateLimitPayload,
    UserInputAttachmentRejectedType,
    UserInputLockTraitType
} from "./serviceEventTypes.ts";
import {selectCurrentBundle} from "../bundles/bundlesSlice.ts";

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

// @ts-expect-error store is unused for now
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const processServiceMessageTrait = (trait: ChatServiceMessageTrait, store: AppStore, dispatch: AppDispatch) => {
    switch (trait.type) {
        case PopupShowType:
            dispatch(pushNotification((trait as PopupShow).data))
            break;
        case UserInputLockTraitType:
            dispatch(lockInput())
            break;
        case UserInputAttachmentRejectedType:
            dispatch(clearUserAttachments())
            break;
    }
}

export const chatServiceMiddleware = (transportType: TransportType): Middleware => {
    const sendChatHistory = true
    let transport: ChatBase;

    return (store) => (next) => (action) => {
        const dispatch = store.dispatch as AppDispatch
        const createTransport = () => {
            console.debug("create transport", transportType);
            const state = store.getState() as RootState;
            const tenant = selectTenant(state)
            const project = selectProject(state)
            const baseUrl = window.location.origin
            const streamIdHeaderName = selectStreamIdHeaderName(state)
            switch (transportType) {
                case "sse":
                    transport = new SSEChat({
                        baseUrl,
                        tenant,
                        project,
                        streamIdHeaderName
                    })
                    break;
                case "websocket":
                    transport = new SocketIOChat({
                        baseUrl,
                        tenant,
                        project,
                        streamIdHeaderName
                    })
                    break;
                default:
                    throw new Error("Unknown transportType");
            }
        }

        const fallbackRateLimitMessage = (rateLimit: RateLimitPayload | undefined, data: Record<string, unknown>): string => {
            const retryAfterSec = rateLimit?.retry_after_sec ?? null;
            const reason = data.reason as string | undefined;
            if (retryAfterSec && retryAfterSec > 0 && rateLimit) {
                const resetText = rateLimit.reset_text ?? (() => {
                    const resetAt = new Date(Date.now() + retryAfterSec * 1000);
                    const now = new Date();
                    const timeStr = resetAt.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
                    const tomorrow = new Date(now);
                    tomorrow.setDate(tomorrow.getDate() + 1);
                    if (resetAt.toDateString() === now.toDateString()) return `today at ${timeStr}`;
                    if (resetAt.toDateString() === tomorrow.toDateString()) return `tomorrow at ${timeStr}`;
                    return `on ${resetAt.toLocaleDateString([], {month: "long", day: "numeric"})} at ${timeStr}`;
                })();
                return `You've reached your usage limit. Your quota resets ${resetText}.`;
            }
            if (reason === "concurrency" || reason?.includes("concurrent")) return "You have too many requests running at once. Please wait for one to complete.";
            if (reason === "quota_lock_timeout") return "Too many requests are being processed right now. Please try again in a moment.";
            if (reason?.includes("token")) return "You've reached your token limit. Try again later or upgrade your plan.";
            if (reason?.includes("request")) return "You've reached your request limit. Try again later or upgrade your plan.";
            return "You've reached your usage limit. Please try again later.";
        };

        const handleServiceMessage = (env: ChatServiceEnvelope) => {
            const eventType = env.type;
            const data = env.data;
            const rateLimit = data?.rate_limit;

            const traits: ChatServiceMessageTrait[] = []
            const lockInputOnError = (notificationType: NotificationType) => {
                if (notificationType === "error") {
                    traits.push({type: UserInputLockTraitType})
                }
            }

            const showPopup = (type: NotificationType, text: string) => {
                traits.push({type: PopupShowType, data: {type, text}} as PopupShow)
            }

            //todo: remove hardcoded event types when traits system will be available

            switch (eventType) {
                case "rate_limit.warning": {
                    const serverMessage = rateLimit?.user_message ?? null;
                    const message = serverMessage ?? fallbackRateLimitMessage(rateLimit, data);
                    const notificationType = (rateLimit?.notification_type ?? "warning") as NotificationType;
                    showPopup(notificationType, message)
                    lockInputOnError(notificationType)
                    break;
                }
                case "rate_limit.denied": {
                    const serverMessage = rateLimit?.user_message ?? (data.user_message as string | undefined) ?? null;
                    const message = serverMessage ?? fallbackRateLimitMessage(rateLimit, data);
                    const notificationType = (rateLimit?.notification_type ?? "error") as NotificationType;
                    showPopup(notificationType, message)
                    lockInputOnError(notificationType)
                    break;
                }
                case "rate_limit.post_run_exceeded": {
                    const serverMessage = rateLimit?.user_message ?? null;
                    const message = serverMessage ?? fallbackRateLimitMessage(rateLimit, data);
                    const notificationType = (rateLimit?.notification_type ?? "warning") as NotificationType;
                    showPopup(notificationType, message)
                    lockInputOnError(notificationType)
                    break;
                }
                case "rate_limit.no_funding": {
                    const serverMessage = (data.user_message as string | undefined) ?? null;
                    const message = serverMessage ?? "This service is not available for your account type. Please contact support.";
                    const notificationType = ((data.notification_type as string | undefined) ?? "error") as NotificationType;
                    showPopup(notificationType, message)
                    lockInputOnError(notificationType)
                    break;
                }
                case "rate_limit.subscription_exhausted": {
                    const serverMessage = (data.user_message as string | undefined) ?? null;
                    const message = serverMessage ?? "Your subscription balance is exhausted. Please top up your balance to continue.";
                    const notificationType = ((data.notification_type as string | undefined) ?? "error") as NotificationType;
                    showPopup(notificationType, message)
                    lockInputOnError(notificationType)
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

                    const notificationType = "error"

                    showPopup(notificationType, message)

                    lockInputOnError(notificationType)
                    traits.push({type: UserInputAttachmentRejectedType})
                    break;
                }
                case "rate_limit.attachment_failure": {
                    const serverMessage = (data.user_message as string | undefined) ?? null;
                    const message = serverMessage ?? "Attachment was rejected.";
                    const notificationType = ((data.notification_type as string | undefined) ?? "error") as NotificationType;
                    showPopup(notificationType, message)
                    break;
                }
                case "rate_limit.lane_switch":
                case "economics.user_underfunded_absorbed":
                    console.info(env)
                    break;
                default:
                    console.warn("unknown eventType", env);
            }

            traits.forEach((trait) => processServiceMessageTrait(trait, store as AppStore, dispatch))
        }

        //move parsers and other logic here
        const eventHandlers: ChatEventHandlers = {
            onConnect: () => {
                dispatch(chatConnected(transport.streamId))
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
            const dispatch = store.dispatch
            if (selectChatConnected(state) || !selectChatStayConnected(state)) {
                return;
            }
            const sessionId = selectUserProfile(state)?.sessionId
            if (!sessionId) {
                dispatch(fetchUserProfile())
                return
            }
            transport.eventHandlers = eventHandlers;
            if (!selectUseAuthCookies(state)) {
                transport.authToken = selectAuthToken(state);
                transport.idToken = selectIdToken(state);
                transport.idHeaderName = selectIdTokenHeaderName(state);
            }
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
                    const request = (action as SendChatMessageAction).payload
                    const activeTurn = selectCurrentTurn(state)
                    const continuationKind = request.continuationKind ?? (activeTurn ? "followup" : "regular")
                    const isContinuation = continuationKind === "followup" || continuationKind === "steer"
                    const targetTurnId = request.targetTurnId ?? activeTurn?.id ?? undefined
                    const turnId = `turn_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

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

                    if (!message && attachments.length === 0 && continuationKind !== "steer") {
                        //do not send empty message
                        return;
                    }

                    if (!isContinuation) {
                        dispatch(newTurn({
                            id: turnId,
                            state: "new",
                            userMessage: message,
                            attachments
                        }))
                    }

                    let conversationId = selectConversationId(state)
                    if (!conversationId) {
                        conversationId = uuidv4()
                        dispatch(setConversationId(conversationId))
                    }

                    const chatRequest: ChatRequest = {
                        message,
                        chat_history: !isContinuation && sendChatHistory ? getConversationHistory(store) : undefined,
                        project: selectProject(state),
                        tenant: selectTenant(state),
                        turn_id: turnId,
                        bundle_id: selectCurrentBundle(state) ?? undefined,
                        ...(isContinuation ? {
                            message_kind: continuationKind,
                            continuation_kind: continuationKind,
                            active_turn_id: targetTurnId,
                            target_turn_id: targetTurnId,
                            ...(continuationKind === "followup" ? {followup: true} : {}),
                            ...(continuationKind === "steer" ? {steer: true} : {}),
                        } : {})
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
                        if (isContinuation) {
                            dispatch(pushNotification({
                                type: "info",
                                text: continuationKind === "steer"
                                    ? (message ? "Steer sent to the in-progress turn." : "Stop signal sent to the in-progress turn.")
                                    : "Follow-up sent to the in-progress turn.",
                            }))
                        }
                    }).catch(error => {
                        console.error(error)
                        if (isContinuation) {
                            dispatch(pushNotification({
                                type: "error",
                                text: error instanceof Error ? error.message : String(error),
                            }))
                        } else {
                            dispatch(turnError({turnId, error}))
                        }
                    })
                    break;
                }
                case REQUEST_CONVERSATION_STATUS:
                    transport.requestConvStatus((action as RequestConversationStatusAction).payload).catch(console.error)
                    break
                case setCredentials.type: {
                    const state = store.getState() as RootState;
                    if (transport && !selectUseAuthCookies(state)) {
                        transport.authToken = selectAuthToken(state);
                        transport.idToken = selectIdToken(state);
                        transport.idHeaderName = selectIdTokenHeaderName(state);
                    }
                    break
                }
            }
        }

        next(action);
        actionHandlers(store as AppStore, action as ChatAction).catch(console.error);
    }
}
