import {RNFile, TurnStep} from "../../../../features/chatController/chatBase.ts";
import {useAppSelector} from "../../../../app/store.ts";
import {selectCurrentTurn} from "../../../../features/chat/chatStateSlice.ts";
import React, {ReactNode, useMemo, useRef, useState} from "react";
import {closeUpMarkdown, useWordStreamEffect} from "../../../WordStreamingEffects.tsx";
import ReactMarkdown from "react-markdown";
import {markdownComponents, markdownComponentsTight, rehypePlugins, remarkPlugins} from "../markdownRenderUtils.tsx";
import {copyMarkdownToClipboard} from "../../../Clipboard.ts";
import {Hint} from "../../../Hints.tsx";
import {
    AlertCircle,
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    Circle,
    ClipboardCopy,
    Database,
    FileText,
    LinkIcon,
    List,
    Loader,
    MessageCircleMore,
    MessageSquare,
    Play,
    ScrollText,
    Search,
    Zap
} from "lucide-react";
import {
    CitationArtifact,
    FileArtifact,
    ThinkingArtifact,
    UnknownArtifact
} from "../../../../features/chat/chatTypes.ts";
import {downloadResourceByRN} from "../../../../app/api/utils.ts";
import {getFileIcon} from "../../../FileIcons.tsx";
import {sortTimestamped} from "../../../../utils/utils.ts";
import {Thinking} from "./Thinking.tsx";
import {getChatLogComponent} from "../../../../features/extensions/logExtesnions.ts";

const getStepName = (step: TurnStep): string =>
    step.title || step.step.replace("_", " ").replace(/\b\w/g, (l) => l.toUpperCase());
const getStepIcon = (step: TurnStep, iconSize = 14, className = "m-auto"): React.ReactNode => {
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
const getStepColor = (step: TurnStep): string => {
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

interface DownloadItemsPanelProps {
    items: RNFile[] | null | undefined,
}

const DownloadItemsPanel = ({items}: DownloadItemsPanelProps) => {
    if (!items || !items.length) return null;

    return (
        <div className="flex justify-start mt-2">
            <div className="w-full flex flex-row flex-wrap">
                {items.map((item, index) => (
                    <div key={index}>
                        <button
                            className="my-1 mr-2 text-gray-700 flex items-center text-sm cursor-pointer hover:text-black hover:underline"
                            onClick={() => downloadResourceByRN(item.rn, item.filename, item.mime)}>
                            <span className="inline-block mr-1">{getFileIcon(item.filename, 24, item.mime)}</span>
                            <span className="inline-block">{item.filename}</span>
                        </button>
                    </div>
                ))}
            </div>
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
type AssistantMessageTab = "message" | "sources" | "steps"

interface AssistantMessageProps {
    message?: string | null;
    steps: TurnStep[];
    artifacts: UnknownArtifact[];
    isGreeting: boolean;
    isError: boolean;
    isHistorical: boolean | undefined | null;
}

export const AssistantMessageComponent = ({
                                              message,
                                              steps,
                                              artifacts,
                                              isHistorical,
                                              isGreeting = false,
                                          }: AssistantMessageProps) => {
    const currentTurn = useAppSelector(selectCurrentTurn)
    const inProgress = useMemo(() => !!currentTurn, [currentTurn])

    const mdRed = useRef<HTMLDivElement>(null);

    const streamedText = useWordStreamEffect(
        message ?? "",
        !isHistorical && !isGreeting,
        50
    );

    const [tab, setTab] = useState<AssistantMessageTab>("message")
    const isPressed = (tabName: AssistantMessageTab) => tab === tabName

    const {thinkingItems, citations, files, other} = useMemo(() => {
        const thinkingItems: ThinkingArtifact[] = []
        const citations: CitationArtifact[] = []
        const files: FileArtifact[] = []
        const other: UnknownArtifact[] = []

        artifacts.forEach((artifact) => {
            switch (artifact.artifactType) {
                case "thinking":
                    thinkingItems.push(artifact as ThinkingArtifact);
                    break;
                case "citation":
                    citations.push(artifact as CitationArtifact);
                    break;
                case "file":
                    files.push(artifact as FileArtifact);
                    break;
                default:
                    other.push(artifact);
            }
        })
        return {thinkingItems, citations, files, other};
    }, [artifacts])


    const markdownMemo = useMemo(() => {
        return (
            <ReactMarkdown
                remarkPlugins={remarkPlugins}
                rehypePlugins={rehypePlugins}
                components={markdownComponents}
                skipHtml={false}
            >
                {closeUpMarkdown(streamedText)}
            </ReactMarkdown>
        )
    }, [streamedText])

    const filesMemo = useMemo(() => {
        return (<DownloadItemsPanel items={files.map(it => it.content)}/>)
    }, [files])

    const messageMemo = useMemo(() => {

        const copyClick = () => {
            if (message)
                copyMarkdownToClipboard(message, mdRed.current?.innerHTML).catch((err) => {
                    console.error("Could not copy message", err);
                });
        }

        return (<div className="pb-1">
            {filesMemo}
            {markdownMemo}
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
    }, [filesMemo, markdownMemo, message])

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


    const stepsMemo = useMemo(() => {
        return (
            <div className="flex flex-col my-2">
                {steps?.map((step, i, arr) => {
                        const markdown = step.markdown
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
                                            rehypePlugins={rehypePlugins}
                                            components={markdownComponentsTight}
                                            skipHtml={false}
                                        >
                                            {closeUpMarkdown(markdown)}
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

    const sourcesMemo = useMemo(() => {
        if (citations.length === 0) return null
        return (<div className="flex flex-col my-2 space-y-2">
            {citations.map((link) => {
                return (
                    <a key={`source_link__${link.content.url}`} href={link.content.url} target="_blank"
                       className="p-0.5 flex-1 rounded-sm border-1 border-gray-200 text-gray-800 cursor-pointer">
                        <div className="w-full p-1 flex flex-row items-center">
                            <LinkIcon size={28} className="mx-2"/>
                            <div className="flex-1 min-w-0 hover:underline">
                                <h1 className="font-bold truncate max-w-[95%]">{link.content.title || link.content.url}</h1>
                                <h2 className="truncate max-w-[95%]">{link.content.url}</h2>
                            </div>
                        </div>
                    </a>
                )
            })}
        </div>)
    }, [citations])

    const thinkingItemsMemo = useMemo(() => {
        if (!thinkingItems || thinkingItems.length === 0) {
            return null
        }
        return (
            <>
                {thinkingItems.map((item, i) => {
                    return <Thinking item={item} key={i}/>
                })}
            </>
        )
    }, [thinkingItems])

    const timelineMemo = useMemo(() => {
        const items = other.concat()
        sortTimestamped(items)
        return <div className={"w-full min-w-0 flex flex-col"}>
            {items.map((item, i) => {
                const Component = getChatLogComponent(item.artifactType)
                if (!Component) {
                    return null
                }
                return <Component key={i} item={item} historical={isHistorical}/>
            })}
        </div>
    }, [isHistorical, other])

    return useMemo(() => {
        return (
            <div className="flex justify-start">

                <div className="flex flex-col w-full">
                    <div
                        className={"px-3 pt-2 text-gray-800 max-w-none"}
                        ref={mdRed}>
                        {!isGreeting && (<div
                            className="flex flex-row w-full [&_button:first-child]:rounded-l-md [&_button:last-child]:rounded-r-md">
                            <SunkenButton pressed={isPressed("message")} onClick={() => setTab("message")}>
                                <div className="inline-flex items-center mr-1">{
                                    inProgress ?
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
                            <SunkenButton pressed={isPressed("sources")} disabled={citations.length < 1}
                                          onClick={() => setTab("sources")}>
                                <div className="inline-flex items-center mr-1"><ScrollText size={18}
                                                                                           className="mx-0.5"/>
                                    Sources{citations.length > 0 ? ` (${citations.length})` : ""}
                                </div>
                            </SunkenButton>
                        </div>)}
                        {isPressed("message") && <div className={"mt-1.5 w-full"}>
                            {thinkingItemsMemo}
                            {timelineMemo}
                            {messageMemo}
                        </div>}
                        {isPressed("steps") && stepsMemo}
                        {isPressed("sources") && sourcesMemo}
                    </div>
                </div>
            </div>
        )
    }, [citations.length, inProgress, isGreeting, isPressed, messageMemo, sourcesMemo, steps.length, stepsMemo, thinkingItemsMemo, timelineMemo])
}