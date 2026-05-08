import {Artifact, CompactionEventData} from "../../chat/chatTypes.ts";

export const CompactionArtifactType = "compaction"

export interface CompactionArtifact extends Artifact<CompactionEventData> {
    artifactType: typeof CompactionArtifactType
}
