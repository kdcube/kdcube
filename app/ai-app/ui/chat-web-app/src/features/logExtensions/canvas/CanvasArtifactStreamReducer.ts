import {UnknownArtifact} from "../../chat/chatTypes.ts";
import {ArtifactStreamDataItem, ArtifactStreamParser} from "../../conversations/conversationsTypes.ts";
import {CanvasArtifact, CanvasArtifactType} from "./types.ts";

export class CanvasArtifactStreamReducer implements ArtifactStreamParser {
    private artifacts: CanvasArtifact[] = []

    process(artifactData: ArtifactStreamDataItem) {
        if (artifactData.marker !== "canvas") return false;
        const c: CanvasArtifact = {
            artifactType: CanvasArtifactType,
            timestamp: artifactData.ts_first,
            complete: true,
            content: {
                name: artifactData.extra?.artifact_name || artifactData.artifact_name,
                title: artifactData.extra?.title || artifactData.title,
                content: artifactData.text,
                contentType: artifactData.extra?.format || artifactData.format
            }
        }
        this.artifacts.push(c)
        return true
    }

    flush(): UnknownArtifact[] {
        const r = this.artifacts
        this.artifacts = []
        return r
    }
}