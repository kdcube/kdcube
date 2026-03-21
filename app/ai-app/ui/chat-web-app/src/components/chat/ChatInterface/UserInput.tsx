import {useCallback, useMemo, useRef} from "react";
import {CirclePlus, Send, X} from "lucide-react";
import {selectFileAdvanced} from "../../shared.ts";
import {useAppDispatch, useAppSelector} from "../../../app/store.ts";
import {
    addUserAttachments, removeUserAttachment,
    selectCurrentTurn,
    selectLocked,
    selectUserAttachments,
    selectUserMessage, setUserMessage
} from "../../../features/chat/chatStateSlice.ts";
import {sendChatMessage} from "../../../features/chat/chatServiceMiddleware.ts";
import {UserAttachment} from "../../../features/chat/chatTypes.ts";
import {dismissNotification, selectPopupNotifications} from "../../../features/popupNotifications/popupsSlice.ts";

const notificationColors: Record<string, string> = {
    error: "bg-red-50 border-red-200 text-red-800",
    warning: "bg-yellow-50 border-yellow-200 text-yellow-800",
    info: "bg-blue-50 border-blue-200 text-blue-800",
};

interface UserInputProps {
    lockMessage?: string;
    inputPlaceholder?: string;
}

const UserInput = ({lockMessage, inputPlaceholder = "Ask me anything..."}: UserInputProps) => {
    const dispatch = useAppDispatch();
    const userInput = useAppSelector(selectUserMessage)
    const userAttachments = useAppSelector(selectUserAttachments)
    const isLocked = useAppSelector(selectLocked)
    const currentTurn = useAppSelector(selectCurrentTurn)
    const inProgress = useMemo(() => !!currentTurn, [currentTurn])

    const notifications = useAppSelector(selectPopupNotifications);
    const hasNotifications = notifications.length > 0;
    const hasErrors = notifications.some(n => n.type === "error");

    const userInputFieldRef = useRef<HTMLTextAreaElement | null>(null);

    const sendMessage = useCallback((message?: string) => {
        dispatch(sendChatMessage({
            message
        }))
    }, [dispatch]);

    const addInputFiles = useCallback((files: File[]) => {
        dispatch(addUserAttachments(files))
    }, [dispatch])

    const removeInputFiles = useCallback((attachment: UserAttachment) => {
        dispatch(removeUserAttachment(attachment.fileKey))
    }, [dispatch])

    const setUserInputValue = useCallback((userInput: string) => {
        dispatch(setUserMessage(userInput));
    }, [dispatch])

    return useMemo(() => {
        const inputDisabled = isLocked || hasErrors;
        const sendDisabled = isLocked || hasErrors || inProgress || (!userInput.trim() && userAttachments.length == 0);

        return (
            <div
                id="UserInput"
                className="absolute -left-2 z-10 bottom-0 w-full"
                onClick={() => userInputFieldRef.current?.focus()}
            >
                <div className="pointer-events-none mx-auto px-8 w-full max-w-[50vw]">
                    {hasNotifications && notifications.map((msg) => (
                        <div
                            key={msg.id}
                            className={`flex items-center gap-2 px-4 py-3 mb-2 text-sm rounded-xl border pointer-events-auto ${notificationColors[msg.type] ?? notificationColors.info}`}
                        >
                            <span className="flex-1">{msg.text}</span>
                            <button
                                className="shrink-0 opacity-60 hover:opacity-100 cursor-pointer"
                                onClick={() => dispatch(dismissNotification(msg.id))}
                                aria-label="Dismiss"
                            >
                                <X size={14}/>
                            </button>
                        </div>
                    ))}
                    <div
                        className={`flex flex-col mx-auto border rounded-t-xl border-gray-400 shadow-sm pointer-events-auto ${isLocked ? "bg-yellow-50" : "bg-white"}`}
                    >
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
                            {isLocked && !hasErrors ? (
                                <div className="flex-1 m-3 flex flex-col items-center">
                                    <span
                                        className="font-semibold text-gray-400">{lockMessage || "Daily token limit reached. Please try again later."}</span>
                                </div>
                            ) : (
                                <textarea
                                    value={userInput}
                                    onChange={(e) => setUserInputValue(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (!sendDisabled && e.key === "Enter" && !e.shiftKey) {
                                            e.preventDefault();
                                            sendMessage();
                                        }
                                    }}
                                    placeholder={inputPlaceholder}
                                    disabled={inputDisabled}
                                    className="flex-1 m-3 resize-none grow field-sizing-content focus:outline-none overflow-y-auto disabled:opacity-40 disabled:cursor-not-allowed"
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
                                disabled={sendDisabled}
                                className=" mb-3 rounded-lg font-medium text-gray-600 hover:text-gray-900 disabled:text-gray-300"
                                aria-label="Add file"
                                title="Add file"
                            >
                                <CirclePlus size={18} className={`${sendDisabled ?
                                    (inProgress ? "cursor-wait" : "cursor-auto") :
                                    "cursor-pointer"}`}/>
                            </button>
                            <button
                                onClick={() => {
                                    sendMessage()
                                }}
                                disabled={sendDisabled}
                                className="mb-3 mr-3 rounded-lg font-medium text-gray-600 hover:text-gray-900 disabled:text-gray-300 ml-auto"
                                aria-label="Send message"
                                title="Send"
                            >
                                <Send size={18} className={`${sendDisabled ?
                                    (userInput.trim() || userAttachments.length > 0 ? "cursor-wait" : "cursor-auto") :
                                    "cursor-pointer"}`}/>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        );
    }, [addInputFiles, dispatch, hasErrors, hasNotifications, inProgress, inputPlaceholder, isLocked, lockMessage, notifications, removeInputFiles, sendMessage, setUserInputValue, userAttachments, userInput]);
}

export default UserInput;