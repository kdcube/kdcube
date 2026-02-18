import {Artifact, SubsystemEventData} from "../../chat/chatTypes.ts";

export interface ConversationStatusArtifactData {
    status: string;
}

export const ConversationStatusArtifactType = "conversation.turn.status";

export interface ConversationStatusArtifact extends Artifact<ConversationStatusArtifactData> {
    artifactType: typeof ConversationStatusArtifactType;
    complete?: boolean;
}

export const ConversationStatusSubsystemEventDataSubtype = "conversation.turn.status"

export interface ConversationStatusSubsystemEventData extends SubsystemEventData {
    subtype: typeof ConversationStatusSubsystemEventDataSubtype
}