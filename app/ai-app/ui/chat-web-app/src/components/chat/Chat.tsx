/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

// Chat.tsx
import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";

import ChatInterface from "./ChatInterface/ChatInterface.tsx";
import {useAppSelector} from "../../app/store.ts";
import {selectConversationId, selectCurrentTurn,} from "../../features/chat/chatStateSlice.ts";
import ChatSidePanel from "../../features/chatSidePanel/ChatSidePanel.tsx";
import ChatHeader from "./ChatHeader.tsx";
import AnimatedExpander from "../AnimatedExpander.tsx";
import ChatCanvas from "../../features/canvas/ChatCanvas.tsx";
import {CanvasItemLink, ChatCanvasContext, ChatCanvasContextValue} from "../../features/canvas/canvasContext.tsx";
import {
    addCanvasItemExtension,
    getCanvasArtifactTypes,
    getCanvasItemLinkGenerator
} from "../../features/extensions/canvasExtensions.tsx";
import {CodeExecArtifactType} from "../../features/logExtensions/codeExec/types.ts";
import {CanvasArtifactType} from "../../features/logExtensions/canvas/types.ts";
import CanvasItem from "../../features/logExtensions/canvas/CanvasItem.tsx";
import {getCanvasArtifactLink, matchesCanvasArtifact} from "../../features/logExtensions/canvas/utils.ts";
import {WebSearchArtifactType} from "../../features/logExtensions/webSearch/types.ts";
import WebSearchCanvasItem from "../../features/logExtensions/webSearch/WebSearchCanvasItem.tsx";
import {getWebSearchArtifactLink, matchesWebSearchArtifact} from "../../features/logExtensions/webSearch/utils.ts";
import {addChatLogExtension} from "../../features/extensions/logExtesnions.ts";
import {CanvasLogItem} from "../../features/logExtensions/canvas/CanvasLogItem.tsx";
import CodeExecLogItem from "../../features/logExtensions/codeExec/CodeExecLogItem.tsx";
import WebSearchLogItem from "../../features/logExtensions/webSearch/WebSearchLogItem.tsx";
import {TimelineTextArtifactType} from "../../features/logExtensions/timelineText/types.ts";
import TimelineTextLogItem from "../../features/logExtensions/timelineText/TimelineTextLogItem.tsx";
import useSharedConfigProvider from "../../features/sharedConfigProvider/sharedConfigProvider.tsx";

//chat log extensions
addChatLogExtension(CanvasArtifactType, CanvasLogItem)
addChatLogExtension(CodeExecArtifactType, CodeExecLogItem)
addChatLogExtension(WebSearchArtifactType, WebSearchLogItem)
addChatLogExtension(TimelineTextArtifactType, TimelineTextLogItem)

// canvas extension
addCanvasItemExtension(CanvasArtifactType, CanvasItem, getCanvasArtifactLink, matchesCanvasArtifact)
addCanvasItemExtension(WebSearchArtifactType, WebSearchCanvasItem, getWebSearchArtifactLink, matchesWebSearchArtifact)

const SingleChatApp: React.FC = () => {
    const currentTurn = useAppSelector(selectCurrentTurn);
    const conversationId = useAppSelector(selectConversationId);
    const chatCanvasRef = useRef<HTMLDivElement>(null);
    const [canvasItemLink, setCanvasItemLink] = useState<CanvasItemLink | null>(null);
    const [overrideCanvasItemLink, setOverrideCanvasItemLink] = useState<boolean>(false);

    useSharedConfigProvider()

    const lastCanvasItem = useMemo(() => {
        if (currentTurn == null) return null;
        const canvasArtifactTypes = getCanvasArtifactTypes()
        const canvasArtifacts = currentTurn.artifacts.filter(artifact => {
            return canvasArtifactTypes.includes(artifact.artifactType);
        })
        return canvasArtifacts.length > 0 ? canvasArtifacts[0] : null;
    }, [currentTurn])

    useEffect(() => {
        setCanvasItemLink(null);
    }, [conversationId]);

    useEffect(() => {
        if (currentTurn) {
            if (!overrideCanvasItemLink && lastCanvasItem && !lastCanvasItem.historical) {
                setCanvasItemLink(getCanvasItemLinkGenerator(lastCanvasItem.artifactType)(lastCanvasItem))
            }
        } else {
            setOverrideCanvasItemLink(false)
        }

    }, [canvasItemLink, currentTurn, lastCanvasItem, overrideCanvasItemLink]);

    const showItem = useCallback((link: CanvasItemLink | null) => {
        if (currentTurn) {
            setOverrideCanvasItemLink(true);
        }
        setCanvasItemLink(link);
    }, [currentTurn])

    const chatCanvasContextValue = useMemo<ChatCanvasContextValue>(() => {
        return {
            showItem,
            itemLink: canvasItemLink
        }
    }, [canvasItemLink, showItem])


    return useMemo(() => {
        return <div id={SingleChatApp.name}
                    className="flex flex-col h-full w-full min-h-0 min-w-0 bg-slate-100 overflow-hidden">
            <ChatHeader/>

            <div className={`flex flex-row overflow-hidden flex-1 w-full min-h-0 min-w-0`}>
                <ChatSidePanel/>
                <div className={`flex-1 flex flex-col h-full`}>
                    <div className={`flex flex-row flex-1 min-h-0 min-w-0`}>
                        <ChatCanvasContext value={chatCanvasContextValue}>
                            <ChatInterface/>
                            <AnimatedExpander contentRef={chatCanvasRef} expanded={!!canvasItemLink}>
                                <ChatCanvas ref={chatCanvasRef}/>
                            </AnimatedExpander>
                        </ChatCanvasContext>
                    </div>
                </div>
            </div>
        </div>
    }, [canvasItemLink, chatCanvasContextValue])
};

export default SingleChatApp;
