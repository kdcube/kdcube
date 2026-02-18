import {UnknownArtifact} from "../../chat/chatTypes.ts";
import {ArtifactStreamDataItem, ArtifactStreamParser} from "../../conversations/conversationsTypes.ts";

export class IgnoredArtifactStreamReducer implements ArtifactStreamParser {
    private readonly _ignoredTypes = new Map<string, string[]>();

    constructor(...ignored: { marker: string, subtypes: string[] }[]) {
        ignored.forEach(ignored => {
            this._ignoredTypes.set(ignored.marker, ignored.subtypes)
        })
    }

    process(artifactData: ArtifactStreamDataItem) {
        const ignored = !!(artifactData.extra?.sub_type && this._ignoredTypes.get(artifactData.marker)?.includes(artifactData.extra?.sub_type));
        if (ignored) console.debug("artifact ignored", artifactData);
        return ignored;
    };

    flush(): UnknownArtifact[] {
        return [];
    }
}