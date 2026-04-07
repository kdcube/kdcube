/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

// Chat.tsx
import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";

import ChatInterface from "./ChatInterface/ChatInterface.tsx";
import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {selectChatStayConnected, selectConversationId, selectCurrentTurn,} from "../../features/chat/chatStateSlice.ts";
import ChatSidePanel from "../../features/chatSidePanel/ChatSidePanel.tsx";
import ChatHeader from "./ChatHeader.tsx";
import AnimatedExpander from "../AnimatedExpander.tsx";
import ChatCanvas from "../../features/canvas/ChatCanvas.tsx";
import {CanvasItemLink, ChatCanvasContext, ChatCanvasContextValue} from "../../features/canvas/canvasContext.tsx";
import {getCanvasArtifactTypes, getCanvasItemLinkGenerator} from "../../features/extensions/canvasExtensions.ts";
import useSharedConfigProvider from "../../features/sharedConfigProvider/sharedConfigProvider.tsx";
import ConversationHeader from "../../features/conversationHeader/ConversationHeader.tsx";
import {selectCurrentBundle} from "../../features/bundles/bundlesSlice.ts";
import {selectProject, selectTenant} from "../../features/chat/chatSettingsSlice.ts";
import {connectChat} from "../../features/chat/chatServiceMiddleware.ts";

const SingleChatApp: React.FC = () => {
    const currentTurn = useAppSelector(selectCurrentTurn);
    const conversationId = useAppSelector(selectConversationId);
    const chatCanvasRef = useRef<HTMLDivElement>(null);
    const bundleIframeRef = useRef<HTMLIFrameElement>(null);
    const [canvasItemLink, setCanvasItemLink] = useState<CanvasItemLink | null>(null);
    const [overrideCanvasItemLink, setOverrideCanvasItemLink] = useState<boolean>(false);
    const [bundleUiAvailable, setBundleUiAvailable] = useState<boolean | null>(null);
    const tenant = useAppSelector(selectTenant)
    const project = useAppSelector(selectProject)
    const bundleId = useAppSelector(selectCurrentBundle);

    useSharedConfigProvider()

    const bundleUIUrl = useMemo(() => {
        if (!bundleId || !tenant || !project) {
            return null;
        }
        return `/api/integrations/static/${tenant}/${project}/${bundleId}`;
    }, [bundleId, project, tenant])

    useEffect(() => {
        setBundleUiAvailable(bundleUIUrl ? null : false);
    }, [bundleUIUrl]);

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

    const handleBundleIframeLoad = useCallback(() => {
        const doc = bundleIframeRef.current?.contentDocument;
        if (!doc) {
            setBundleUiAvailable(true);
            return;
        }
        const text = (doc.body?.innerText || "").trim();
        if (text === '{"detail":"Not found"}' || text.includes('"detail":"Not found"') || text.includes('does not have a UI defined"')) {
            setBundleUiAvailable(false);
            return;
        }
        setBundleUiAvailable(true);
    }, [])

    const chatCanvasContextValue = useMemo<ChatCanvasContextValue>(() => {
        return {
            showItem,
            itemLink: canvasItemLink
        }
    }, [canvasItemLink, showItem])

    const chatInterface = useMemo(() => {
        if (bundleUIUrl && bundleUiAvailable !== false) {
            return <div className={"flex-1 flex flex-col h-full"}>
                <iframe
                    ref={bundleIframeRef}
                    src={bundleUIUrl}
                    className={"w-full h-full border-0"}
                    title={`bundle-ui-${bundleId}`}
                    onLoad={handleBundleIframeLoad}
                    style={{visibility: bundleUiAvailable === true ? "visible" : "hidden"}}
                />
            </div>
        }

        return <div className={`flex-1 flex flex-col h-full`}>
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
    }, [bundleId, bundleUIUrl, bundleUiAvailable, canvasItemLink, chatCanvasContextValue, handleBundleIframeLoad])

    const dispatch = useAppDispatch();
    const stayConnected = useAppSelector(selectChatStayConnected)

    useEffect(() => {
        if (!stayConnected)
            dispatch(connectChat())
    }, [dispatch, stayConnected]);


    return useMemo(() => {
        return <div id={SingleChatApp.name}
                    className="flex flex-col h-full w-full min-h-0 min-w-0 bg-slate-100 overflow-hidden">
            <ChatHeader/>

            <div className={`flex flex-row overflow-hidden flex-1 w-full min-h-0 min-w-0`}>
                <ChatSidePanel/>
                {chatInterface}
            </div>
        </div>
    }, [chatInterface])
};

export default SingleChatApp;
