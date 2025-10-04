/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

// Chat.tsx
import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {
    BookOpen,
    Bot,
    Database,
    GripVertical,
    Loader,
    LogOut,
    Search,
    Server,
    Settings,
    Sparkles,
    Wifi,
    WifiOff,
    X
} from "lucide-react";
import {
    ChatCompleteEnvelope,
    ChatDeltaEnvelope,
    ChatErrorEnvelope,
    ChatEventHandlers,
    ChatRequest,
    ChatStartEnvelope,
    ChatStepEnvelope,
    downloadBlob,
    getChatServiceSingleton,
    getResourceByRN,
    getSuggestedQuestions,
    SocketChatOptions,
    UseSocketChatReturn,
    WireChatMessage,
} from "./ChatService";

import {useAuthManagerContext} from "../auth/AuthManager";
import {
    getChatSocketAddress,
    getChatSocketSocketIOPath,
    getKBAPIBaseAddress,
    getWorkingScope,
    showExampleAssistantFileSteps,
    showExampleAssistantSourceSteps
} from "../../AppConfig";

import {
    AgentTiming,
    AssistantAnswerEvent,
    AssistantChatMessage,
    AssistantThinkingEvent,
    AssistantThinkingItem,
    BundleInfo,
    ChatLogItem,
    ChatMessage,
    createAssistantChatStep,
    createDownloadItem,
    createSourceLinks,
    DownloadItem,
    EmbedderInfo,
    ModelInfo,
    RichLink,
    StepUpdate,
    UserChatMessage,
} from "./types/chat";
import ChatInterface, {ChatInterfaceContext, ChatInterfaceContextValue} from "./ChatInterface/ChatInterface.tsx";

import {handleContentDownload, openUrlSafely} from "../shared.ts";
import {ChatConfigPanel} from "./config/ChatConfigPanel.tsx";
import {ConfigProvider, useConfigProvider} from "./ChatConfigProvider.tsx";
import KBPanel from "../kb/KBPanel.tsx";
import {SystemMonitorPanel} from "../monitoring/monitoring.tsx";
import EnhancedKBSearchResults from "./SearchResults.tsx";
import {getExampleAssistantFileSteps, getExampleAssistantSourceSteps} from "./ChatInterface/debug.ts";

/* ============================
   Local Socket.IO hook (v1)
   ============================ */

export function useSocketChat(options: SocketChatOptions): UseSocketChatReturn {
    const [isConnected, setIsConnected] = useState(false);
    const [isConnecting, setIsConnecting] = useState(false);
    const [socketId, setSocketId] = useState<string | undefined>(undefined);
    const [connectionError, setConnectionError] = useState<string | null>(null);

    const authContext = useAuthManagerContext();

    const stableOpts = useMemo<SocketChatOptions>(
        () => ({
            baseUrl: options.baseUrl,
            path: options.path ?? "/socket.io",
            reconnectionAttempts: options.reconnectionAttempts ?? 10,
            timeout: options.timeout ?? 10000,
            project: options.project,
            tenant: options.tenant,
            namespace: options.namespace ?? "/",
            authContext,
        }),
        [options.baseUrl, options.path, options.reconnectionAttempts, options.timeout, options.project, options.tenant, options.namespace]
    );

    const service = useMemo(() => getChatServiceSingleton(stableOpts), [stableOpts]);

    const connect = useCallback(
        async (handlers: ChatEventHandlers, ac = authContext) => {
            setIsConnecting(true);
            setConnectionError(null);

            const enhancedHandlers: ChatEventHandlers = {
                ...handlers,
                onConnect: () => {
                    setIsConnected(true);
                    setIsConnecting(false);
                    setSocketId(service.socketId);
                    setConnectionError(null);
                    handlers.onConnect?.();
                },
                onDisconnect: (reason: string) => {
                    setIsConnected(false);
                    setSocketId(undefined);
                    handlers.onDisconnect?.(reason);
                },
                onConnectError: (error: Error) => {
                    setIsConnecting(false);
                    setConnectionError(error.message);
                    handlers.onConnectError?.(error);
                },
            };

            await service.connect(enhancedHandlers, ac);
        },
        [service, authContext]
    );

    const disconnect = useCallback(() => {
        service.disconnect();
        setIsConnected(false);
        setIsConnecting(false);
        setSocketId(undefined);
        setConnectionError(null);
    }, [service]);

    const sendMessage = useCallback(
        (request: ChatRequest, attachments?: File[]) => {
            if (!service.connected) throw new Error("Not connected to chat service");
            service.sendChatMessage(request, attachments);
        },
        [service]
    );

    const ping = useCallback(() => {
        if (!service.connected) throw new Error("Not connected to chat service");
        service.ping();
    }, [service]);

    return {isConnected, isConnecting, socketId, connect, disconnect, sendMessage, ping, connectionError};
}

// -----------------------------------------------------------------------------
// Helper: KB search results wrapper
// -----------------------------------------------------------------------------
const UpdatedSearchResultsHistory = ({searchHistory, onClose, kbEndpoint}: {
    searchHistory: any[];
    onClose: () => void;
    kbEndpoint: string;
}) => {
    return (
        <EnhancedKBSearchResults
            searchResults={searchHistory}
            onClose={onClose}
            kbEndpoint={kbEndpoint}
        />
    );
};

const SingleChatApp: React.FC = () => {
    const configProvider = useMemo(() => new ConfigProvider({
        storageKey: 'ai_assistant_config_v1',
        encryptionKey: 'ai_config_secure_key'
    }), []);

    const {
        config,
        isValid: isConfigValid,
        validationErrors,
        updateConfig,
        setConfigValue
    } = useConfigProvider(configProvider);

    const authContext = useAuthManagerContext();
    const workingScope = getWorkingScope();
    const project = workingScope.project;
    const tenant = workingScope.tenant;

    // Socket
    const {
        isConnected: isSocketConnected,
        isConnecting: isSocketConnecting,
        socketId,
        connect: connectSocket,
        disconnect: disconnectSocket,
        sendMessage: sendSocketMessage
    } =
        useSocketChat({
            baseUrl: getChatSocketAddress(),
            path: getChatSocketSocketIOPath(),
            authContext,
            project,
            tenant,
            reconnectionAttempts: Infinity
        });

    // Panels and header meta (decoupled)
    const [showConfig, setShowConfig] = useState<boolean>(() => config.show_config);
    const [showKB, setShowKB] = useState<boolean>(false);
    const [showKbResults, setShowKbResults] = useState<boolean>(false);
    const [showSystemMonitor, setShowSystemMonitor] = useState<boolean>(false);

    const [kbSearchHistory, setKbSearchHistory] = useState<any[]>([]);
    const [newKbSearchCount, setNewKbSearchCount] = useState<number>(0);

    const [headerModel, setHeaderModel] = useState<ModelInfo | undefined>();
    const [headerEmbedder, setHeaderEmbedder] = useState<EmbedderInfo | undefined>();
    const [headerBundle, setHeaderBundle] = useState<BundleInfo | undefined>();

    // Sync toggles to persisted config
    useEffect(() => {
        setShowConfig(config.show_config);
    }, [config.show_config]);

    const handleShowConfigChange = useCallback((show: boolean) => {
        setShowConfig(show);
        setConfigValue('show_config', show);
    }, [setConfigValue]);

    // KB helpers
    const handleKbSearchResults = useCallback((searchResponse: any, isAutomatic: boolean = true) => {
        const enrichedResponse = {
            ...searchResponse,
            searchType: isAutomatic ? 'automatic' : 'manual',
            timestamp: new Date()
        };
        setKbSearchHistory(prev => [enrichedResponse, ...prev.slice(0, 9)]);
        setNewKbSearchCount(prev => prev + 1);
        setTimeout(() => setNewKbSearchCount(0), 5000);
    }, []);
    const handleShowKbResults = useCallback(() => {
        setShowKbResults(true);
        setNewKbSearchCount(0);
    }, []);
    const handleCloseKbResults = useCallback(() => setShowKbResults(false), []);

    // Connect Socket.IO
    const didConnectRef = useRef(false);
    useEffect(() => {
        if (didConnectRef.current) return;
        didConnectRef.current = true;

        let cancelled = false;

        const waitForToken = async (timeoutMs = 2000, intervalMs = 100) => {
            const start = Date.now();
            while (!cancelled) {
                if (authContext?.getUserAuthToken?.()) return true;
                if (Date.now() - start >= timeoutMs) return false;
                await new Promise(r => setTimeout(r, intervalMs));
            }
            return false;
        };

        (async () => {
            try {
                await waitForToken();
                await connectSocket(chatEventHandlers, authContext);
            } catch (e) {
                console.error("Failed to initialize socket:", e);
                setTimeout(() => connectSocket(chatEventHandlers, authContext).catch(console.error), 750);
            }
        })();

        return () => {
            cancelled = true;
            disconnectSocket();
            didConnectRef.current = false;
        };
    }, []);

    // Suggested questions
    const [updatingQustions, setUpdatingQustions] = useState<boolean>(false);
    const [quickQuestions, setQuickQuestions] = useState<string[]>([]);
    useEffect(() => {
        setUpdatingQustions(true);
        getSuggestedQuestions(tenant, project, authContext, headerBundle?.id)
            .then(setQuickQuestions)
            .catch(console.error)
            .finally(() => setUpdatingQustions(false));
    }, [project, tenant, headerBundle]);


    const connectionStatus = useMemo(() => {
        if (isSocketConnecting) return {
            icon: <Loader size={14} className="animate-spin"/>,
            text: 'Connecting...',
            color: 'text-yellow-600 bg-yellow-50'
        };
        if (isSocketConnected) return {icon: <Wifi size={14}/>, text: 'Connected', color: 'text-green-600 bg-green-50'};
        return {icon: <WifiOff size={14}/>, text: 'Disconnected', color: 'text-red-600 bg-red-50'};
    }, [isSocketConnected, isSocketConnecting]);

    // Logout
    const handleLogout = useCallback(async () => {
        try {
            disconnectSocket();
            await authContext.logout();
        } catch (e) {
            console.error("Logout error:", e);
        }
    }, [disconnectSocket, authContext]);

    const [userMessages, setUserMessages] = useState<Map<string, UserChatMessage>>(new Map([['_greeting_0', new AssistantChatMessage(
        0,
        "Hello! I'm your AI assistant application and currently under active development.",
        new Date(),
        {
            turn_id: '_greeting_0'
        }
    )]]))

    const updatedTurns = useRef<string[]>([])

    const [lastTurnID, setLastTurnID] = useState<string | null>(null);
    const [isProcessing, setIsProcessing] = useState<boolean>(false);

    const [thinkingItemEvents, setThinkingItemEvents] = useState<Map<string, AssistantThinkingEvent[]>>(new Map())
    const [assistantAnswerEvents, setAssistantAnswerEvents] = useState<Map<string, AssistantAnswerEvent[]>>(new Map());
    const [finalAssistantAnswers, setFinalAssistantAnswers] = useState<Map<string, string>>(new Map());
    const [assistantErrors, setAssistantErrors] = useState<Map<string, string>>(new Map());

    const thinkingItemsMap = useRef<Map<string, AssistantThinkingItem>>(new Map())
    const assistantThinkingItems = useMemo(() => {
        const result = new Map(thinkingItemsMap.current)
        for (const turnId of updatedTurns.current) {
            let item: AssistantThinkingItem
            const turnThinkingEvents = thinkingItemEvents.get(turnId)
            if (!turnThinkingEvents || !turnThinkingEvents.length)
                continue

            const completionEvent = turnThinkingEvents.find(val => val.completed)
            const completed = !!completionEvent
            const completedAt = completionEvent?.timestamp

            const agents: Record<string, string> = {}
            const agentTimes: Record<string, AgentTiming> = {}

            turnThinkingEvents.forEach(val => {
                agents[val.agent] = (agents[val.agent] ?? "") + val.text
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

            if (result.has(turnId)) {
                const prevItem = result.get(turnId) as AssistantThinkingItem;
                item = new AssistantThinkingItem(
                    prevItem.id,
                    prevItem.timestamp,
                    prevItem.turn_id,
                    !completed,
                    completedAt,
                    agents,
                    agentTimes,
                )
            } else {
                if (completed) {
                    console.warn("AssistantThinkingItem completed without prior delta(s). Turn", turnId)
                }
                const timestamp = Math.min(...turnThinkingEvents.map((val) => val.timestamp.getTime()))
                item = new AssistantThinkingItem(
                    timestamp,
                    new Date(timestamp),
                    turnId,
                    !completed,
                    completedAt,
                    agents,
                    agentTimes,
                )
            }
            result.set(turnId, item);
        }
        thinkingItemsMap.current = result
        return result;
    }, [thinkingItemEvents]);

    const [currentSteps, setCurrentSteps] = useState<StepUpdate[]>([]);

    const assistantMessagesMap = useRef<Map<string, AssistantChatMessage>>(new Map())
    const assistantMessages = useMemo(() => {
        const result = new Map(assistantMessagesMap.current);
        for (const turnId of updatedTurns.current) {
            const answerEvents = assistantAnswerEvents.get(turnId)
            const thinkingEvents = thinkingItemEvents.get(turnId)
            const finalAnswer = finalAssistantAnswers.get(turnId)
            const assistantError = assistantErrors.get(turnId)
            const turnSteps = currentSteps.filter(item => item.turn_id === turnId)

            if ((!answerEvents || answerEvents.length === 0)
                && (!thinkingEvents || thinkingEvents.length === 0)
                && (!turnSteps || turnSteps.length === 0)
                && finalAnswer === undefined
                && assistantError === undefined
            ) {
                console.debug("No data assistant data for turn", turnId)
                continue
            }

            const hasError = assistantError !== undefined
            const answer = hasError
                ? `Sorry, I have encountered an error: ${assistantError}`
                : finalAnswer ?? answerEvents?.map(item => item.text).join("") ?? ""

            let item: AssistantChatMessage
            const tsCandidates: number[] = [];
            if (thinkingEvents && thinkingEvents.length > 0) {
                tsCandidates.push(...thinkingEvents.map(item => item.timestamp.getTime()))
            }
            if (answerEvents && answerEvents.length > 0) {
                tsCandidates.push(...answerEvents.map(item => item.timestamp.getTime()))
            }
            if (turnSteps && turnSteps.length > 0) {
                tsCandidates.push(...turnSteps.map(item => item.timestamp.getTime()))
            }
            const timestamp = Math.min(...tsCandidates)
            if (result.has(turnId)) {
                const msg = result.get(turnId) as AssistantChatMessage
                item = new AssistantChatMessage(
                    timestamp,
                    answer,
                    new Date(timestamp),
                    msg.metadata,
                    hasError,
                    msg.isGreeting
                )
            } else {
                item = new AssistantChatMessage(
                    timestamp,
                    answer,
                    new Date(timestamp),
                    {
                        turn_id: turnId,
                    },
                    hasError
                )
            }
            result.set(turnId, item);
        }
        assistantMessagesMap.current = result
        return result;
    }, [assistantAnswerEvents, thinkingItemEvents, finalAssistantAnswers, assistantErrors, currentSteps]);

    useEffect(() => {
        updatedTurns.current = []
    }, [assistantAnswerEvents, thinkingItemEvents, finalAssistantAnswers, assistantErrors, currentSteps]);


    const [followUpQuestion, setFollowUpQuestion] = useState<string[]>([]);

    const chatMessages = useMemo<ChatMessage[]>(() => {
        return [...userMessages.values(), ...assistantMessages.values()].sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime())
    }, [userMessages, assistantMessages]);

    const chatEventHandlers: ChatEventHandlers = useMemo(
        () => ({
            onConnect: () => {
            },
            onSessionInfo: (info) => {
                console.info("Server session:", info.session_id, info.user_type);
            },
            onDisconnect: (reason: string) => {
                console.warn("Disconnected:", reason);
                setIsProcessing(false);
            },
            onConnectError: (error: Error) => {
                console.error("Connect error:", error);
                setIsProcessing(false);

                const msg = (error?.message || "").toLowerCase();
                const looksAuthy =
                    msg.includes("401") ||
                    msg.includes("unauthorized") ||
                    msg.includes("forbidden") ||
                    msg.includes("rejected by server");

                if (looksAuthy) {
                    // Token is likely expired or invalid for this server — force re-auth.
                    try {
                        authContext.logout?.();
                    } catch (e) {
                        console.error("Failed to initiate re-auth:", e);
                    }
                }
            },

            onChatStart: (env: ChatStartEnvelope) => {
                console.debug("chat.start:", env);
                const turnId = env.conversation.turn_id;
                if (lastTurnID === turnId) {
                    console.warn("Turn already started", lastTurnID);
                    return;

                }
                setLastTurnID(lastTurnID);
            },

            onChatDelta: (env: ChatDeltaEnvelope) => {
                console.debug("chat.delta", env);
                const turnId = env.conversation.turn_id;
                const marker = env.delta?.marker ?? "answer";
                const ts = new Date(Date.parse(env.timestamp));
                const chunkText = env.delta.text;

                if (!env.event.agent) {
                    console.warn("Event has no agent", env)
                }

                const agent = env.event?.agent ?? "unknown_agent";
                const completed = !!env.delta.completed;

                if (marker === "thinking") {
                    setThinkingItemEvents(prevState => {
                        const newVal = new Map(prevState);
                        const events = [...newVal.get(turnId) ?? []]
                        events.push({
                            index: env.delta.index,
                            timestamp: ts,
                            completed: completed,
                            agent: agent,
                            text: chunkText
                        })
                        newVal.set(turnId, events.sort((a, b) => a.index - b.index))
                        return newVal;
                    })
                } else if (marker === "answer") {
                    setAssistantAnswerEvents(prevState => {
                        const newVal = new Map(prevState)
                        const events = [...newVal.get(turnId) ?? []]
                        events.push({
                            text: chunkText,
                            index: env.delta.index,
                            completed: completed,
                            timestamp: ts
                        })
                        newVal.set(turnId, events.sort((a, b) => a.index - b.index));
                        return newVal;
                    })
                }

                updatedTurns.current.push(turnId);
            },

            onChatStep: (env: ChatStepEnvelope) => {
                console.debug("chat.step", env)
                const turnId = env.conversation?.turn_id;

                setCurrentSteps((prev) => {
                    const stepId = env.event.step

                    const existing = prev.find(
                        (s) => s.step === stepId && s.turn_id === turnId
                    );

                    const stepUpdate: StepUpdate = {
                        step: env.event?.step,
                        status: env.event?.status as any,
                        title: env.event?.title,
                        timestamp: existing ? existing.timestamp : new Date(Date.parse(env.timestamp || new Date().toISOString())),
                        elapsed_time: (env as any).elapsed_time, // optional, not in v1 spec
                        error: env.data?.error,
                        data: env.data,
                        markdown: (env.event as any)?.markdown,
                        agent: (env.event as any)?.agent,
                        turn_id: turnId,
                    };

                    return existing
                        ? prev.map((s) =>
                            s.step === stepUpdate.step && s.turn_id === stepUpdate.turn_id ? stepUpdate : s
                        )
                        : [...prev, stepUpdate];
                });

                if (env.event?.step === "followups" && env.event?.status === "completed") {
                    setFollowUpQuestion(env.data?.items as [] || []);
                }

                updatedTurns.current.push(turnId);
            },

            onChatComplete: (env: ChatCompleteEnvelope) => {
                console.debug("chat.complete", env);
                const turnId = env.conversation.turn_id;
                const finalText = env.data?.final_answer ?? "";
                setFinalAssistantAnswers(prevState => {
                    const newVal = new Map(prevState)
                    newVal.set(turnId, finalText)
                    return newVal
                })
                setIsProcessing(false)
                updatedTurns.current.push(turnId);
            },

            onChatError: (env: ChatErrorEnvelope) => {
                console.debug("chat.error", env);
                const turnId = env.conversation.turn_id;
                const errText = env.data?.error ? String(env.data.error) : "Unknown error";
                setAssistantErrors(prevState => {
                    const newVal = new Map(prevState)
                    newVal.set(turnId, errText)
                    return newVal
                })
                setIsProcessing(false)
                updatedTurns.current.push(turnId)
            },
        }),
        []
    );

    const sendMessage = useCallback(
        async (message: string, attachments?: File[]): Promise<void> => {
            if ((!message.trim() && !attachments?.length) || isProcessing) return;

            const turnId = `turn_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

            const timestamp = new Date();
            const newMessage = new UserChatMessage(
                timestamp.getTime(),
                message.trim(),
                timestamp,
                {turn_id: turnId},
                attachments
            )
            setUserMessages(prevState => {
                const newVal = new Map(prevState)
                newVal.set(turnId, newMessage)
                return newVal
            })
            setIsProcessing(true);
            setFollowUpQuestion([]);

            const toWire = (msgs: ChatMessage[]): WireChatMessage[] => {
                return msgs.filter(m => m instanceof UserChatMessage || m instanceof AssistantChatMessage)
                    .map(m => {
                        return {
                            role: (m instanceof UserChatMessage ? "user" : "assistant"),
                            content: m.text,
                            timestamp: m.timestamp.toISOString(),
                            id: m.id,
                        }
                    })
            }

            try {
                const history = toWire(chatMessages);
                const payload: ChatRequest = {
                    message: newMessage.text,
                    chat_history: history,
                    project,
                    tenant,
                    turn_id: turnId,
                };
                sendSocketMessage(payload, attachments);
            } catch (error) {
                console.error("Error sending message via socket:", error);
                setAssistantErrors(prevState => {
                    const newVal = new Map(prevState)
                    newVal.set(turnId, `I was unable to send your message: ${(error as Error).message}`)
                    return newVal
                })
                setIsProcessing(false);
            }
        },
        [isProcessing, sendSocketMessage, project, tenant, chatMessages]
    );

    const hideKB = () => setShowKB(false);
    const toggleSystemMonitor = () => setShowSystemMonitor(prev => !prev);

    const chatLogItems = useMemo(() => {
        const items: ChatLogItem[] = [];
        const addItem = (item: ChatLogItem) => {
            items.push(item);
        }

        chatMessages.forEach(addItem)

        const steps = [...currentSteps]
        if (showExampleAssistantFileSteps()) {
            steps.push(...getExampleAssistantFileSteps())
        }
        if (showExampleAssistantSourceSteps()) {
            steps.push(...getExampleAssistantSourceSteps())
        }

        steps.forEach((s) => {
            addItem(createAssistantChatStep(s))
        })

        steps.forEach((s) => {
            if (s.step === "file" && s.status === "completed" && !!s.data?.rn && !!s.data?.filename) {

                addItem(createDownloadItem(s))
            } else if (s.step === "citations" && s.status === "completed" && !!s.data?.count && !!s.data?.items) {
                addItem(createSourceLinks(s))
            }
        })

        for (const item of assistantThinkingItems.values()) {
            addItem(item)
        }

        items.forEach((s) => {
            if (!s.getTurnId()) {
                console.warn("Item has no turnId", s)
            }
        })
        return items.sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime());
    }, [currentSteps, assistantThinkingItems, chatMessages])

        const renderFullHeader = () => {
        return (
            <div className="bg-white border-b border-gray-200 px-6 py-4">
                <div className="flex items-center justify-between">
                    <div className="flex items-center">
                        <div
                            className="w-10 h-10 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg mr-3 flex items-center justify-center">
                            {headerModel?.provider === 'anthropic' ? <Sparkles size={20} className="text-white"/> :
                                <Bot size={20} className="text-white"/>}
                        </div>
                        <div>
                            <h1 className="text-xl font-semibold text-gray-900">
                                {headerModel?.description || 'AI Assistant'}
                            </h1>
                            <p className="text-sm text-gray-500 flex items-center">
                                <Server size={14} className="mr-1"/>
                                {headerModel?.provider || 'Unknown'} • {headerModel?.has_classifier ? ' Domain Classification' : ' Direct Processing'}
                                <span className="flex items-center ml-1">
                    <Database size={12} className="mr-1"/>
                                    {headerEmbedder ? `${headerEmbedder.provider}${headerEmbedder.model ? ` (${headerEmbedder.model})` : ''}` : 'Embeddings'}
                  </span>
                                {headerBundle && (
                                    <span className="flex items-center ml-1">
                      • <Server size={12} className="mx-1"/> Bundle: {headerBundle.name || headerBundle.id}
                    </span>
                                )}
                                {config.kb_search_endpoint && (
                                    <span className="flex items-center ml-1"> • <BookOpen size={12}
                                                                                          className="mr-1"/> KB Search</span>
                                )}
                                <span className="flex items-center ml-2"> • {connectionStatus.icon}<span
                                    className="ml-1 text-xs">Streaming</span></span>
                            </p>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        {/* Connection status pill */}
                        <div className={`flex items-center px-3 py-1 rounded-lg text-sm ${connectionStatus.color}`}>
                            {connectionStatus.icon}
                            <span className="ml-2 font-medium">{connectionStatus.text}</span>
                            {socketId &&
                                <span className="ml-2 text-xs opacity-75">({socketId.slice(0, 8)}...)</span>}
                        </div>

                        <button
                            onClick={() => setShowKB(!showKB)}
                            className="relative flex items-center px-3 py-2 rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200"
                            title="View KB"
                        >
                            <Database size={16} className="mr-1"/><span className="text-sm">KB</span>
                        </button>

                        <button
                            onClick={handleShowKbResults}
                            className={`relative flex items-center px-3 py-2 rounded-lg transition-colors ${
                                kbSearchHistory.length > 0 ? 'bg-blue-100 text-blue-700 hover:bg-blue-200' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                            }`}
                            title="View KB Search Results"
                        >
                            <Search size={16} className="mr-1"/>
                            <span className="text-sm">KB Search</span>
                            {kbSearchHistory.length > 0 && (
                                <span
                                    className="ml-1 text-xs bg-blue-200 text-blue-800 px-1 rounded">{kbSearchHistory.length}</span>
                            )}
                            {newKbSearchCount > 0 && (
                                <span
                                    className="absolute -top-1 -right-1 w-2 h-2 bg-red-500 rounded-full animate-pulse"/>
                            )}
                        </button>

                        <button
                            onClick={() => handleShowConfigChange(!showConfig)}
                            className="flex items-center px-3 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg"
                        >
                            <Settings size={16} className="mr-1"/><span className="text-sm">Config</span>
                        </button>

                        <button
                            onClick={toggleSystemMonitor}
                            className={`relative flex items-center px-3 py-2 rounded-lg transition-colors ${
                                showSystemMonitor ? 'bg-green-100 text-green-700 hover:bg-green-200' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                            }`}
                            title={showSystemMonitor ? "Hide Monitor" : "Show Monitor"}
                        >
                            <Server size={16} className="mr-1"/>
                            <span className="text-sm">Monitor</span>
                            <div className="ml-2 w-2 h-2 bg-green-400 rounded-full animate-pulse"/>
                            {showSystemMonitor && <div className="ml-1 w-1 h-1 bg-green-600 rounded-full"/>}
                        </button>

                        <button
                            onClick={handleLogout}
                            className="flex items-center px-3 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg"
                            title="Sign out"
                        >
                            <LogOut size={16} className="mr-1"/><span className="text-sm">Logout</span>
                        </button>
                    </div>
                </div>
            </div>
        )
    }

    const onDownloadItemClick = (item: DownloadItem) => {
        const fn = async () => {
            const resource = await getResourceByRN(item.rn, authContext)
            const download_url = resource.metadata.download_url
            const data = await downloadBlob(download_url, authContext)
            handleContentDownload(item.filename, data, item.mimeType || "application/octet-stream")
        }
        fn()
    }

    const onLinkItemClick = (item: RichLink) => {
        openUrlSafely(item.url)
    }

    const chatContainerRef = useRef<HTMLDivElement>(null)
    const [fullChatWidth, setFullChatWidth] = useState<number>(0)


    useEffect(() => {
        function handleResize() {
            if (!chatContainerRef.current)
                return;
            const width = chatContainerRef.current.clientWidth;
            setFullChatWidth(width)
        }

        window.addEventListener('resize', handleResize);
        handleResize();

        return () => window.removeEventListener('resize', handleResize);
    }, []);



    const chatContextValue: ChatInterfaceContextValue = {
        chatLogItems: chatLogItems,
        onSendMessage: sendMessage,
        onDownloadItemClick: onDownloadItemClick,
        userInputEnabled: isSocketConnected,
        isProcessing: isProcessing,
        followUpQuestion: followUpQuestion
    }

    return (
        <div id={SingleChatApp.name} className="flex h-screen bg-slate-100">
            {/* Config Panel (widget) */}
            {showConfig && !!authContext.getUserProfile()?.roles?.includes('kdcube:role:super-admin') && (
                <ChatConfigPanel
                    visible={showConfig}
                    onClose={() => handleShowConfigChange(false)}
                    authContext={authContext}
                    config={config}
                    setConfigValue={setConfigValue}
                    className="w-[520px]"
                    updateConfig={updateConfig}
                    validationErrors={validationErrors}
                    onMetaChange={({model, embedder, bundle}) => {
                        setHeaderModel(model);
                        setHeaderEmbedder(embedder);
                        setHeaderBundle(bundle);
                    }}
                />
            )}

            {/* Main Column */}
            <div className="flex-1 flex flex-col">
                {/* Header */}
                {/*{renderSimpleHeader()}*/}
                {renderFullHeader()}

                {/* Body: Chat + optionally Steps / KB Results / System Monitor */}
                <div className={`flex-1 flex overflow-hidden transition-all duration-300`}>
                    {/* Chat Column */}
                    <div className={`flex-1 flex flex-col ${showSystemMonitor ? 'mr-4' : ''}`} ref={chatContainerRef}>
                        {/* Quick Questions */}
                        <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
                            {updatingQustions ?
                                (<div className="w-full flex">
                                    <Loader size={28} className='animate-spin text-gray-300 mx-auto'/>
                                </div>) :
                                (<>
                                    <h4 className="text-sm font-medium text-gray-700 mb-2">Try these questions:</h4>
                                    <div className="flex flex-wrap gap-2">
                                        {quickQuestions.map((q, idx) => (
                                            <button key={idx} onClick={() => sendMessage(q)}
                                                    disabled={isProcessing || !isSocketConnected}
                                                    className="px-3 py-1 text-xs bg-white text-gray-700 border border-gray-200 rounded-full hover:bg-gray-50 hover:border-gray-300 disabled:opacity-50">
                                                {q}
                                            </button>
                                        ))}
                                    </div>
                                </>)
                            }
                        </div>

                        <ChatInterfaceContext value={chatContextValue}>
                            <ChatInterface maxWidth={fullChatWidth * (3 / 5)}/>
                        </ChatInterfaceContext>
                    </div>

                    {/* KB Search Results Panel */}
                    {showKbResults && (
                        <div className="border-l border-gray-200 bg-white relative" style={{width: `700px`}}>
                            {/* simple draggable bar */}
                            <div
                                className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-300 group">
                                <div
                                    className="absolute left-0 top-1/2 transform -translate-y-1/2 -translate-x-1 opacity-0 group-hover:opacity-100">
                                    <GripVertical size={16} className="text-gray-400"/>
                                </div>
                            </div>
                            {kbSearchHistory.length > 0 ? (
                                <UpdatedSearchResultsHistory
                                    searchHistory={kbSearchHistory}
                                    onClose={handleCloseKbResults}
                                    kbEndpoint={config.kb_search_endpoint || `${getKBAPIBaseAddress()}/api/kb`}
                                />
                            ) : (
                                <div className="h-full flex flex-col">
                                    <div
                                        className="px-4 py-3 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
                                        <h3 className="font-semibold text-gray-900 text-sm">KB Search Results</h3>
                                        <button onClick={handleCloseKbResults}
                                                className="p-1 hover:bg-gray-200 rounded text-gray-500 hover:text-gray-700">
                                            <X size={14}/>
                                        </button>
                                    </div>
                                    <div className="flex-1 flex items-center justify-center text-gray-500">
                                        <div className="text-center">
                                            <Database size={24} className="mx-auto mb-2 opacity-50"/>
                                            <p>No KB search results yet</p>
                                            <p className="text-xs mt-1">Results will appear here when RAG retrieval
                                                occurs</p>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* KB Side Panel */}
            {showKB && (
                <div className="fixed inset-0 z-50 flex">
                    <div className="absolute inset-0 bg-transparent backdrop-blur-xs" onClick={hideKB}/>
                    <div className="ml-auto transition-transform h-full w-1/2">
                        <KBPanel onClose={hideKB}/>
                    </div>
                </div>
            )}

            {/* System Monitor Panel (widget) */}
            {showSystemMonitor && (
                <div className="border-l border-gray-200 bg-white relative flex-shrink-0" style={{width: `360px`}}>
                    <SystemMonitorPanel onClose={toggleSystemMonitor}/>
                </div>
            )}
        </div>
    );
};

export default SingleChatApp;
