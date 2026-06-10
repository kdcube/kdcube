import {Fragment, ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState} from "react";
import {connectChat, sendChatMessage} from "../../../features/chat/chatServiceMiddleware.ts";
import {ChevronsDown, Loader} from "lucide-react";
import {UserMessageComponent} from "./UserMessageComponent.tsx";
import {AssistantMessageComponent} from "./assistantMessage/AssistantMessageComponent.tsx";
import {useAppDispatch, useAppSelector} from "../../../app/store.ts";
import {
    selectChatStayConnected,
    selectCurrentTurn,
    selectTurnOrder,
    selectTurns
} from "../../../features/chat/chatStateSlice.ts";
import {
    ConversationStatusArtifact,
    ConversationStatusArtifactType
} from "../../../features/logExtensions/conversationStatus/types.ts";
import {motion} from "motion/react";
import IconContainer from "../../IconContainer.tsx";
import {AssistantMessage, UserMessage, UnknownArtifact} from "../../../features/chat/chatTypes.ts";
import {getChatLogComponent} from "../../../features/extensions/logExtesnions.ts";

type OrderedTurnItem =
    | { kind: "user"; item: UserMessage; timestamp: number; index: number }
    | { kind: "assistant"; item: AssistantMessage; artifacts: UnknownArtifact[]; timestamp: number; index: number }
    | { kind: "activity"; timestamp: number; index: number }
    | { kind: "artifact"; item: UnknownArtifact; timestamp: number; index: number };

const isRenderableArtifact = (artifact: UnknownArtifact) => {
    if (artifact.artifactType === ConversationStatusArtifactType) return false;
    return !!getChatLogComponent(artifact.artifactType);
}

const activityArtifacts = (artifacts: UnknownArtifact[]) => {
    return artifacts.filter((artifact) => {
        switch (artifact.artifactType) {
            case "thinking":
                return true;
            default:
                return false;
        }
    });
}

const assistantArtifacts = (artifacts: UnknownArtifact[]) => {
    return artifacts.filter((artifact) => {
        switch (artifact.artifactType) {
            case "citation":
            case "file":
                return true;
            default:
                return false;
        }
    });
}

const getActivityTimestamp = (
    artifacts: UnknownArtifact[],
    steps: { timestamp: number }[],
    fallback: number,
) => {
    const artifactTimestamps = artifacts
        .map((artifact) => artifact.timestamp)
        .filter(Number.isFinite);

    const stepTimestamps = steps
        .map((step) => step.timestamp)
        .filter(Number.isFinite);

    const timestamps = [...artifactTimestamps, ...stepTimestamps];
    if (timestamps.length > 0) {
        return Math.min(...timestamps);
    }

    return fallback;
}

const ChatLog = () => {
    const dispatch = useAppDispatch();

    const turns = useAppSelector(selectTurns)
    const turnOrder = useAppSelector(selectTurnOrder)
    const currentTurn = useAppSelector(selectCurrentTurn)
    const stayConnected = useAppSelector(selectChatStayConnected)

    useEffect(() => {
        if (!stayConnected)
            dispatch(connectChat())
    }, [dispatch, stayConnected]);

    const inProgress = useMemo(() => !!currentTurn, [currentTurn])
    const followUpQuestions = useMemo(() => {
        return turnOrder.length > 0 ? turns[turnOrder[turnOrder.length - 1]].followUpQuestions : null
    }, [turnOrder, turns])

    const logContainerRef = useRef<HTMLDivElement | null>(null);
    const logContentRef = useRef<HTMLDivElement | null>(null);

    const autoScroll = useRef(true);
    const [inputClearance, setInputClearance] = useState(88);

    const scrollToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
        const container = logContainerRef.current;
        if (!container) return;

        container.scrollTo({
            top: Math.max(0, container.scrollHeight - container.clientHeight),
            behavior,
        });
    }, []);

    useLayoutEffect(() => {
        if (autoScroll.current) {
            scrollToBottom();
        }
    }, [turns, inProgress, inputClearance, scrollToBottom]);

    useEffect(() => {
        const input = document.getElementById("UserInput");
        if (!input || typeof ResizeObserver === "undefined") return;

        const updateClearance = () => {
            setInputClearance(Math.ceil(input.getBoundingClientRect().height));
        };
        updateClearance();
        const observer = new ResizeObserver(updateClearance);
        observer.observe(input);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        const content = logContentRef.current;
        if (!content || typeof ResizeObserver === "undefined") return;

        const observer = new ResizeObserver(() => {
            if (autoScroll.current) {
                requestAnimationFrame(() => scrollToBottom());
            }
        });
        observer.observe(content);
        return () => observer.disconnect();
    }, [scrollToBottom]);

    const [showScrollDown, setShowScrollDown] = useState<boolean>(false)

    const onScroll = useCallback(() => {
        const el = logContainerRef.current;
        if (!el) return;
        const threshold = 60;
        autoScroll.current = el.scrollHeight - (el.scrollTop + el.clientHeight) < threshold;
    }, [])

    useEffect(() => {
        if (logContainerRef.current) {
            const logContainer = logContainerRef.current;
            if (!logContainer) return;
            const onScroll = () => {
                setShowScrollDown(logContainer.scrollHeight - logContainer.clientHeight - logContainer.scrollTop > 400);
            }
            logContainer.addEventListener("scroll", onScroll);
            return () => {
                logContainer.removeEventListener("scroll", onScroll);
            }
        }
    });

    const sendMessage = useCallback((message?: string) => {
        dispatch(sendChatMessage({
            message
        }))
    }, [dispatch]);

    const scrollDownButton = useMemo(() => {
        return <motion.button
            className={"block cursor-pointer z-50"}
            initial={{
                opacity: showScrollDown ? 0 : 1,
            }}
            animate={{
                opacity: showScrollDown ? 1 : 0,
            }}
            onClick={() => {
                autoScroll.current = true;
                scrollToBottom("smooth");
            }}
        >
            <IconContainer icon={ChevronsDown} size={2}
                           className={"text-gray-600 hover:text-gray-800 transition-colors duration-200"}/>
        </motion.button>
    }, [scrollToBottom, showScrollDown])

    const followUpQuestionsMemo = useMemo(() => {
        const disabled = inProgress;
        if (!inProgress && followUpQuestions && followUpQuestions.length > 0) {
            return (
                <div className="flex flex-row items-start w-full flex-wrap space-x-1 space-y-1 pl-3">
                    {followUpQuestions.map((q, i) => {
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
    }, [inProgress, followUpQuestions, sendMessage]);

    const conversationStatus = useMemo(() => {
        if (currentTurn) {
            const statusArtifact = currentTurn.artifacts.find(artifact => artifact.artifactType === ConversationStatusArtifactType)
            if (statusArtifact) {
                const status = (statusArtifact as ConversationStatusArtifact).content.status
                return (status.at(0)?.toUpperCase() + status.slice(1)).replace(/\.*$/g, "") + "..."
            }
        }
        return null
    }, [currentTurn])

    const processingRender = useMemo(() => {
        return inProgress ? (
            <div className="flex items-center text-gray-500 mt-2 ml-4">
                <Loader size={16} className="animate-spin mr-2"/>
                <span>{conversationStatus ? conversationStatus : "Working…"}</span>
            </div>
        ) : null
    }, [conversationStatus, inProgress])

    const turnsRender = useMemo(() => {
        return (<>
            {turnOrder.map(turnId => {
                const turn = turns[turnId];
                const orderedItems: OrderedTurnItem[] = [];
                let index = 0;
                const sharedActivityArtifacts = activityArtifacts(turn.artifacts);
                const assistantScopedArtifacts = assistantArtifacts(turn.artifacts);
                const renderableArtifacts = turn.artifacts.filter(isRenderableArtifact);
                const turnSteps = Object.values(turn.steps);
                const assistantMessages = (turn.assistantMessages && turn.assistantMessages.length > 0)
                    ? turn.assistantMessages
                    : (turn.answer ? [{text: turn.answer, timestamp: turn.events.find((event) => event.eventType === "answer")?.timestamp ?? turn.userMessage.timestamp + 1}] : []);
                const assistantArtifactBuckets: UnknownArtifact[][] = assistantMessages.map(() => []);
                const activityCarrierArtifacts = sharedActivityArtifacts.slice();
                if (assistantMessages.length > 0) {
                    assistantScopedArtifacts.forEach((artifact) => {
                        let targetIndex = -1;
                        assistantMessages.forEach((message, messageIndex) => {
                            if (message.timestamp <= artifact.timestamp) {
                                targetIndex = messageIndex;
                            }
                        });
                        if (targetIndex >= 0) {
                            assistantArtifactBuckets[targetIndex].push(artifact);
                        } else {
                            activityCarrierArtifacts.push(artifact);
                        }
                    });
                } else {
                    activityCarrierArtifacts.push(...assistantScopedArtifacts);
                }

                if (turn.userMessage.text || turn.userMessage.attachments?.length) {
                    orderedItems.push({
                        kind: "user",
                        item: turn.userMessage,
                        timestamp: turn.userMessage.timestamp,
                        index: index++,
                    });
                }

                (turn.additionalUserMessages ?? []).forEach((message) => {
                    orderedItems.push({
                        kind: "user",
                        item: message,
                        timestamp: message.timestamp,
                        index: index++,
                    });
                });

                if (activityCarrierArtifacts.length > 0 || turnSteps.length > 0 || turn.state === "inProgress") {
                    orderedItems.push({
                        kind: "activity",
                        timestamp: getActivityTimestamp(
                            activityCarrierArtifacts,
                            turnSteps,
                            turn.userMessage.timestamp + 1,
                        ),
                        index: index++,
                    });
                }

                assistantMessages.forEach((message, assistantIndex) => {
                    orderedItems.push({
                        kind: "assistant",
                        item: message,
                        artifacts: assistantArtifactBuckets[assistantIndex] ?? [],
                        timestamp: message.timestamp,
                        index: index++,
                    });
                });

                renderableArtifacts.forEach((artifact) => {
                    orderedItems.push({
                        kind: "artifact",
                        item: artifact,
                        timestamp: artifact.timestamp,
                        index: index++,
                    });
                });

                orderedItems.sort((a, b) => a.timestamp - b.timestamp || a.index - b.index);

                const rendered: ReactNode[] = [];
                let renderedActivity = false;
                let renderedAssistant = false;

                const renderActivityCarrier = (key: string) => {
                    const showActivity = !renderedActivity;
                    renderedActivity = true;
                    renderedAssistant = true;
                    return (
                        <AssistantMessageComponent
                            key={key}
                            message={null}
                            isGreeting={false}
                            artifacts={showActivity ? activityCarrierArtifacts : []}
                            steps={showActivity ? turnSteps : []}
                            isError={turn.state === 'error'}
                            isHistorical={turn.historical}
                            turnId={turnId}
                            showActivity={showActivity}
                            showTimelineArtifacts={showActivity}
                        />
                    );
                };

                const renderAssistantMessage = (key: string, message: AssistantMessage, artifacts: UnknownArtifact[]) => {
                    renderedAssistant = true;
                    return (
                        <AssistantMessageComponent
                            key={key}
                            message={message.text}
                            isGreeting={false}
                            artifacts={artifacts}
                            steps={[]}
                            isError={turn.state === 'error'}
                            isHistorical={turn.historical}
                            turnId={turnId}
                            showActivity={false}
                            showTimelineArtifacts={false}
                        />
                    );
                };

                orderedItems.forEach((entry) => {
                    if (entry.kind === "user") {
                        rendered.push(
                            <UserMessageComponent
                                key={`user_${turnId}_${entry.item.timestamp}_${entry.index}`}
                                message={entry.item}
                                turnId={turnId}
                            />
                        );
                        return;
                    }

                    if (entry.kind === "assistant") {
                        rendered.push(renderAssistantMessage(`assistant_${turnId}_${entry.item.timestamp}_${entry.index}`, entry.item, entry.artifacts));
                        return;
                    }

                    if (entry.kind === "activity") {
                        rendered.push(renderActivityCarrier(`activity_${turnId}_${entry.timestamp}_${entry.index}`));
                        return;
                    }

                    const Component = getChatLogComponent(entry.item.artifactType);
                    if (Component) {
                        rendered.push(
                            <Component
                                key={`artifact_${turnId}_${entry.item.artifactType}_${entry.item.timestamp}_${entry.index}`}
                                item={entry.item}
                                historical={turn.historical}
                            />
                        );
                    }
                });

                return (<Fragment key={turnId}>
                    {rendered}
                    {!renderedAssistant && (sharedActivityArtifacts.length > 0 || turnSteps.length > 0 || turn.state === "inProgress") &&
                        renderActivityCarrier(`assistant_activity_${turnId}`)}
                </Fragment>)
            })}
        </>)
    }, [turnOrder, turns])

    return useMemo(() => {
        return (
            <div
                className="h-full w-full relative"
                id="ChatLog"
            >
                <div
                    className="h-full w-full overflow-x-auto"
                    ref={logContainerRef}
                    onScroll={onScroll}
                >
                    <div
                        ref={logContentRef}
                        className="border-r border-l border-gray-200 mx-auto min-h-full bg-slate-50 w-full max-w-[50vw]">
                        <div className="px-10 py-4">
                            {turnsRender}
                            {processingRender}
                            {followUpQuestionsMemo}
                        </div>
                        <div aria-hidden="true" style={{height: inputClearance}}/>
                    </div>
                </div>
                <div className={"absolute bottom-24 right-1/4 pr-6"}>
                    {scrollDownButton}
                </div>
            </div>
        )
    }, [followUpQuestionsMemo, inputClearance, onScroll, processingRender, scrollDownButton, turnsRender])
}

export default ChatLog;
