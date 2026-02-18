import {UnknownArtifact} from "../../chat/chatTypes.ts";
import {ArtifactStreamDataItem, ArtifactStreamParser} from "../../conversations/conversationsTypes.ts";

export class IgnoredArtifactStreamReducer implements ArtifactStreamParser {
    process(artifactData: ArtifactStreamDataItem) {
        const ignored = artifactData.marker === "subsystem" && artifactData?.extra?.sub_type === "conversation.turn.status" ||
            artifactData.marker === "tool" && ["web_search.filtered_results", "web_search.html_view"].includes(artifactData?.extra?.sub_type as string)
        console.debug("ignored", artifactData);
        return ignored;
    };

    flush(): UnknownArtifact[] {
        return [];
    }
}