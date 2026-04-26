import {ReactNode} from "react";

interface Props {
    title: string;
    children: ReactNode;
}

/** Small labelled section used by Class/Concept/Policy details. */
export function Section({title, children}: Props) {
    return (
        <div className="mb-3">
            <div className="text-[10px] font-semibold text-slate-600 uppercase tracking-wide mb-1">
                {title}
            </div>
            <div className="text-xs text-slate-700 leading-relaxed">{children}</div>
        </div>
    );
}
