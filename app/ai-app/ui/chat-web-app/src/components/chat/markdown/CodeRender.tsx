import MermaidDiagram from "../../MermaidDiagram.tsx";
import {ReactNode, useCallback, useMemo, useRef} from "react";
import IconContainer from "../../IconContainer.tsx";
import {ClipboardCopy} from "lucide-react";
import {copyMarkdownToClipboard} from "../../Clipboard.ts";

interface CodeRenderProps {
    className?: string;
    children?: ReactNode | ReactNode[];
}

const CodeRender = ({className, children}: CodeRenderProps) => {
    const contentRef = useRef<HTMLDivElement>(null)

    const copyToClipboard = useCallback(() => {
        copyMarkdownToClipboard(contentRef.current?.innerText, contentRef.current?.innerHTML).catch((err) => {
            console.error("Could not copy message", err);
        })
    }, []);

    return useMemo(() => {
        const languageName = /language-(\w+)/.exec(className || '')?.[1];

        if (languageName === 'mermaid') {
            return <MermaidDiagram chart={String(children).replace(/\n$/, '')}/>
        }

        return <span
            className="hljs p-0 rounded-sm overflow-x-auto overflow-y-auto mt-4 mb-1 last:mb-1 w-full h-full min-w-0 min-h-0 relative">
            {languageName &&
                <span className="px-2 py-1 bg-gray-200 align-middle text-[0.7rem] text-gray-800">
                    {languageName}
                </span>
            }
            {languageName && <button
                onClick={copyToClipboard}
                className={"absolute right-2 top-7 cursor-pointer text-gray-600 hover:text-gray-800 transition-colors duration-200"}>
                <IconContainer icon={ClipboardCopy} size={1}/>
            </button>}
            <code
                className={`-mt-1 text-sm  ${languageName ? "border border-gray-200" : ""} ${className ? className : "hljs"}`}>
                <span className={"w-full h-full min-w-0 min-h-0"} ref={contentRef}>
                    {children}
                </span>
            </code>
    </span>
    }, [children, className, copyToClipboard]);
}

export default CodeRender;