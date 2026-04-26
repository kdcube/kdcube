import {useCallback} from "react";
import {ArrowRight} from "lucide-react";

import {useAppDispatch} from "../../../app/store.ts";
import {setUserMessage} from "../../../features/chat/chatStateSlice.ts";

interface Props {
    label: string;
    prompt: string;
}

/**
 * Fills the composer with a context-aware prompt. Two-tap flow: user can
 * still edit the prompt before pressing Enter to send.
 */
function AskAgentButton({label, prompt}: Props) {
    const dispatch = useAppDispatch();
    const onClick = useCallback(() => {
        dispatch(setUserMessage(prompt));
    }, [dispatch, prompt]);

    return (
        <button
            type="button"
            onClick={onClick}
            className="mt-3 inline-flex flex-row items-center gap-1 px-3 py-1.5 text-xs font-medium bg-blue-50 border border-blue-300 text-blue-800 rounded-full hover:bg-blue-100 transition-colors"
            title={prompt}
        >
            <span>{label}</span>
            <ArrowRight size={12}/>
        </button>
    );
}

export default AskAgentButton;
