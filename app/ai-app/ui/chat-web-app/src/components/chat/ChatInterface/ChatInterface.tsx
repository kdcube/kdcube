/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

// ChatInterface.tsx
import {
    AlertCircle,
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    Circle,
    CirclePlus,
    ClipboardCopy,
    Database,
    FileText, LinkIcon,
    List,
    Loader,
    MessageCircleMore,
    MessageSquare,
    Play,
    ScrollText,
    Search,
    Send,
    User,
    X,
    Zap,
} from "lucide-react";
import React, {
    createContext,
    CSSProperties, Fragment,
    ReactNode,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useRef,
    useState
} from "react";

import ReactMarkdown from "react-markdown";

import {
    AssistantChatMessage,
    AssistantChatStep,
    AssistantThinkingItem,
    ChatLogItem,
    ChatMessage,
    DownloadItem,
    SourceLinks,
    StepDerivedItem,
    UserChatMessage
} from "../types/chat.ts";
import {Hint} from "../../Hints.tsx";
import {copyMarkdownToClipboard} from "../../Clipboard.ts";

import {getFileIcon} from "../../FileIcons.tsx";
import {markdownComponents, markdownComponentsTight, rehypePlugins, remarkPlugins} from "./markdownRenderUtils.tsx";
import {selectFileAdvanced} from "../../shared.ts";
import {useWordStreamEffect} from "../../WordStreamingEffects.tsx";

interface ChatInterfaceProps {
    lockMessage?: string;
    inputPlaceholder?: string;
    showMetadata?: boolean;
    maxWidth?: number | string;
}

/* ---------- helpers (no hooks) ---------- */
const getStepName = (step: AssistantChatStep): string =>
    step.title || step.step.replace("_", " ").replace(/\b\w/g, (l) => l.toUpperCase());

const getStepIcon = (step: AssistantChatStep, iconSize = 14, className = "m-auto"): React.ReactNode => {
    switch (step.status) {
        case "started":
            return <Loader size={iconSize} className={`animate-spin ${className}`}/>;
        case "error":
            return <AlertCircle size={iconSize} className={className}/>;
    }
    switch (step.step) {
        case "classifier":
            return <Zap size={iconSize} className={className}/>;
        case "query_writer":
            return <FileText size={iconSize} className={className}/>;
        case "rag_retrieval":
            return <Database size={iconSize} className={className}/>;
        case "reranking":
            return <Search size={iconSize} className={className}/>;
        case "answer_generator":
            return <MessageSquare size={iconSize} className={className}/>;
        case "workflow_start":
            return <Play size={iconSize} className={className}/>;
        case "workflow_complete":
            return <CheckCircle2 size={iconSize} className={className}/>;
        default:
            return <Circle size={iconSize} className={className}/>;
    }
};

const getStepColor = (step: AssistantChatStep): string => {
    switch (step.status) {
        case "completed":
            return "text-green-800 ";
        case "started":
            return "text-blue-800";
        case "error":
            return "text-red-800";
        default:
            return "text-gray-800";
    }
};

const formatSeconds = (sec: number): string => {
    if (!isFinite(sec) || sec < 0) sec = 0;
    if (sec < 60) return `${Math.round(sec * 10) / 10}s`;
    const m = Math.floor(sec / 60);
    const s = Math.round((sec % 60) * 10) / 10;
    const sStr = (s < 10 ? "0" : "") + s.toFixed(1);
    return `${m}:${sStr}`;
};

const DownloadItemsPanel = ({items, onClick}: { items: DownloadItem[], onClick?: (item: DownloadItem) => void; }) => {
    if (!items || !items.length) return null;

    return (
        <div className="flex justify-start mt-2">
            <div className="w-full flex flex-row flex-wrap">
                {items.map((item, index) => (
                    <div key={index}>
                        <button
                            className="my-1 mr-2 text-gray-700 flex items-center text-sm cursor-pointer hover:text-black hover:underline"
                            onClick={() => onClick && onClick(item)}>
                            <span className="inline-block mr-1">{getFileIcon(item.filename, 24, item.mimeType)}</span>
                            <span className="inline-block">{item.filename}</span>
                        </button>
                    </div>
                ))}
            </div>
        </div>
    )
}

const ThinkingItem = ({item}: { item: AssistantThinkingItem }) => {
    const [expanded, setExpanded] = useState<boolean>(false)
    const onExpandThinkingItemClick = () => {
        setExpanded(!expanded)
    }

    const active = item.active;
    const endedAt = item.endedAt;
    const endMs = active ? Date.now() : endedAt?.getTime() ?? Date.now();
    const durSec = Math.max(0, Math.round(((endMs - item.timestamp.getTime()) / 1000) * 10) / 10);
    const agentKeys = Object.keys(item.agents || {})

    const getAgentSecondsIfCompleted = (agent: string): number | null => {
        const meta = item.agentTimes?.[agent];
        if (!meta?.endedAt || !meta?.startedAt) return null; // ← only show after completed
        const s = (meta.endedAt.getTime() - meta.startedAt.getTime()) / 1000;
        return Math.max(0, Math.round(s * 10) / 10);
    };

    return (
        <div>
            <button
                className={`flex items-center justify-between px-3 py-2 cursor-pointer select-none`}
                onClick={() => {
                    onExpandThinkingItemClick()
                }}
                title={expanded ? "Collapse" : "Expand"}
            >
                <div className="flex items-center gap-2">
                        <span className={`text-sm font-medium ${active ? "thinking-animated" : "text-gray-700"}`}>
                            {active ? "Thinking" : `Thought for ${formatSeconds(durSec)}`}
                        </span>
                </div>
                <span className="text-gray-500">{expanded ? <ChevronUp size={16}/> : <ChevronDown size={16}/>}</span>
            </button>
            {expanded && (
                <div className="px-3 py-2 text-xs text-slate-700">
                    {agentKeys.length === 0 ? (
                        <div className="text-gray-500 italic">No thoughts yet…</div>
                    ) : (
                        <div className="space-y-3">
                            {agentKeys.map((agent) => {
                                const secs = getAgentSecondsIfCompleted(agent);
                                return (
                                    <div key={agent}>
                                        <div
                                            className="px-2 py-1 font-medium uppercase tracking-wide text-gray-600 rounded-t-md flex items-center justify-between">
                                            <span>{agent || "agent"}</span>
                                            {/* Only show when completed */}
                                            {secs != null ? (
                                                <span className="text-gray-500">{formatSeconds(secs)}</span>
                                            ) : null}
                                        </div>
                                        <div>
                                            <ReactMarkdown
                                                remarkPlugins={remarkPlugins}
                                                rehypePlugins={rehypePlugins}
                                                components={markdownComponentsTight}
                                                linkTarget="_blank"
                                                skipHtml={false}
                                            >
                                                {item.agents[agent] || ""}
                                            </ReactMarkdown>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

const SunkenButton = (
    {children, onClick, pressed = false, disabled = false, className = ""}:
    { children: ReactNode, onClick?: () => unknown, pressed?: boolean, disabled?: boolean, className?: string }
) => {
    return (
        <button
            onClick={() => {
                onClick?.();
            }}
            disabled={disabled}
            className={`p-1 transition-all duration-150 border-1 border-gray-200 ${disabled ? "text-gray-300" : "hover:bg-slate-200 cursor-pointer"}  ${pressed ? '' : 'hover:bg-gray-50'} ${className}`}
            style={{
                boxShadow: pressed
                    ? 'inset 3px 3px 6px rgba(0,0,0,0.2), inset -3px -3px 6px rgba(255,255,255,0.8)'
                    : ''
            }}
        >
            {children}
        </button>
    );
};

const UserMessage = ({message}: { message: UserChatMessage }) => {
    return (
        <div key={message.id} className="flex justify-end">
            <div className="flex flex-row p-3 rounded-2xl bg-gray-200 text-black">
                <div className="flex flex-col">
                    {(message as UserChatMessage).attachments && (<div className="flex flex-row gap-1 flex-wrap">
                        {(message as UserChatMessage).attachments?.map((attachment: File) => (
                            <div
                                className="flex items-center border-2 px-2 py-1 rounded-xl border-gray-300 bg-gray-100">{getFileIcon(attachment.name, 18, undefined, "mr-1")}{attachment.name}</div>
                        ))}
                    </div>)}
                    {message.text &&
                        <p className="text-sm leading-relaxed whitespace-pre-wrap pt-1">{message.text}</p>}
                </div>
                <div
                    className="w-8 h-8 rounded-full bg-gray-300 ml-3 flex items-center justify-center flex-shrink-0">
                    <User size={16} className="text-gray-600"/>
                </div>
            </div>
        </div>
    )
}

type AssistantMessageTab = "message" | "sources" | "steps"

const AssistantMessage = (
    {message, items, onDownloadItemClick, processing = false, showMetadata = false}:
    {
        message?: AssistantChatMessage,
        items?: ChatLogItem[],
        onDownloadItemClick?: (item: DownloadItem) => void
        processing?: boolean,
        showMetadata?: boolean,
    }
) => {
    const mdRed = useRef<HTMLDivElement>(null);


    const streamedText = useWordStreamEffect(
        message?.text ?? "",
        50,
        2
    );

    const [tab, setTab] = useState<AssistantMessageTab>("message")
    const isPressed = (tabName: AssistantMessageTab) => tab === tabName

    const files = useMemo(() => {
        return items?.filter(item => item instanceof DownloadItem) || []
    }, [items])

    const sources = useMemo(() => {
        return items?.filter(item => item instanceof SourceLinks) || []
    }, [items])

    const steps = useMemo(() => {
        return items?.filter(item => item instanceof AssistantChatStep).sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime()) || []
    }, [items])

    const thinkingItems = useMemo(() => {
        return items?.filter(item => item instanceof AssistantThinkingItem).sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime()) || []
    }, [items])

    const renderMessage = useCallback(() => {
        if (!message) return null

        const copyClick = () => {
            if (message)
                copyMarkdownToClipboard(message.text, mdRed.current?.innerHTML).catch((err) => {
                    console.error("Could not copy message", err);
                });
        }

        return (<div className="pb-1">
            <DownloadItemsPanel items={files} onClick={onDownloadItemClick}/>
            <ReactMarkdown
                remarkPlugins={remarkPlugins}
                rehypePlugins={rehypePlugins}
                components={markdownComponents}
                linkTarget="_blank"
                skipHtml={false}
            >
                {message.isGreeting ? message.text : streamedText}
            </ReactMarkdown>
            <div
                className="flex flex-row space-x-2 w-full justify-start transition-all duration-300 ease-out"
            >
                <Hint content="Copied" trigger="click" autohideDelay={2000} className={"text-nowrap"}>
                    <Hint content="Copy to clipboard">
                        <button className="cursor-pointer" onClick={copyClick}>
                            <ClipboardCopy size={16} className="text-gray-400 hover:text-gray-600"/>
                        </button>
                    </Hint>
                </Hint>
            </div>

        </div>)
    }, [message, files, streamedText])

    const [expandedSteps, setExpandedSteps] = useState<Map<number, boolean>>(new Map())

    const onExpandStepClick = (i: number) => {
        setExpandedSteps((prevState) => {
            const state = new Map(prevState)
            if (state.has(i)) {
                state.set(i, !state.get(i));
            } else {
                state.set(i, true);
            }
            return state
        })
    }


    const renderSteps = useCallback(() => {
        return (
            <div className="flex flex-col my-2">
                {steps?.map((step, i, arr) => {
                        const markdown = step.getMarkdown()
                        const isExpanded = expandedSteps.has(i) ?
                            expandedSteps.get(i) : (i === arr.length - 1 && step.status !== 'completed') || step.status === 'error'
                        return (
                            <div key={i}>
                                <div className={`flex flex-row text-sm ${getStepColor(step)}`}>
                                    <div className="flex w-6 h-6">{getStepIcon(step)}</div>
                                    <span className="my-auto font-bold">{getStepName(step)}</span>
                                    {markdown && (
                                        <button className="flex w-4 h-6 cursor-pointer"
                                                onClick={() => onExpandStepClick(i)}>
                                            {isExpanded ? <ChevronUp size={16} className="m-auto"/> :
                                                <ChevronDown size={16} className="m-auto"/>}
                                        </button>
                                    )}
                                    <div/>
                                </div>
                                {isExpanded && markdown && (
                                    <div className="ml-5 transition-all duration-300 ease-out overflow-x-hidden">
                                        <ReactMarkdown
                                            remarkPlugins={remarkPlugins}
                                            rehypePlugins={rehypePlugins as any}
                                            components={markdownComponentsTight}
                                            linkTarget="_blank"
                                            skipHtml={false}
                                        >
                                            {markdown}
                                        </ReactMarkdown>
                                    </div>
                                )}
                            </div>
                        )
                    }
                )}
            </div>
        )
    }, [steps, expandedSteps])

    const renderSources = useCallback(() => {
        if (sources.length === 0) return null
        const renderSource = (source: SourceLinks, key: React.Key) => {
            return (
                <Fragment key={`source_${key}`}>
                    {source.links.map((link, i) => {
                        return (
                            <a key={`source_link_${key}_${i}`} href={link.url} target="_blank"
                               className="p-0.5 flex-1 rounded-sm border-1 border-gray-200 text-gray-800 cursor-pointer">
                                <div className="w-full p-1 flex flex-row items-center">
                                    <LinkIcon size={28} className="mx-2"/>
                                    <div className="flex-1 min-w-0 hover:underline">
                                        <h1 className="font-bold truncate max-w-[95%]">{link.title || link.url}</h1>
                                        <h2 className="truncate max-w-[95%]">{link.url}</h2>
                                    </div>
                                </div>
                            </a>
                        )
                    })}
                </Fragment>
            )
        }
        return (<div className="flex flex-col my-2 space-y-2">
            {sources.map(renderSource)}
        </div>)
    }, [sources])

    const renderItems = () => {
        switch (tab) {
            case "message":
                return renderMessage()
            case "steps":
                return renderSteps()
            case "sources":
                return renderSources()
            default:
                return "Unknown tab"
        }
    }

    const renderThinkingItems = useCallback(() => {
        if (thinkingItems.length === 0) {
            return null
        }

        return (
            <div
                className="flex flex-col rounded-lg mb-2 border border-gray-200 [&>div:not(:last-child)]:border-b [&>div:not(:last-child)]:border-gray-200">
                {thinkingItems.map((item, i) => {
                        return (
                            <ThinkingItem key={`thinking-item-${i}`} item={item}/>
                        )
                    }
                )}
            </div>
        )
    }, [thinkingItems])

    return (
        <div key={message?.id || "nomessage"} className="flex justify-start">

            <div className="flex flex-col w-full">
                <div
                    className={`px-3 pt-2 ${message?.isError ? "text-red-800" : "text-gray-800"} max-w-none`}
                    ref={mdRed}>
                    {!message?.isGreeting && (<div
                        className="flex flex-row w-full [&_button:first-child]:rounded-l-md [&_button:last-child]:rounded-r-md">
                        <SunkenButton pressed={isPressed("message")} onClick={() => setTab("message")}>
                            <div className="inline-flex items-center mr-1">{
                                processing ?
                                    <Loader size={14} className="animate-spin mr-2"/> :
                                    <MessageCircleMore size={18} className="mx-0.5"/>}
                                Message
                            </div>
                        </SunkenButton>
                        <SunkenButton pressed={isPressed("steps")} disabled={steps.length < 1}
                                      onClick={() => setTab("steps")}>
                            <div className="inline-flex items-center mr-1"><List size={18}
                                                                                 className="mx-0.5"/>Steps{steps.length > 0 ? ` (${steps.length})` : ""}
                            </div>
                        </SunkenButton>
                        <SunkenButton pressed={isPressed("sources")} disabled={sources.length < 1}
                                      onClick={() => setTab("sources")}>
                            <div className="inline-flex items-center mr-1"><ScrollText size={18}
                                                                                       className="mx-0.5"/>
                                Sources{sources.length > 0 ? ` (${sources.reduce((previousValue, currentValue) => {
                                    return previousValue + currentValue.links.length
                                }, 0)})` : ""}
                            </div>
                        </SunkenButton>
                    </div>)}
                    {renderItems()}
                    {renderThinkingItems()}
                </div>

                {showMetadata && message?.metadata && (
                    <div className="mt-2 text-xs text-gray-500 space-y-1">
                        {message.metadata.is_our_domain !== undefined && (
                            <div className="flex items-center">
                                <span className="mr-2">Classification:</span>
                                <span
                                    className={`px-2 py-1 rounded text-xs ${
                                        message.metadata.is_our_domain ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-700"
                                    }`}
                                >
                                        {message.metadata.is_our_domain ? "Our Domain" : "Not Our Domain"}
                                    </span>
                            </div>
                        )}
                        {message.metadata.retrieved_docs && message.metadata.retrieved_docs > 0 && (
                            <div className="flex items-center">
                                <Database size={12} className="mr-1"/>
                                <span>KB Search: {message.metadata.retrieved_docs} documents retrieved</span>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    )
}

export interface ChatInterfaceContextValue {
    chatLogItems?: ChatLogItem[];
    isLocked?: boolean;
    userInputEnabled?: boolean;
    isProcessing?: boolean;
    followUpQuestion?: string[];
    onSendMessage?: (message: string, files?: File[]) => Promise<void>;
    onDownloadItemClick?: (downloadItem: DownloadItem) => void;
}

export const ChatInterfaceContext = createContext<ChatInterfaceContextValue>({})

const ChatInterface = ({
                           inputPlaceholder = "Ask me anything...",
                           lockMessage,
                           showMetadata = false,
                           maxWidth,
                       }: ChatInterfaceProps) => {
    const [userInput, setUserInput] = useState<string>("");
    const [userInputFiles, setUserInputFiles] = useState<File[]>([]);
    const logContainerRef = useRef<HTMLDivElement | null>(null);
    const userInputFieldRef = useRef<HTMLTextAreaElement | null>(null);
    const contentRef = useRef<HTMLDivElement | null>(null);

    // auto-scroll when near bottom
    const autoScroll = useRef(true);

    const {
        chatLogItems,
        followUpQuestion,
        isProcessing,
        isLocked,
        userInputEnabled,
        onSendMessage,
        onDownloadItemClick
    } = useContext(ChatInterfaceContext);

    const addInputFiles = (files: File[]) => {
        setUserInputFiles((prevState) => {
            const newState = [...prevState]
            files.forEach((newFile) => {
                const i = newState.findIndex((file) => file.name === newFile.name)
                if (i >= 0) {
                    newState[i] = newFile;
                } else {
                    newState.push(newFile);
                }
            })
            return newState;
        })
    }

    const removeInputFiles = (files: File[]) => {
        setUserInputFiles((prevState) => {
            const newState = [...prevState]
            files.forEach((newFile) => {
                const i = newState.findIndex((file) => file.name === newFile.name)
                if (i >= 0) {
                    newState.splice(i, 1)
                }
            })
            return newState;
        })
    }

    useEffect(() => {
        if (autoScroll.current && logContainerRef.current) {
            logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
        }
    }, [chatLogItems, isProcessing]);

    const onScroll = () => {
        const el = logContainerRef.current;
        if (!el) return;
        const threshold = 60;
        const atBottom = el.scrollHeight - (el.scrollTop + el.clientHeight) < threshold;
        autoScroll.current = atBottom;
    };

    const sendMessage = (message?: string) => {
        if (isProcessing)
            return;
        message = message || userInput.trim();
        if (!message && userInputFiles.length < 1)
            return;
        onSendMessage?.(message, userInputFiles).then(() => {
            setUserInput("")
            setUserInputFiles([])
        });
    };

    const turnGroups = useMemo(() => {
        return chatLogItems?.reduce((acc, item) => {
            const turnId = item.getTurnId();

            if (turnId) {
                if (acc.has(turnId)) {
                    acc.get(turnId)?.push(item);
                } else {
                    acc.set(turnId, [item]);
                }
            } else {

                console.warn("Item has no turnId. Skipping", item);
            }
            return acc
        }, new Map<string, ChatLogItem[]>()) || new Map<string, ChatLogItem[]>()
    }, [chatLogItems])

    const turnOrder = useMemo(() => {
        return chatLogItems?.filter((item) => {
            return item instanceof ChatMessage
        }).sort((a, b) => {
            return a.timestamp.getTime() - b.timestamp.getTime()
        }).reduce((acc, item) => {
            const turnId = item.getTurnId();
            if (turnId && !acc.includes(turnId)) {
                acc.push(turnId);
            }
            return acc;
        }, [] as string[]) || []
    }, [chatLogItems]);

    const renderTurnGroup = (groupId: string) => {
        const items = turnGroups.get(groupId) || [];
        if (!items)
            return null;
        const messages = []
        const assistantMessages: AssistantChatMessage[] = []
        const assistantItems = []
        for (const item of items) {
            if (item instanceof ChatMessage) {
                messages.push(item);
                if (item instanceof AssistantChatMessage)
                    assistantMessages.push(item);
            } else {
                assistantItems.push(item);
            }
        }

        const children = []
        for (const message of messages) {
            if (message instanceof UserChatMessage) {
                children.push(<UserMessage key={message.id} message={message}/>)
            } else if (message instanceof AssistantChatMessage) {
                const msgIndex = assistantMessages.indexOf(message);
                const startTime = message.timestamp.getTime();
                const stopTime = msgIndex === assistantMessages.length - 1 ? Infinity : assistantMessages[msgIndex + 1].timestamp.getTime();
                const msgItems = assistantItems.filter((item) => {
                    const time = item.timestamp.getTime();
                    // console.debug(startTime, ">=", item.timestamp.getTime(), "<=", stopTime, time >= startTime && time <= stopTime, item);
                    return time >= startTime && time <= stopTime;
                })
                children.push(<AssistantMessage key={message.id} message={message} showMetadata={false}
                                                items={msgItems} onDownloadItemClick={onDownloadItemClick}/>)
            }
        }

        if (assistantMessages.length == 0) {
            children.push(<AssistantMessage key={"placeholder_assistant_message"} showMetadata={false}
                                            items={assistantItems}/>)
        }

        return (
            <div key={`group-${groupId}`}>
                {children}
            </div>
        )
    }

    const renderChatLogItems = () => {
        return (
            <>
                {turnOrder.map((groupId) => renderTurnGroup(groupId))}
            </>
        )
    }

    const renderFollowUpQuestions = () => {
        const disabled = !userInputEnabled || isProcessing;
        if (!isProcessing && followUpQuestion) {
            return (
                <div className="flex flex-row items-start w-full flex-wrap space-x-1 space-y-1 pl-3">
                    {followUpQuestion.map((q, i) => {
                        return (<button key={`follow-up-question-${i}`}
                                        className="px-3 py-1 text-xs bg-white text-gray-700 border border-gray-200 rounded-full hover:bg-gray-50 hover:border-gray-300 disabled:opacity-50"
                                        onClick={() => {
                                            sendMessage(q)
                                        }} disabled={disabled}>
                            {q}
                        </button>)
                    })}
                </div>
            )
        }
        return null
    }

    const renderChatLog = () => {
        const elementStyle: CSSProperties = {}
        if (maxWidth)
            elementStyle.width = typeof maxWidth === 'number' ? `${maxWidth}px` : maxWidth;
        return (
            <div
                className="h-full w-full"
                id="ChatLog"
            >
                <div
                    className="h-full w-full overflow-x-auto"
                    ref={logContainerRef}
                    onScroll={onScroll}
                >
                    <div className="border-r border-l border-gray-200 mx-auto min-h-full bg-slate-50"
                         style={elementStyle}>
                        <div className="px-10 py-4">
                            {renderChatLogItems()}
                            {renderProcessing()}
                            {renderFollowUpQuestions()}
                        </div>
                        <div className="pb-22"/>
                    </div>
                </div>

            </div>
        )
    };

    const renderProcessing = () => {
        return isProcessing ? (
            <div className="flex items-center text-gray-500 mt-2 ml-4">
                <Loader size={16} className="animate-spin mr-2"/>
                <span>Working…</span>
            </div>
        ) : null
    }

    const renderUserInput = () => {
        const inputDisabled = !userInputEnabled || isLocked;
        // const inputDisabled = false;
        const sendButtonDisabled = inputDisabled || isProcessing || (!userInput.trim() && userInputFiles.length == 0);
        const elementStyle: CSSProperties = {}
        if (maxWidth)
            elementStyle.width = typeof maxWidth === 'number' ? `${maxWidth}px` : maxWidth;

        return (
            <div
                id="UserInput"
                className="absolute -left-2 z-30 bottom-0 w-full"
                onClick={() => userInputFieldRef.current?.focus()}
            >
                <div className="pointer-events-none mx-auto px-8" style={elementStyle}>
                    <div
                        className={`flex flex-col mx-auto border rounded-t-xl border-gray-400 shadow-sm pointer-events-auto ${isLocked ? "bg-yellow-50" : "bg-white"}`}
                    >
                        {userInputFiles && userInputFiles.length > 0 &&
                            <div className="flex flex-row flex-wrap p-3 gap-1">
                                {
                                    userInputFiles.map((file, i) => {
                                        return (<div
                                            className="flex border-2 border-gray-400 bg-gray-50 rounded-2xl px-3 py-1 items-center"
                                            key={`input-file-${i}`}
                                        >
                                            <span>{file.name}</span>
                                            <button
                                                className="pl-1 text-gray-400 hover:text-gray-600 cursor-pointer"
                                                onClick={() => {
                                                    removeInputFiles([file])
                                                }}
                                            >
                                                <X size={12}/>
                                            </button>
                                        </div>)
                                    })
                                }
                            </div>}
                        <div className="flex max-h-72 min-h-12 w-full">
                            {isLocked ? (
                                <div className="flex-1 m-3 flex flex-col items-center">
                                    <span
                                        className="font-semibold text-gray-400">{lockMessage || "Daily token limit reached. Please try again later."} </span>
                                </div>
                            ) : (
                                <textarea
                                    value={userInput}
                                    onChange={(e) => setUserInput(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (userInputEnabled && e.key === "Enter" && !e.shiftKey) {
                                            e.preventDefault();
                                            sendMessage();
                                        }
                                    }}
                                    placeholder={inputPlaceholder}
                                    disabled={inputDisabled}
                                    className="flex-1 m-3 resize-none grow field-sizing-content focus:outline-none overflow-y-auto"
                                    rows={2}
                                    ref={userInputFieldRef}
                                />
                            )}

                        </div>
                        <div className="flex">
                            <div className="pl-2"/>
                            <button
                                onClick={() => {
                                    selectFileAdvanced({multiple: true}).then((res) => {
                                        addInputFiles(res)
                                    })
                                }}
                                disabled={inputDisabled}
                                className=" mb-3 rounded-lg font-medium text-gray-600 hover:text-gray-900 disabled:text-gray-300"
                                aria-label="Add file"
                                title="Add file"
                            >
                                <CirclePlus size={18} className={`${inputDisabled ?
                                    (isProcessing ? "cursor-wait" : "cursor-auto") :
                                    "cursor-pointer"}`}/>
                            </button>
                            <button
                                onClick={() => {
                                    sendMessage()
                                }}
                                disabled={sendButtonDisabled}
                                className="mb-3 mr-3 rounded-lg font-medium text-gray-600 hover:text-gray-900 disabled:text-gray-300 ml-auto"
                                aria-label="Send message"
                                title="Send"
                            >
                                <Send size={18} className={`${sendButtonDisabled ?
                                    (userInput.trim() || userInputFiles.length > 0 ? "cursor-wait" : "cursor-auto") :
                                    "cursor-pointer"}`}/>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        );
    };

    return (
        <div id={ChatInterface.name}
             ref={contentRef}
             className="flex-1 flex flex-col bg-slate-100 min-h-0 min-w-0 transition-all duration-100 ease-out w-full relative"
        >
            {renderChatLog()}
            {renderUserInput()}
        </div>
    );
};

export default ChatInterface;
