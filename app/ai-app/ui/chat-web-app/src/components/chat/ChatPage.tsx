import SingleChatApp from "./Chat.tsx";
import {useNavigate, useParams} from "react-router-dom";
import {useEffect, useMemo} from "react";
import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {selectConversationId} from "../../features/chat/chatStateSlice.ts";
import {loadConversations} from "../../features/conversations/conversationsMiddleware.ts";
import {selectIsConversationLoading} from "../../features/conversations/conversationsSlice.ts";
import IconContainer from "../IconContainer.tsx";
import {LoaderCircle} from "lucide-react";
import {getChatPagePath} from "../../features/chat/configHelper.ts";

function ChatPage() {
    const urlParams = useParams();
    const dispatch = useAppDispatch();
    const navigate = useNavigate();

    const requestedConversationID = useMemo(() => {
        return urlParams.conversationID
    }, [urlParams]);

    const conversationId = useAppSelector(selectConversationId);
    const conversationLoading = useAppSelector(selectIsConversationLoading);

    useEffect(() => {
        if (conversationId === undefined || conversationLoading)
            return;

        const path = conversationId === null ? getChatPagePath() : getChatPagePath() + "/" + conversationId;
        if (window.location.pathname !== path) {
            navigate(path);
        } else {
            console.debug("skip navigation", path);
        }

    }, [conversationId, navigate, conversationLoading]);

    useEffect(() => {
        dispatch(loadConversations(requestedConversationID ?? null))
    }, [requestedConversationID, dispatch]);

    return useMemo(() => {
        return <div className={"w-screen h-screen relative"}>
            <SingleChatApp/>
            {conversationLoading &&
                <div className={"w-screen h-screen absolute top-0 left-0 backdrop-blur-[1px] bg-black/15 z-30"}>
                    <div className={"w-full h-full content-center"}>
                        <IconContainer icon={LoaderCircle} size={4} className={"animate-spin mx-auto text-black/25"}/>
                    </div>
                </div>
            }
        </div>
    }, [conversationLoading])

}

export default ChatPage
