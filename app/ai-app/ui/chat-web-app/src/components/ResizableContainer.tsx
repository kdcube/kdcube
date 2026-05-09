import {
    CSSProperties,
    MouseEvent,
    ReactNode,
    RefObject,
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState
} from "react";
import {clamp} from "../utils/utils.ts";

interface ResizableContainerProps {
    className?: string;
    children?: ReactNode | ReactNode[];
    position?: "top" | "bottom" | "left" | "right";
    initialSize?: number;
    minSize?: number;
    maxSize?: number;
    ref?: RefObject<HTMLDivElement | null>
    onResize?: (size: number) => void;
}

const ResizableContainer = ({
                                children,
                                className,
                                ref,
                                onResize,
                                initialSize = 200,
                                minSize = 100,
                                maxSize,
                                position = "right"
                            }: ResizableContainerProps) => {
    const contentContainerRef = useRef<HTMLDivElement | null>(null);

    const [isResizing, setIsResizing] = useState(false);
    const [containerSize, setContainerSize] = useState<number>(maxSize === undefined ? Math.max(initialSize, minSize) : Math.min(Math.max(initialSize, minSize), maxSize));

    useEffect(() => {
        const resize = (e: globalThis.MouseEvent) => {
            if (!contentContainerRef.current) return;
            let newSize = minSize;
            switch (position) {
                case "top":
                    newSize = e.clientY - contentContainerRef.current.getBoundingClientRect().bottom;
                    break;
                case "bottom":
                    newSize = e.clientY - contentContainerRef.current.getBoundingClientRect().top;
                    break;
                case "left":
                    newSize = e.clientX - contentContainerRef.current.getBoundingClientRect().right;
                    break;
                case "right":
                    newSize = e.clientX - contentContainerRef.current.getBoundingClientRect().left;
                    break;
            }
            newSize = clamp(newSize, minSize, maxSize)
            setContainerSize(newSize)
            onResize?.(newSize)
        }

        const stopResize = () => {
            setIsResizing(false);
        }

        if (isResizing) {
            window.addEventListener('mousemove', resize);
            window.addEventListener('mouseup', stopResize);
        }

        return () => {
            window.removeEventListener('mousemove', resize);
            window.removeEventListener('mouseup', stopResize);
        };
    }, [isResizing, maxSize, minSize, onResize, position]);

    useEffect(() => {
        setContainerSize(prev => {
            return clamp(prev, minSize, maxSize)
        })
    }, [maxSize, minSize]);

    const onMouseDown = useCallback((e: MouseEvent) => {
        e.preventDefault();
        setIsResizing(true)
    }, [])

    const onMouseUp = useCallback((e: MouseEvent) => {
        e.preventDefault();
        setIsResizing(true)
    }, [])

    return useMemo(() => {
        const containerStyle: CSSProperties = {}

        if (position === "top" || position === "bottom") {
            containerStyle.height = containerSize + "px";
        } else {
            containerStyle.width = containerSize + "px";
        }

        let resizeLine = null
        switch (position) {
            case "top":
                resizeLine =
                    <div className={"w-full absolute top-0 h-2 cursor-row-resize translate-y-1"}
                         onMouseDown={onMouseDown}
                         onMouseUp={onMouseUp}
                    >&nbsp;</div>;
                break;
            case "bottom":
                resizeLine =
                    <div className={"w-full absolute bottom-0 h-2 cursor-row-resize -translate-y-1"}
                         onMouseDown={onMouseDown}
                         onMouseUp={onMouseUp}
                    >&nbsp;</div>;
                break;
            case "left":
                resizeLine =
                    <div className={"h-full absolute left-0 w-2 cursor-col-resize -translate-x-1"}
                         onMouseDown={onMouseDown}
                         onMouseUp={onMouseUp}
                    >&nbsp;</div>;
                break;
            case "right":
                resizeLine = <div
                    className={"h-full absolute right-0 top-0 w-2 cursor-col-resize translate-x-1 hover:bg-gray-200"}
                    onMouseDown={onMouseDown}
                    onMouseUp={onMouseUp}
                >&nbsp;</div>;
                break;
        }

        return <div className={`${className ?? ""}`} ref={ref} style={containerStyle}>
            <div className={"h-full w-full relative"} ref={contentContainerRef}>
                {children}
                <div id={"blocker"}
                     className={`top-0 left-0 bg-transparent absolute w-full h-full ${isResizing ? "" : "hidden"}`}/>
                {resizeLine}
            </div>
        </div>
    }, [children, className, containerSize, isResizing, onMouseDown, onMouseUp, position, ref])
}

export default ResizableContainer;