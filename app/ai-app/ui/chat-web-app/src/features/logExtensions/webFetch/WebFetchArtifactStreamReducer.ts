import {ArtifactStreamDataItem, ArtifactStreamParser} from "../../conversations/conversationsTypes.ts";
import {UnknownArtifact} from "../../chat/chatTypes.ts";
import {WebFetchArtifact, WebFetchArtifactType, WebFetchSubsystemEventDataSubtype} from "./types.ts";

export class WebFetchArtifactStreamReducer implements ArtifactStreamParser {
    private artifacts: WebFetchArtifact[] = []

    process(artifactData: ArtifactStreamDataItem) {
        if (artifactData.marker !== "subsystem") return false;
        let processed = false;
        if (artifactData?.extra?.sub_type === WebFetchSubsystemEventDataSubtype) {
            processed = true;
            const artifact: WebFetchArtifact = {
                artifactType: WebFetchArtifactType,
                timestamp: artifactData.ts_first,
                content: {
                    name: artifactData.artifact_name,
                    executionId:artifactData.extra.execution_id as string,
                    title: artifactData.title,
                    items: JSON.parse(artifactData.text).urls
                }
            }
            this.artifacts.push(artifact)
        }
        return processed;
    }

    flush(): UnknownArtifact[] {
        const r = this.artifacts
        this.artifacts = []
        return r
    }

}