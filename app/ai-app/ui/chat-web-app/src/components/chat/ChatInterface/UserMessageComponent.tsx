import {UserMessage} from "../../../features/chat/chatTypes.ts";
import {useCallback, useMemo} from "react";
import {getFileIcon} from "../../FileIcons.tsx";
import {ClipboardCopy, User} from "lucide-react";
import {formatDateToLocalString} from "../../../utils/dateTimeUtils.ts";
import IconContainer from "../../IconContainer.tsx";
import {copyMarkdownToClipboard} from "../../Clipboard.ts";

interface UserMessageProps {
    turnId: string;
    message: UserMessage;
}

export const UserMessageComponent = ({turnId, message}: UserMessageProps) => {
    const copyToClipboard = useCallback((text: string) => {
        copyMarkdownToClipboard(text).catch((err) => {
            console.error("Could not copy message", err);
        })
    }, []);

    return useMemo(() => {
        return (
            <div id={`user_message_${turnId}`} className="flex justify-end">
                <div className={"flex flex-col"}>
                    <div className="flex flex-row p-3 rounded-2xl bg-gray-200 text-black">
                        <div className="flex flex-col">
                            {message.attachments && message.attachments.length > 0 && (
                                <div className="flex flex-row gap-1 flex-wrap">
                                    {message.attachments?.map(attachment => {
                                        return (
                                            <div
                                                key={attachment.name}
                                                className="flex items-center border-2 px-2 py-1 rounded-xl border-gray-300 bg-gray-100"
                                            >{getFileIcon(attachment.name, 18, undefined, "mr-1")}{attachment.name}</div>
                                        )
                                    })}
                                </div>)}
                            {message &&
                                <p className="text-sm leading-relaxed whitespace-pre-wrap pt-1">{message.text}</p>}
                        </div>
                        <div
                            className="w-8 h-8 rounded-full bg-gray-300 ml-3 flex items-center justify-center shrink-0">
                            <User size={16} className="text-gray-600"/>
                        </div>
                    </div>
                    <div className={"mt-1 ml-auto mr-1 flex flex-row gap-1"}>
                        <span
                            className={"text-sm text-gray-600"}>{formatDateToLocalString(new Date(message.timestamp), true)}</span>
                        <button
                            className={"block cursor-pointer text-gray-600 hover:text-gray-800 transition-colors duration-200"}
                            onClick={() => copyToClipboard(message.text)}
                        >
                            <IconContainer icon={ClipboardCopy} size={1}/>
                        </button>
                    </div>
                </div>
            </div>
        )
    }, [turnId, message, copyToClipboard])
}