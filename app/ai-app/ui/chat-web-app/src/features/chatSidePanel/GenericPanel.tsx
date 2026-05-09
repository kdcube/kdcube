import {ReactNode, useCallback, useEffect, useMemo, useRef, useState} from "react";
import {useAppSelector} from "../../app/store.ts";
import {selectProject, selectTenant} from "../chat/chatSettingsSlice.ts";
import IconContainer from "../../components/IconContainer.tsx";
import {LoaderCircle, Maximize2, Minimize2, RotateCcw, X} from "lucide-react";
import {motion} from "motion/react";
import {useSidePanelContext} from "./sidePanelContext.ts";

interface IFrameSrcDocPanelProps {
    srcDoc: string;
}

const IFrameSrcDocPanel = ({srcDoc}: IFrameSrcDocPanelProps) => {
    return useMemo(() => {
        return <div className={"w-full h-full flex flex-col"}>
            <iframe
                srcDoc={srcDoc}
                className={"w-full h-full border-0"}
            />
        </div>
    }, [srcDoc])
}

const PanelLoading = () => {
    return useMemo(() => {
        return <div className={"w-full h-full flex text-gray-200"}>
            <IconContainer icon={LoaderCircle} className={"animate-spin duration-200"} containerClassName={"m-auto"}
                           size={4}/>
        </div>
    }, [])
}

const PanelLoadingError = () => {
    return useMemo(() => {
        return <div className={"w-full h-full flex text-gray-600 p-2"}>
            <div>Sorry, an error has occurred</div>
        </div>
    }, [])
}

interface PanelContainerProps {
    children?: ReactNode | ReactNode[]
    visible?: boolean
    className?: string
    reload?: () => void
}

const PanelContainer = ({children, className, reload, visible = true}: PanelContainerProps) => {
    const [fullScreen, setFullScreen] = useState(false);

    const sidePanelContext = useSidePanelContext()

    const close = useCallback(() => {
        sidePanelContext.setPanelId(null)
    }, [sidePanelContext])

    useEffect(() => {
        if (!visible) setFullScreen(false)
    }, [visible]);

    return useMemo(() => {
        return <motion.div
            className={fullScreen ? "w-screen h-screen fixed z-40 top-0 left-0" : className}
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
            <div className={"flex flex-col w-full h-full"}>
                <div className={"flex flex-row gap-0.5 p-1 border-b border-gray-200 bg-white"}>
                    <div className={"ml-auto"}/>
                    {reload &&
                        <button
                            className={"cursor-pointer text-gray-600 hover:text-gray-800"}
                            onClick={() => reload()}
                        >
                            <IconContainer icon={RotateCcw} size={1.2}/>
                        </button>
                    }
                    {fullScreen ?
                        <button
                            className={"cursor-pointer text-gray-600 hover:text-gray-800"}
                            onClick={() => setFullScreen(false)}
                        >
                            <IconContainer icon={Minimize2} size={1.2}/></button>
                        :
                        <button
                            className={"cursor-pointer text-gray-600 hover:text-gray-800"}
                            onClick={() => setFullScreen(true)}
                        >
                            <IconContainer icon={Maximize2} size={1.2}/></button>
                    }
                    <button
                        className={"cursor-pointer text-gray-600 hover:text-gray-800 -m-1"}
                        onClick={() => close()}
                    >
                        <IconContainer icon={X} size={1.5}/>
                    </button>
                </div>
                <div className={"w-full flex-1 relative"}>
                    {children}
                </div>
            </div>
        </motion.div>
    }, [children, className, close, fullScreen, reload, visible])
}

interface GenericWidgetPanelProps {
    visible: boolean;
    className?: string;
    trigger: (params: unknown, preferCache?: boolean) => void;
    useCached?: boolean;
    params?: Record<string, string>;
    lastArg: {
        data?: string | undefined;
        isFetching: boolean;
        isError: boolean;
        isUninitialized: boolean;
    };
}

export const GenericPanel = ({
                                 visible,
                                 className,
                                 trigger,
                                 lastArg,
                                 useCached = false,
                                 params
                             }: GenericWidgetPanelProps) => {
    const wasVisible = useRef(visible);

    const {data, isFetching, isError, isUninitialized} = useMemo(() => {
        return lastArg
    }, [lastArg]);

    const tenant = useAppSelector(selectTenant);
    const project = useAppSelector(selectProject);
    const resolvedParams = useMemo(() => {
        return {
            tenant,
            project,
            ...(params || {}),
        };
    }, [params, project, tenant]);

    useEffect(() => {
        if (!wasVisible.current && !useCached && visible) {
            trigger(resolvedParams, false);
        } else if (visible && isUninitialized) {
            trigger(resolvedParams, true)
        }
        wasVisible.current = visible;
    }, [isUninitialized, resolvedParams, trigger, useCached, visible]);

    const hardReload = useCallback(() => {
        if (isFetching) return
        trigger(resolvedParams, false);
    }, [isFetching, resolvedParams, trigger])

    const content = useMemo(() => {
        if (visible) {
            if (isFetching) {
                return <PanelLoading/>
            }
            if (isError) {
                return <PanelLoadingError/>
            }
        }

        if (!isFetching && !isError) {
            return <IFrameSrcDocPanel srcDoc={data as string}/>
        }

        return null
    }, [data, isError, isFetching, visible])

    return useMemo(() => {
        return <PanelContainer visible={visible} className={className} reload={hardReload}>
            {content}
        </PanelContainer>
    }, [className, content, hardReload, visible])
}
