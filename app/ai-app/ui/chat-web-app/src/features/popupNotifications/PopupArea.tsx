import {useMemo, useRef} from "react";
import {dismissNotification, selectPopupNotifications} from "./popupsSlice.ts";
import {X} from "lucide-react";
import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {AppNotification} from "./types.ts";
import AnimatedExpander from "../../components/AnimatedExpander.tsx";


const notificationColors: Record<string, string> = {
    error: "bg-red-50 border-red-200 text-red-800",
    warning: "bg-yellow-50 border-yellow-200 text-yellow-800",
    info: "bg-blue-50 border-blue-200 text-blue-800",
};

interface PopupWrapperProps {
    notification: AppNotification;
}

const PopupWrapper = ({notification}: PopupWrapperProps) => {
    const dispatch = useAppDispatch();

    const contentRef = useRef<HTMLDivElement>(null);

    return useMemo(() => {
        return <AnimatedExpander contentRef={contentRef} expanded={true} direction={"vertical"}>
            <div
                ref={contentRef}
                className={`flex items-center gap-2 px-4 py-3 mb-2 text-sm rounded-xl border pointer-events-auto ${notificationColors[notification.type] ?? notificationColors.info}`}
            >
                <span className="flex-1">{notification.text}</span>
                <button
                    className="shrink-0 opacity-60 hover:opacity-100 cursor-pointer"
                    onClick={() => dispatch(dismissNotification(notification.id))}
                    aria-label="Dismiss"
                >
                    <X size={14}/>
                </button>
            </div>
        </AnimatedExpander>
    }, [dispatch, notification.id, notification.text, notification.type])
}

export interface PopupAreaProps {
    className?: string
}

const PopupArea = ({className}: PopupAreaProps) => {
    const notifications = useAppSelector(selectPopupNotifications);

    return useMemo(() => {
        return <div id={PopupArea.name} className={className}>
            <div className={"flex flex-col gap-1 w-full"}>
                {notifications.map((notification) => (
                    <PopupWrapper key={notification.id} notification={notification}/>
                ))}
            </div>
        </div>
    }, [className, notifications])
}

export default PopupArea