import {useMemo} from "react";
import ReactMarkdown from "react-markdown";
import {
    markdownComponentsTight,
    rehypePlugins,
    remarkPlugins
} from "../../../components/chat/ChatInterface/markdownRenderUtils.tsx";
import {appendCodeMarkdown, cleanupCode} from "../../canvas/utils.ts";
import {CodeExecArtifact, CodeExecArtifactType} from "./types.ts";
import {ArtifactComponentProps} from "../../extensions/canvasExtensions.ts";

const CodeExecCanvasItem = ({item}: ArtifactComponentProps) => {
    if (item.artifactType !== CodeExecArtifactType) {
        throw new Error("not a CodeExecArtifactType")
    }

    const codeExecItem = item as CodeExecArtifact;

    return useMemo(() => {
        return <div className={"p-2 border-gray-200 border-l-1 bg-white h-full w-full overflow-y-auto"}>
            <ReactMarkdown
                remarkPlugins={remarkPlugins}
                rehypePlugins={rehypePlugins}
                components={markdownComponentsTight}
                skipHtml={false}
            >
                {appendCodeMarkdown(cleanupCode(codeExecItem.content!.program!.content), codeExecItem.content!.program!.language)}
            </ReactMarkdown>
        </div>
    }, [codeExecItem])
}

export default CodeExecCanvasItem;