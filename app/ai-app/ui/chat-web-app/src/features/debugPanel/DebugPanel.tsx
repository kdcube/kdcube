import {WidgetPanelProps} from "../chatSidePanel/ChatSidePanel.tsx";
import {useDispatch} from "react-redux";
import {loadExampleConversation} from "../chat/chatStateSlice.ts";

const DebugPanel = ({visible, className}: WidgetPanelProps) => {
    const dispatch = useDispatch();
    return <div className={`${className ?? ""} ${visible ? "" : "pointer-events-none hidden"} ${className}`}>
        <button
            className={"px-2 py-1 border cursor-pointer hover:bg-gray-100 m-2"}
            onClick={() => {
                dispatch(loadExampleConversation())
            }}
        >Load example conversation
        </button>
    </div>
}

export default DebugPanel;