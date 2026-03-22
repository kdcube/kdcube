import {WidgetPanelProps} from "../chatSidePanel/ChatSidePanel.tsx";
import {useDispatch} from "react-redux";
import {loadExampleConversation} from "../chat/chatStateSlice.ts";
import {ReactNode, useMemo} from "react";
import {pushNotification} from "../popupNotifications/popupsSlice.ts";

interface DebugPanelProps {
    name: string;
    children: ReactNode | ReactNode[];
}

const Section = ({name, children}: DebugPanelProps) => {
    return useMemo(() => {
        return <div className={"flex flex-col p-2 w-full border border-gray-200"}>
            <div>{name}</div>
            <div>
                {children}
            </div>
        </div>
    }, [children, name]);
}

const DebugPanel = ({visible, className}: WidgetPanelProps) => {
    const dispatch = useDispatch();
    return <div className={`${className ?? ""} ${visible ? "" : "pointer-events-none hidden"} ${className}`}>
        <div className={"flex flex-col w-full h-full overflow-y-auto"}>
            <Section name={"Conversation"}>
                <button
                    className={"px-2 py-1 border cursor-pointer hover:bg-gray-100 m-2"}
                    onClick={() => {
                        dispatch(loadExampleConversation())
                    }}
                >Load example conversation
                </button>
            </Section>
            <Section name={"Notifications"}>
                <button
                    className={"px-2 py-1 border cursor-pointer hover:bg-gray-100 m-2"}
                    onClick={() => {
                        dispatch(pushNotification({text: "This is an info notification", type: "info"}))
                    }}
                >Info
                </button>
                <button
                    className={"px-2 py-1 border cursor-pointer hover:bg-gray-100 m-2"}
                    onClick={() => {
                        dispatch(pushNotification({text: "This is a warning notification", type: "warning"}))
                    }}
                >Warning
                </button>
                <button
                    className={"px-2 py-1 border cursor-pointer hover:bg-gray-100 m-2"}
                    onClick={() => {
                        dispatch(pushNotification({text: "This is an error notification", type: "error"}))
                    }}
                >Error
                </button>
            </Section>
        </div>
    </div>
}

export default DebugPanel;