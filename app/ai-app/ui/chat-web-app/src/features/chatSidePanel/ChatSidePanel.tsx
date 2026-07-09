import {ReactNode, useCallback, useEffect, useMemo, useRef, useState} from "react";
import {
    ArrowLeftRight,
    Bot,
    Bug,
    CircleDollarSign,
    CreditCard,
    Database,
    DatabaseZap,
    MessageSquareMore,
    PlugZap,
} from "lucide-react";
import IconContainer from "../../components/IconContainer.tsx";
import AnimatedExpander from "../../components/AnimatedExpander.tsx";
import {useAppSelector} from "../../app/store.ts";
import {showDebugControls} from "../../BuildConfig.ts";
import DebugPanel from "../debugPanel/DebugPanel.tsx";
import ResizableContainer from "../../components/ResizableContainer.tsx";
import {readParam, writeParam} from "../settingsStorage/settingsStorage.ts";
import {selectAppUser} from "../auth/authSlice.ts";
import {
    AIBundlesPanel,
    BundleWidgetPanel,
    ConvBrowserPanel,
    EconomicsPanel,
    EconomicUsagePanel,
    GatewayPanel,
    RedisBrowserPanel
} from "./GenericPanels.tsx";
import {ArtifactsPanel} from "./ArtifactsPanel.tsx";
import {SidePanel, useSidePanelContext} from "./sidePanelContext.ts";
import {useGetBundleWidgets} from "../bundles/widgetReducer.tsx";
import {selectMainViewActive} from "../bundles/bundlesSlice.ts";
import {getBundleWidgetPanelId} from "../bundles/utils.ts";
import {SERVICES_APP_ID} from "../widgetPanels/widgetPanels.ts";

// The side rail is the quick access for the platform control surfaces:
// besides the services app's operation panels it pins served widgets from
// the always-running control apps.
const STORAGE_WIDGET_ALIAS = "bundle_storage";

const CONNECTION_HUB_APP_ID = "connection-hub@1-0";
const CONNECTIONS_WIDGET_ALIAS = "connections_settings";

interface MenuButtonProps {
    children: ReactNode | ReactNode[];
    onClick?: () => void;
}

const MenuButton = ({children, onClick}: MenuButtonProps) => {
    return useMemo(() => {
        return <button
            onClick={e => {
                e.preventDefault();
                e.stopPropagation();
                onClick?.()
            }}
            className={"hover:bg-gray-200 transition-all duration-200 p-1 rounded-md cursor-pointer"}>
            {children}
        </button>
    }, [children, onClick])
}

export interface WidgetPanelProps {
    visible: boolean;
    className?: string;
}

const ChatSidePanel = () => {
    const parentRef = useRef<HTMLDivElement>(null);
    const sidePanelContentRef = useRef<HTMLDivElement>(null);

    const sidePanelContext = useSidePanelContext()

    const {currentBundleId, widgets} = useGetBundleWidgets()
    const mainViewActive = useAppSelector(selectMainViewActive)

    const visiblePanel = useMemo(() => {
        return sidePanelContext.panelId
    }, [sidePanelContext.panelId])

    const setPanelId = useMemo(() => {
        return sidePanelContext.setPanelId
    }, [sidePanelContext.setPanelId])

    const onPanelButtonClick = useCallback((panel: SidePanel) => {
        if (panel === null || panel !== visiblePanel) {
            setPanelId(panel)
        } else {
            setPanelId(null)
        }
    }, [setPanelId, visiblePanel])

    const user = useAppSelector(selectAppUser)

    const onPanelResize = useCallback((size: number) => {
        writeParam("sidePanelWidth", size, user ? user.username : null);
    }, [user])

    const bundlePanels = useMemo(() => {
        // An app showing its own main view keeps its widgets reachable as
        // side panels (summoned by the header chips). The automatic scene
        // carries its own widget chips, so scene apps skip these.
        if (!currentBundleId || !mainViewActive) {
            return null
        }
        return widgets.map((widget) => {
            const widgetPanelId = getBundleWidgetPanelId(currentBundleId, widget.alias)
            return <BundleWidgetPanel key={widgetPanelId}
                                      visible={visiblePanel === widgetPanelId}
                                      bundleId={currentBundleId}
                                      widgetAlias={widget.alias}
                                      className={"w-full h-full absolute left-0 top-0"}/>
        })
    }, [currentBundleId, mainViewActive, visiblePanel, widgets])

    const [maxWidth, setMaxWidth] = useState<number>(window.innerWidth / 2)

    useEffect(() => {
        const observer = new ResizeObserver((entries) => {
            entries.forEach(e => setMaxWidth(e.contentRect.width /2))
        })
        observer.observe(document.body)
        return () => {
            observer.disconnect()
        }
    }, []);

    return useMemo(() => {
        const initialPanelWidth = readParam("sidePanelWidth", 400) as number

        return <div
            ref={parentRef}
            className={"flex flex-row h-full overflow-visible min-h-0 min-w-0"}>
            <div
                className={"h-full flex flex-col items-center bg-gray-50 border-r border-gray-200 pt-1 px-1 text-gray-700 gap-1"}>
                {/*<MenuButton*/}
                {/*    onClick={() => {*/}
                {/*        onPanelButtonClick("artifacts");*/}
                {/*    }}*/}
                {/*>*/}
                {/*    <IconContainer icon={Package} size={1.5}/>*/}
                {/*</MenuButton>*/}
                <MenuButton
                    onClick={() => {
                        onPanelButtonClick("economics");
                    }}
                >
                    <IconContainer icon={CircleDollarSign} size={1.5}/>
                </MenuButton>
                <MenuButton
                    onClick={() => {
                        onPanelButtonClick("economic_usage");
                    }}
                >
                    <IconContainer icon={CreditCard} size={1.5}/>
                </MenuButton>
                <MenuButton
                    onClick={() => {
                        onPanelButtonClick("ai_bundles");
                    }}
                >
                    <IconContainer icon={Bot} size={1.5}/>
                </MenuButton>
                <MenuButton
                    onClick={() => {
                        onPanelButtonClick("gateway");
                    }}
                >
                    <IconContainer icon={ArrowLeftRight} size={1.5}/>
                </MenuButton>
                <MenuButton
                    onClick={() => {
                        onPanelButtonClick("conv_browser");
                    }}
                >
                    <IconContainer icon={MessageSquareMore} size={1.5}/>
                </MenuButton>
                <MenuButton
                    onClick={() => {
                        onPanelButtonClick("redis_browser");
                    }}
                >
                    <IconContainer icon={DatabaseZap} size={1.5}/>
                </MenuButton>
                <MenuButton
                    onClick={() => {
                        onPanelButtonClick("storage");
                    }}
                >
                    <IconContainer icon={Database} size={1.5}/>
                </MenuButton>
                <MenuButton
                    onClick={() => {
                        onPanelButtonClick("connections");
                    }}
                >
                    <IconContainer icon={PlugZap} size={1.5}/>
                </MenuButton>
                {showDebugControls && <MenuButton
                    onClick={() => {
                        onPanelButtonClick("debug");
                    }}
                >
                    <IconContainer icon={Bug} size={1.5}/>
                </MenuButton>}
            </div>
            {/*<div className={"absolute h-full top-0 left-12 z-20 shadow-md border-r border-gray-200 bg-white"}>*/}
            <div
                className={`h-full border-r border-gray-200 bg-white relative ${visiblePanel ? "pointer-events-auto" : "pointer-events-none"}`}>
                <AnimatedExpander contentRef={sidePanelContentRef} className={"h-full"}
                                  expanded={visiblePanel !== null}>
                    {/*<div className={"h-full"} ref={sidePanelContentRef} style={{width: `${panelWidth}px`}}>*/}
                    <ResizableContainer ref={sidePanelContentRef} onResize={onPanelResize}
                                        initialSize={initialPanelWidth} minSize={300} maxSize={maxWidth} className={"h-full"}>
                        <ArtifactsPanel visible={visiblePanel === "artifacts"}
                                        className={"w-full h-full absolute left-0 top-0"}/>
                        <EconomicsPanel visible={visiblePanel === "economics"}
                                        className={"w-full h-full absolute left-0 top-0"}/>
                        <AIBundlesPanel visible={visiblePanel === "ai_bundles"}
                                        className={"w-full h-full absolute left-0 top-0"}/>
                        <GatewayPanel visible={visiblePanel === "gateway"}
                                      className={"w-full h-full absolute left-0 top-0"}/>
                        <ConvBrowserPanel visible={visiblePanel === "conv_browser"}
                                          className={"w-full h-full absolute left-0 top-0"}/>
                        <RedisBrowserPanel visible={visiblePanel === "redis_browser"}
                                           className={"w-full h-full absolute left-0 top-0"}/>
                        <EconomicUsagePanel visible={visiblePanel === "economic_usage"}
                                            className={"w-full h-full absolute left-0 top-0"}/>
                        <BundleWidgetPanel visible={visiblePanel === "storage"}
                                           bundleId={SERVICES_APP_ID}
                                           widgetAlias={STORAGE_WIDGET_ALIAS}
                                           className={"w-full h-full absolute left-0 top-0"}/>
                        <BundleWidgetPanel visible={visiblePanel === "connections"}
                                           bundleId={CONNECTION_HUB_APP_ID}
                                           widgetAlias={CONNECTIONS_WIDGET_ALIAS}
                                           className={"w-full h-full absolute left-0 top-0"}/>
                        {bundlePanels}
                        {showDebugControls && <DebugPanel visible={visiblePanel === "debug"}
                                                          className={"w-full h-full absolute left-0 top-0"}/>}
                    </ResizableContainer>
                    {/*</div>*/}
                </AnimatedExpander>
            </div>
        </div>
    }, [bundlePanels, maxWidth, onPanelButtonClick, onPanelResize, visiblePanel])
}

export default ChatSidePanel;
