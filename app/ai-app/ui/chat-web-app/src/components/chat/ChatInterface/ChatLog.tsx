import {Fragment, useCallback, useEffect, useMemo, useRef, useState} from "react";
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

    const autoScroll = useRef(true);

    useEffect(() => {
        if (autoScroll.current && logContainerRef.current) {
            logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
        }
    }, [turns, inProgress]);

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
                if (logContainerRef.current) {
                    logContainerRef.current.scrollTo({
                        top: logContainerRef.current.scrollHeight,
                        behavior: "smooth",
                    })
                }
            }}
        >
            <IconContainer icon={ChevronsDown} size={2}
                           className={"text-gray-600 hover:text-gray-800 transition-colors duration-200"}/>
        </motion.button>
    }, [showScrollDown])

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

                return (<Fragment key={turnId}>
                    {turn.userMessage.text && <UserMessageComponent message={turn.userMessage} turnId={turnId}/>}
                    <AssistantMessageComponent
                        message={turn.answer}
                        isGreeting={false}
                        artifacts={turn.artifacts}
                        steps={Object.values(turn.steps)}
                        isError={turn.state === 'error'}
                        isHistorical={turn.historical}
                        followupMessages={turn.additionalUserMessages}
                        turnId={turnId}
                    />
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
                        className="border-r border-l border-gray-200 mx-auto min-h-full bg-slate-50 w-full max-w-[50vw]">
                        <div className="px-10 py-4">
                            {turnsRender}
                            {processingRender}
                            {followUpQuestionsMemo}
                        </div>
                        <div className="pb-22"/>
                    </div>
                </div>
                <div className={"absolute bottom-24 right-1/4 pr-6"}>
                    {scrollDownButton}
                </div>
            </div>
        )
    }, [followUpQuestionsMemo, onScroll, processingRender, scrollDownButton, turnsRender])
}

export default ChatLog;