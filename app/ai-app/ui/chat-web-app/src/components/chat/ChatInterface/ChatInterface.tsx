/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

// ChatInterface.tsx
import {useEffect, useMemo} from "react";
import {useAppDispatch, useAppSelector} from "../../../app/store.ts";
import {selectChatStayConnected} from "../../../features/chat/chatStateSlice.ts";
import {connectChat} from "../../../features/chat/chatServiceMiddleware.ts";
import UserInput from "./UserInput.tsx";
import ChatLog from "./ChatLog.tsx";
import QuickActionChips from "../../configAssistant/QuickActionChips.tsx";


const ChatInterface = () => {
    const dispatch = useAppDispatch();
    const stayConnected = useAppSelector(selectChatStayConnected)

    useEffect(() => {
        if (!stayConnected)
            dispatch(connectChat())
    }, [dispatch, stayConnected]);


    return useMemo(() => {
        return <div id={ChatInterface.name}
                    className="flex-1 flex flex-col bg-slate-100 min-h-0 min-w-0 transition-all duration-100 ease-out w-full relative"
        >
            <ChatLog/>
            <QuickActionChips/>
            <UserInput/>
        </div>
    }, [])
};

export default ChatInterface;
