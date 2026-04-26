/*
 * SPDX-License-Identifier: MIT
 * Configuration Assistant page — chat-centric helper for bundle developers.
 * Reuses the existing chat infrastructure but tags every chat request with
 * `mode=config_assistant` so the backend selects the persona prompt and
 * (later) emits the code_core.* artifact types.
 */
import {useEffect, useMemo} from "react";
import {useNavigate, useParams} from "react-router-dom";
import {LoaderCircle} from "lucide-react";

import SingleChatApp from "../chat/Chat.tsx";
import IconContainer from "../IconContainer.tsx";
import InspectPanel from "./InspectPanel.tsx";
import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {selectConversationId} from "../../features/chat/chatStateSlice.ts";
import {loadConversations} from "../../features/conversations/conversationsMiddleware.ts";
import {selectIsConversationLoading} from "../../features/conversations/conversationsSlice.ts";
import {selectConfigAssistantPath} from "../../features/chat/chatSettingsSlice.ts";
import {resetConfigAssistant, setMode} from "../../features/configAssistant/configAssistantSlice.ts";

const CONFIG_ASSISTANT_MODE = "config_assistant";

function ConfigAssistantPage() {
    const urlParams = useParams();
    const dispatch = useAppDispatch();
    const navigate = useNavigate();

    const requestedConversationID = useMemo(() => urlParams.conversationID, [urlParams]);
    const conversationId = useAppSelector(selectConversationId);
    const conversationLoading = useAppSelector(selectIsConversationLoading);
    const configAssistantPath = useAppSelector(selectConfigAssistantPath);

    useEffect(() => {
        dispatch(setMode(CONFIG_ASSISTANT_MODE));
        return () => {
            dispatch(resetConfigAssistant());
        };
    }, [dispatch]);

    useEffect(() => {
        if (conversationId === undefined || conversationLoading) return;
        const path = conversationId === null
            ? configAssistantPath
            : configAssistantPath + "/" + conversationId;
        if (window.location.pathname !== path) {
            navigate(path);
        }
    }, [conversationId, conversationLoading, configAssistantPath, navigate]);

    useEffect(() => {
        dispatch(loadConversations(requestedConversationID ?? null));
    }, [requestedConversationID, dispatch]);

    return useMemo(() => (
        <div className="w-screen h-screen relative">
            <SingleChatApp rightPanel={<InspectPanel/>}/>
            {conversationLoading && (
                <div className="w-screen h-screen absolute top-0 left-0 backdrop-blur-[1px] bg-black/15 z-30">
                    <div className="w-full h-full content-center">
                        <IconContainer
                            icon={LoaderCircle}
                            size={4}
                            className="animate-spin mx-auto text-black/25"
                        />
                    </div>
                </div>
            )}
        </div>
    ), [conversationLoading]);
}

export default ConfigAssistantPage;
