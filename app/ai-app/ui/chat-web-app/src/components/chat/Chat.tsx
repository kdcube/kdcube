/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

// Chat.tsx
import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";

import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {selectChatStayConnected} from "../../features/chat/chatStateSlice.ts";
import ChatSidePanel from "../../features/chatSidePanel/ChatSidePanel.tsx";
import ChatHeader from "../../features/header/ChatHeader.tsx";
import AppScene from "../../features/bundles/AppScene.tsx";
import useSharedConfigProvider from "../../features/sharedConfigProvider/sharedConfigProvider.tsx";
import {selectCurrentBundle} from "../../features/bundles/bundlesSlice.ts";
import {selectProject, selectTenant} from "../../features/chat/chatSettingsSlice.ts";
import {connectChat} from "../../features/chat/chatServiceMiddleware.ts";
import SidePanelContext, {SidePanel, SidePanelContextValue} from "../../features/chatSidePanel/sidePanelContext.ts";

// The app surface: an app with its own main view gets that view; every other
// app gets the automatic scene of its widgets. An app that declares the
// default chat surface (surfaces.as_provider.bundle.default_chat) serves the
// SDK chat widget, which shows up on the scene like any other widget.

const SingleChatApp: React.FC = () => {
    const bundleIframeRef = useRef<HTMLIFrameElement>(null);
    const [bundleUiAvailable, setBundleUiAvailable] = useState<boolean | null>(null);

    const tenant = useAppSelector(selectTenant)
    const project = useAppSelector(selectProject)
    const bundleId = useAppSelector(selectCurrentBundle);

    const [sidePanelId, setSidePanelId] = useState<SidePanel>(null);
    const sidePanelContextValue = useMemo((): SidePanelContextValue => {
        return {
            panelId: sidePanelId,
            setPanelId: setSidePanelId,
        }
    }, [sidePanelId])

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

    const appSurface = useMemo(() => {
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
        return <AppScene/>
    }, [bundleId, bundleUIUrl, bundleUiAvailable, handleBundleIframeLoad])

    const dispatch = useAppDispatch();
    const stayConnected = useAppSelector(selectChatStayConnected)

    useEffect(() => {
        if (!stayConnected)
            dispatch(connectChat())
    }, [dispatch, stayConnected]);


    return useMemo(() => {
        return <div id={SingleChatApp.name}
                    className="flex flex-col h-full w-full min-h-0 min-w-0 bg-slate-100 overflow-hidden">
            <SidePanelContext value={sidePanelContextValue}>
                <ChatHeader/>

                <div className={`flex flex-row overflow-hidden flex-1 w-full min-h-0 min-w-0`}>
                    <ChatSidePanel/>
                    {appSurface}
                </div>
            </SidePanelContext>
        </div>
    }, [appSurface, sidePanelContextValue])
};

export default SingleChatApp;
