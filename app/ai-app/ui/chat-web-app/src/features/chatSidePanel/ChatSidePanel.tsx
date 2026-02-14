import {ReactNode, useCallback, useEffect, useMemo, useRef, useState} from "react";
import {
    ArrowLeftRight,
    Bot,
    CircleDollarSign,
    CirclePlus,
    Database,
    LoaderCircle,
    MessageSquareMore,
    MessagesSquare,
    Search
} from "lucide-react";
import IconContainer from "../../components/IconContainer.tsx";
import AnimatedExpander from "../../components/AnimatedExpander.tsx";
import {motion} from "motion/react";
import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {
    selectConversationDescriptors,
    selectConversationDescriptorsLoading,
    selectConversationDescriptorsLoadingError
} from "../conversations/conversationsSlice.ts";
import {formatDateToLocalString} from "../../utils/dateTimeUtils.ts";
import {ConversationDescriptor} from "../conversations/conversationsTypes.ts";
import {timeSortPredicate} from "../../utils/utils.ts";
import {useNavigate} from "react-router-dom";
import {loadConversationList} from "../conversations/conversationsMiddleware.ts";
import {newConversation} from "../chat/chatStateSlice.ts";
import {
    GetWidgetParams,
    useLazyGetAIBundlesWidgetQuery,
    useLazyGetConversationBrowserWidgetQuery,
    useLazyGetEconomicsWidgetQuery,
    useLazyGetGatewayWidgetQuery,
    useLazyGetRedisBrowserWidgetQuery
} from "../widgetPanels/widgetPanels.ts";
import {selectProject, selectTenant} from "../chat/chatSettingsSlice.ts";
import {getChatPagePath} from "../chat/configHelper.ts";

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

interface ConversationMenuItemProps {
    conversation: ConversationDescriptor;
}

const ConversationMenuItem = ({conversation}: ConversationMenuItemProps) => {
    const navigate = useNavigate();

    return useMemo(() => {
        const href = getChatPagePath() + "/" + conversation.id;
        return <div className={"mx-2.5 mt-2 px-2 py-1 text-md hover:bg-gray-100 rounded-md cursor-pointer"}>
            <a
                href={href}
                onClick={e => {
                    e.preventDefault();
                    e.stopPropagation();
                    navigate(href)
                }}
            >
                <p>{conversation.title ?? "Unnamed conversation"}</p>
                {conversation.lastActivity && (<p className={"text-sm"}>
                    {formatDateToLocalString(new Date(conversation.lastActivity))}
                </p>)}
            </a>
        </div>
    }, [conversation, navigate]);
}

interface ConversationsPanelProps {
    visible: boolean;
    className?: string;
}

const ConversationsPanel = ({visible, className}: ConversationsPanelProps) => {
    const dispatch = useAppDispatch();
    const conversations = useAppSelector(selectConversationDescriptors);

    const processedConversations = useMemo(() => {
        return conversations ? conversations.concat().sort((a, b) => timeSortPredicate(a.lastActivity, b.lastActivity)).reverse() : null;

    }, [conversations]);

    const conversationsLoading = useAppSelector(selectConversationDescriptorsLoading);
    const conversationsLoadingError = useAppSelector(selectConversationDescriptorsLoadingError);

    const [searchFor, setSearchFor] = useState("");

    useEffect(() => {
        dispatch(loadConversationList())
    }, [dispatch]);

    return useMemo(() => {
        return <motion.div
            className={className}
            initial={{
                opacity: visible ? 0 : 1,
            }}
            animate={{
                opacity: visible ? 1 : 0,
            }}
        >
            <div className={"w-full h-full flex flex-col"}>
                <h1 className={"text-xl mx-auto mt-2 ml-2.5"}>Conversations</h1>
                {conversationsLoading && !conversations && (
                    <div>loading</div>
                )}
                {conversationsLoadingError && (
                    <div>error</div>
                )}
                {processedConversations && processedConversations.length > 0 && (
                    <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
                        <div
                            className="p-1.5 mx-2 border bg-white border-gray-200 transition-all rounded-md flex flex-row items-center focus-within:border-gray-800">
                            <IconContainer icon={Search} size={1}/>
                            <input name={"convSearch"} type={"text"}
                                   className={"flex-1 ml-2 focus:outline-none"}
                                   placeholder={"Search"} value={searchFor}
                                   onChange={e => setSearchFor(e.target.value)}/>
                        </div>
                        <div className={"flex-1 min-h-0 overflow-y-auto mr-2 mt-1 mb-2"}>
                            {processedConversations.map((conversation) => (
                                <ConversationMenuItem conversation={conversation} key={conversation.id}/>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </motion.div>
    }, [className, visible, conversationsLoading, conversations, conversationsLoadingError, processedConversations, searchFor])
}

interface IFrameSrcDocPanelProps {
    visible: boolean;
    srcDoc: string;
    className?: string;
}

const IFrameSrcDocPanel = ({visible, srcDoc, className}: IFrameSrcDocPanelProps) => {
    return useMemo(() => {
        return <motion.div
            className={className}
            style={{
                pointerEvents: visible ? "auto" : 'none',
            }}
            initial={{
                opacity: visible ? 0 : 1,
            }}
            animate={{
                opacity: visible ? 1 : 0,
            }}
        >
            <div className={"w-full h-full flex flex-col"}>
                <iframe
                    srcDoc={srcDoc}
                    className={"w-full h-full border-0"}
                />
            </div>
        </motion.div>
    }, [className, srcDoc, visible])
}

interface PanelLoadingProps {
    className?: string;
}

const PanelLoading = ({className}: PanelLoadingProps) => {
    return useMemo(() => {
        return <div className={className}>
            <div className={"w-full h-full flex text-gray-200"}>
                <IconContainer icon={LoaderCircle} className={"animate-spin duration-200"} containerClassName={"m-auto"}
                               size={4}/>
            </div>
        </div>
    }, [className])
}

interface PanelLoadingProps {
    className?: string;
}

const PanelLoadingError = ({className}: PanelLoadingProps) => {
    return useMemo(() => {
        return <div className={className}>
            <div className={"w-full h-full flex text-gray-200"}>
                <div>Sorry, an error has occurred</div>
            </div>
        </div>
    }, [className])
}

interface GenericWidgetPanelProps {
    visible: boolean;
    className?: string;
    trigger: (params: GetWidgetParams, preferCache?: boolean) => void;
    lastArg: {
        data?: string | undefined;
        isFetching: boolean;
        isError: boolean;
        isUninitialized: boolean;
    }
}

const GenericPanel = ({visible, className, trigger, lastArg}: GenericWidgetPanelProps) => {
    const {data, isFetching, isError, isUninitialized} = useMemo(() => {
        return lastArg
    }, [lastArg]);

    const tenant = useAppSelector(selectTenant);
    const project = useAppSelector(selectProject);

    useEffect(() => {
        if (visible && isUninitialized) trigger({tenant, project}, true)
    }, [isUninitialized, project, tenant, trigger, visible]);

    return useMemo(() => {
        if (isFetching) {
            return <PanelLoading className={className}/>
        }
        if (isError) {
            return <PanelLoadingError className={className}/>
        }
        return <IFrameSrcDocPanel visible={visible} className={className} srcDoc={data as string}/>
    }, [className, data, isError, isFetching, visible])
}

interface WidgetPanelProps {
    visible: boolean;
    className?: string;
}

const EconomicsPanel = ({visible, className}: WidgetPanelProps) => {
    const [trigger, lastArg] = useLazyGetEconomicsWidgetQuery();

    return useMemo(() => {
        return <GenericPanel trigger={trigger} lastArg={lastArg} visible={visible} className={className}/>
    }, [trigger, lastArg, visible, className]);
}

const AIBundlesPanel = ({visible, className}: WidgetPanelProps) => {
    const [trigger, lastArg] = useLazyGetAIBundlesWidgetQuery();

    return useMemo(() => {
        return <GenericPanel trigger={trigger} lastArg={lastArg} visible={visible} className={className}/>
    }, [trigger, lastArg, visible, className]);
}

const GatewayPanel = ({visible, className}: WidgetPanelProps) => {
    const [trigger, lastArg] = useLazyGetGatewayWidgetQuery();

    return useMemo(() => {
        return <GenericPanel trigger={trigger} lastArg={lastArg} visible={visible} className={className}/>
    }, [trigger, lastArg, visible, className]);
}

const ConvBrowserPanel = ({visible, className}: WidgetPanelProps) => {
    const [trigger, lastArg] = useLazyGetConversationBrowserWidgetQuery();

    return useMemo(() => {
        return <GenericPanel trigger={trigger} lastArg={lastArg} visible={visible} className={className}/>
    }, [trigger, lastArg, visible, className]);
}

const RedisBrowserPanel = ({visible, className}: WidgetPanelProps) => {
    const [trigger, lastArg] = useLazyGetRedisBrowserWidgetQuery();

    return useMemo(() => {
        return <GenericPanel trigger={trigger} lastArg={lastArg} visible={visible} className={className}/>
    }, [trigger, lastArg, visible, className]);
}

type Panels = "conversations" | "economics" | "ai_bundles" | "gateway" | "conv_browser" | "redis_browser" | null

const ChatSidePanel = () => {
    const dispatch = useAppDispatch();

    const parentRef = useRef<HTMLDivElement>(null);
    const [visiblePanel, setVisiblePanel] = useState<Panels>(null);
    const sidePanelContentRef = useRef<HTMLDivElement>(null);

    const onPanelButtonClick = useCallback((panel: Panels) => {
        if (panel === null || panel !== visiblePanel) {
            setVisiblePanel(panel)
        } else {
            setVisiblePanel(null)
        }
    }, [visiblePanel])

    return useMemo(() => {
        let panelWidth = 800
        switch (visiblePanel) {
            case "conversations":
                panelWidth = 500
                break
        }

        return <div
            ref={parentRef}
            className={"flex flex-row h-full overflow-visible min-h-0 min-w-0"}>
            <div
                className={"h-full flex flex-col items-center bg-gray-50 border-r border-gray-200 pt-1 px-1 text-gray-700 gap-1"}>
                <MenuButton onClick={() => dispatch(newConversation())}>
                    <IconContainer icon={CirclePlus} size={1.5}/>
                </MenuButton>
                <MenuButton
                    onClick={() => {
                        onPanelButtonClick("conversations");
                    }}
                >
                    <IconContainer icon={MessagesSquare} size={1.5}/>
                </MenuButton>
                <MenuButton
                    onClick={() => {
                        onPanelButtonClick("economics");
                    }}
                >
                    <IconContainer icon={CircleDollarSign} size={1.5}/>
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
                    <IconContainer icon={Database} size={1.5}/>
                </MenuButton>
            </div>
            {/*<div className={"absolute h-full top-0 left-12 z-20 shadow-md border-r border-gray-200 bg-white"}>*/}
            <div className={"h-full border-r border-gray-200 bg-white relative"}>
                <AnimatedExpander contentRef={sidePanelContentRef} className={"h-full"}
                                  expanded={visiblePanel !== null}>
                    <div className={"h-full"} ref={sidePanelContentRef} style={{width: `${panelWidth}px`}}>
                        <ConversationsPanel visible={visiblePanel === "conversations"}
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
                    </div>
                </AnimatedExpander>
            </div>
        </div>
    }, [dispatch, onPanelButtonClick, visiblePanel])
}

export default ChatSidePanel;