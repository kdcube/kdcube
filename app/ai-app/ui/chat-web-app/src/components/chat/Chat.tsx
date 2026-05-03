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
import {getCanvasArtifactTypes, getCanvasItemLinkGenerator} from "../../features/extensions/canvasExtensions.ts";
import useSharedConfigProvider from "../../features/sharedConfigProvider/sharedConfigProvider.tsx";
import ConversationHeader from "../../features/conversationHeader/ConversationHeader.tsx";
import InspectDrawer from "../configAssistant/InspectDrawer.tsx";
import {
    selectConfigAssistantDrawerMaximized,
    selectConfigAssistantDrawerOpen,
    selectConfigAssistantMode,
} from "../../features/configAssistant/configAssistantSlice.ts";

// Widths must match the InspectDrawer's class names.
// Default: `w-[min(820px,55vw)] min-w-[560px]`
const drawerDefaultWidthPx = (vw: number) => Math.max(560, Math.min(820, vw * 0.55));
// Maximized: `w-[min(1300px,92vw)]`
const drawerMaximizedWidthPx = (vw: number) => Math.min(1300, vw * 0.92);

const SingleChatApp: React.FC = () => {
    const currentTurn = useAppSelector(selectCurrentTurn);
    const conversationId = useAppSelector(selectConversationId);
    const chatCanvasRef = useRef<HTMLDivElement>(null);
    const [canvasItemLink, setCanvasItemLink] = useState<CanvasItemLink | null>(null);
    const [overrideCanvasItemLink, setOverrideCanvasItemLink] = useState<boolean>(false);

    // When the Configuration Assistant drawer is open, push the chat layout
    // left by the drawer's width so the composer / messages aren't covered.
    const configAssistantMode = useAppSelector(selectConfigAssistantMode);
    const configAssistantDrawerOpen = useAppSelector(selectConfigAssistantDrawerOpen);
    const configAssistantDrawerMaximized = useAppSelector(selectConfigAssistantDrawerMaximized);
    const [vw, setVw] = useState<number>(
        typeof window !== "undefined" ? window.innerWidth : 1280,
    );
    useEffect(() => {
        const onResize = () => setVw(window.innerWidth);
        window.addEventListener("resize", onResize);
        return () => window.removeEventListener("resize", onResize);
    }, []);
    const reservedRight =
        configAssistantMode === "config_assistant" && configAssistantDrawerOpen
            ? configAssistantDrawerMaximized
                ? drawerMaximizedWidthPx(vw)
                : drawerDefaultWidthPx(vw)
            : 0;

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

    }, [currentTurn, lastCanvasItem, overrideCanvasItemLink]);

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
                    className="flex flex-col h-full w-full min-h-0 min-w-0 bg-slate-100 overflow-hidden"
                    style={{
                        paddingRight: reservedRight,
                        transition: "padding-right 300ms ease-out",
                    }}>
            <ChatHeader/>

            <div className={`flex flex-row overflow-hidden flex-1 w-full min-h-0 min-w-0`}>
                <ChatSidePanel/>
                <div className={`flex-1 flex flex-col h-full`}>
                    <ConversationHeader/>
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
            {/* Slides in from the right edge when the user enables the
                Configuration Assistant in settings and the agent calls
                code_graph.* tools. Self-hides otherwise. */}
            <InspectDrawer/>
        </div>
    }, [canvasItemLink, chatCanvasContextValue, reservedRight])
};

export default SingleChatApp;
