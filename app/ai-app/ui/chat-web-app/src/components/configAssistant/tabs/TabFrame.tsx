/*
 * Shared frame for inspect-panel tabs: title bar + content area.
 */
import {ReactNode} from "react";

interface Props {
    title: string;
    subtitle?: string;
    children: ReactNode;
    actions?: ReactNode;
}

export function TabFrame({title, subtitle, children, actions}: Props) {
    return (
        <div className="flex flex-col h-full">
            <div className="flex flex-row items-center justify-between gap-2 px-4 py-3 border-b border-slate-200">
                <div className="flex flex-col min-w-0">
                    <div className="text-sm font-semibold text-slate-900 truncate">{title}</div>
                    {subtitle && (
                        <div className="text-xs text-slate-500 truncate">{subtitle}</div>
                    )}
                </div>
                {actions}
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">{children}</div>
        </div>
    );
}

interface EmptyProps {
    children: ReactNode;
}

export function TabEmpty({children}: EmptyProps) {
    return (
        <div className="flex flex-col items-center justify-center h-full text-center text-slate-500 text-sm py-8">
            <div className="max-w-xs">{children}</div>
        </div>
    );
}
