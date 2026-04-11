import {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {CirclePlus, Send, Square, X} from "lucide-react";
import {selectFileAdvanced} from "../../shared.ts";
import {useAppDispatch, useAppSelector} from "../../../app/store.ts";
import {
    addUserAttachments,
    removeUserAttachment,
    selectCurrentTurn,
    selectUserAttachments,
    selectUserInputLocked,
    selectUserInputLockMessage,
    selectUserMessage,
    setUserMessage
} from "../../../features/chat/chatStateSlice.ts";
import {sendChatMessage} from "../../../features/chat/chatServiceMiddleware.ts";
import {UserAttachment} from "../../../features/chat/chatTypes.ts";
import PopupArea from "../../../features/popupNotifications/PopupArea.tsx";

interface UserInputProps {
    inputPlaceholder?: string;
}

const UserInput = ({inputPlaceholder = "Ask me anything..."}: UserInputProps) => {
    const dispatch = useAppDispatch();
    const userInput = useAppSelector(selectUserMessage)
    const userAttachments = useAppSelector(selectUserAttachments)
    const isLocked = useAppSelector(selectUserInputLocked)
    const lockMessage = useAppSelector(selectUserInputLockMessage)
    const currentTurn = useAppSelector(selectCurrentTurn)
    const inProgress = useMemo(() => !!currentTurn, [currentTurn])
    const [steerPanelOpen, setSteerPanelOpen] = useState(false)
    const [steerMessage, setSteerMessage] = useState("")

    const userInputFieldRef = useRef<HTMLTextAreaElement | null>(null);
    const steerInputRef = useRef<HTMLTextAreaElement | null>(null);

    const sendMessage = useCallback((message?: string) => {
        dispatch(sendChatMessage({
            message
        }))
    }, [dispatch]);

    const sendSteer = useCallback((message?: string) => {
        dispatch(sendChatMessage({
            message,
            continuationKind: "steer",
            targetTurnId: currentTurn?.id,
        }))
        setSteerMessage("")
        setSteerPanelOpen(false)
    }, [currentTurn?.id, dispatch]);

    const addInputFiles = useCallback((files: File[]) => {
        dispatch(addUserAttachments(files))
    }, [dispatch])

    const removeInputFiles = useCallback((attachment: UserAttachment) => {
        dispatch(removeUserAttachment(attachment.fileKey))
    }, [dispatch])

    const setUserInputValue = useCallback((userInput: string) => {
        dispatch(setUserMessage(userInput));
    }, [dispatch])

    useEffect(() => {
        if (!inProgress) {
            setSteerPanelOpen(false)
            setSteerMessage("")
        }
    }, [inProgress])

    useEffect(() => {
        if (steerPanelOpen) {
            steerInputRef.current?.focus()
        }
    }, [steerPanelOpen])

    const inputDisabled = isLocked;
    const sendDisabled = isLocked || (!userInput.trim() && userAttachments.length == 0);
    const addFilesDisabled = isLocked;
    const stopDisabled = isLocked || !inProgress;
    const steerSendDisabled = isLocked || !steerMessage.trim();
    const effectivePlaceholder = inProgress
        ? "Send follow-up while the current turn is still running..."
        : inputPlaceholder;

    return useMemo(() => {
        return (
            <div
                id="UserInput"
                className="absolute -left-2 z-10 bottom-0 w-full"
                onClick={() => userInputFieldRef.current?.focus()}
            >
                <div className="pointer-events-none mx-auto px-8 w-full max-w-[50vw]">
                    <PopupArea className={"max-w-full mb-2"}/>
                    <div
                        className={`flex flex-col mx-auto border rounded-t-xl border-gray-400 shadow-sm pointer-events-auto ${isLocked ? "bg-yellow-50" : "bg-white"}`}
                    >
                        {steerPanelOpen && inProgress && (
                            <div className="mx-3 mt-3 rounded-xl border border-gray-300 bg-gray-50 p-3">
                                <div className="flex items-start gap-3">
                                    <div className="min-w-0 flex-1">
                                        <div className="text-sm font-semibold text-gray-800">Stop current turn</div>
                                        <div className="mt-1 text-xs text-gray-500">
                                            Send a steer event now. You can leave the message blank to stop without extra instructions.
                                        </div>
                                    </div>
                                    <button
                                        onClick={() => {
                                            setSteerPanelOpen(false)
                                            setSteerMessage("")
                                        }}
                                        className="text-gray-400 hover:text-gray-700"
                                        aria-label="Close stop panel"
                                        title="Close"
                                    >
                                        <X size={14}/>
                                    </button>
                                </div>
                                <textarea
                                    ref={steerInputRef}
                                    value={steerMessage}
                                    onChange={(e) => setSteerMessage(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (!steerSendDisabled && e.key === "Enter" && !e.shiftKey) {
                                            e.preventDefault();
                                            sendSteer(steerMessage.trim())
                                        }
                                    }}
                                    placeholder="Optional steer message..."
                                    disabled={isLocked}
                                    className="mt-3 min-h-20 w-full resize-none rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-gray-400 disabled:opacity-40"
                                    rows={3}
                                />
                                <div className="mt-3 flex items-center justify-end gap-2">
                                    <button
                                        onClick={() => sendSteer("")}
                                        disabled={isLocked}
                                        className="rounded-lg border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                        Stop now
                                    </button>
                                    <button
                                        onClick={() => sendSteer(steerMessage.trim())}
                                        disabled={steerSendDisabled}
                                        className="rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                        Stop + send
                                    </button>
                                </div>
                            </div>
                        )}
                        {userAttachments && userAttachments.length > 0 &&
                            <div className="flex flex-row flex-wrap p-3 gap-1">
                                {
                                    userAttachments.map((file, i) => {
                                        return (<div
                                            className="flex border-2 border-gray-400 bg-gray-50 rounded-2xl px-3 py-1 items-center"
                                            key={`input-file-${i}`}
                                        >
                                            <span>{file.name}</span>
                                            <button
                                                className="pl-1 text-gray-400 hover:text-gray-600 cursor-pointer"
                                                onClick={() => {
                                                    removeInputFiles(file)
                                                }}
                                            >
                                                <X size={12}/>
                                            </button>
                                        </div>)
                                    })
                                }
                            </div>}
                        <div className="flex max-h-72 min-h-12 w-full">
                            {isLocked ?
                                <div className="flex-1 m-3 flex flex-col items-center">
                                    <span
                                        className="font-semibold text-gray-400">{lockMessage || "Daily token limit reached. Please try again later."}</span>
                                </div>
                                :
                                <textarea
                                    value={userInput}
                                    onChange={(e) => setUserInputValue(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (!sendDisabled && e.key === "Enter" && !e.shiftKey) {
                                            e.preventDefault();
                                            sendMessage();
                                        }
                                    }}
                                    placeholder={effectivePlaceholder}
                                    disabled={inputDisabled}
                                    className="flex-1 m-3 resize-none grow field-sizing-content focus:outline-none overflow-y-auto disabled:opacity-40 disabled:cursor-not-allowed"
                                    rows={2}
                                    ref={userInputFieldRef}
                                />
                            }

                        </div>
                        <div className="flex">
                            <div className="pl-2"/>
                            <button
                                onClick={() => {
                                    selectFileAdvanced({multiple: true}).then((res) => {
                                        addInputFiles(res)
                                    })
                                }}
                                disabled={addFilesDisabled}
                                className=" mb-3 rounded-lg font-medium text-gray-600 hover:text-gray-900 disabled:text-gray-300"
                                aria-label="Add file"
                                title="Add file"
                            >
                                <CirclePlus size={18} className={`${addFilesDisabled ?
                                    "cursor-auto" :
                                    "cursor-pointer"}`}/>
                            </button>
                            {inProgress && (
                                <button
                                    onClick={() => setSteerPanelOpen((prev) => !prev)}
                                    disabled={stopDisabled}
                                    className="mb-3 rounded-lg font-medium text-gray-600 hover:text-gray-900 disabled:text-gray-300 ml-2"
                                    aria-label="Stop current turn"
                                    title="Stop current turn"
                                >
                                    <Square size={18} className={`${stopDisabled ? "cursor-auto" : "cursor-pointer"}`}/>
                                </button>
                            )}
                            <button
                                onClick={() => {
                                    sendMessage()
                                }}
                                disabled={sendDisabled}
                                className="mb-3 mr-3 rounded-lg font-medium text-gray-600 hover:text-gray-900 disabled:text-gray-300 ml-auto"
                                aria-label={inProgress ? "Send follow-up" : "Send message"}
                                title={inProgress ? "Send follow-up" : "Send"}
                            >
                                <Send size={18} className={`${sendDisabled ?
                                    (userInput.trim() || userAttachments.length > 0 ? "cursor-auto" : "cursor-auto") :
                                    "cursor-pointer"}`}/>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        );
    }, [addFilesDisabled, addInputFiles, effectivePlaceholder, inProgress, inputDisabled, isLocked, lockMessage, removeInputFiles, sendDisabled, sendMessage, sendSteer, setUserInputValue, steerMessage, steerPanelOpen, steerSendDisabled, stopDisabled, userAttachments, userInput]);
}

export default UserInput;
