import {WebSearchArtifact, WebSearchArtifactType} from "./types.ts";
import {useMemo} from "react";
import {ArtifactComponentProps} from "../../extensions/canvasExtensions.ts";

const WebSearchCanvasItem = ({item}: ArtifactComponentProps) => {
    if (item.artifactType !== WebSearchArtifactType) {
        throw new Error("not a CodeExecArtifactType")
    }

    const webSearchItem = item as WebSearchArtifact;

    return useMemo(() => {
        if (!webSearchItem.content.reportContent) return null;
        return <div className={"w-full h-full overflow-hidden"}>
            <iframe
                srcDoc={webSearchItem.content.reportContent}
                className={"w-full h-full border-0"}
            />
        </div>
    }, [webSearchItem.content.reportContent])
}

export default WebSearchCanvasItem;