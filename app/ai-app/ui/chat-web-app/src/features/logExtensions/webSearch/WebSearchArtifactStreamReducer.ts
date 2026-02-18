import {
    UnknownArtifact
} from "../../chat/chatTypes.ts";
import {ArtifactStreamDataItem, ArtifactStreamParser} from "../../conversations/conversationsTypes.ts";
import {
    WebSearchArtifact, WebSearchArtifactType,
    WebSearchFilteredResultsSubsystemEventDataSubtype, WebSearchHTMLViewSubsystemEventDataSubtype
} from "./types.ts";

export class WebSearchArtifactStreamReducer implements ArtifactStreamParser {
    private artifacts: WebSearchArtifact[] = []

    private getWebSearchArtifact(searchId: string, defaultTimestamp: number): WebSearchArtifact {
        const r = this.artifacts.find(c => c.content.searchId === searchId);
        return r ?? {
            content: {
                name: "Web Search",
                searchId,
                items: []
            },
            artifactType: WebSearchArtifactType,
            timestamp: defaultTimestamp
        }
    }

    private addWebSearchArtifact(codeExec: WebSearchArtifact) {
        const idx = this.artifacts.findIndex(c => c.content.searchId === codeExec.content.searchId)
        if (idx >= 0) {
            this.artifacts.splice(idx, 1, codeExec)
        } else {
            this.artifacts.push(codeExec)
        }
    }

    process(artifactData: ArtifactStreamDataItem) {
        if (artifactData.marker !== "subsystem") return false;
        let processed = false;
        switch (artifactData?.extra?.sub_type) {
            case WebSearchFilteredResultsSubsystemEventDataSubtype: {
                const searchId = artifactData?.extra?.search_id as string
                if (!searchId) {
                    console.warn("no search id found", artifactData)
                    break
                }
                const ws = this.getWebSearchArtifact(searchId, artifactData.ts_first)
                ws.content.name = artifactData.artifact_name
                ws.content.title = artifactData.title
                const d = JSON.parse(artifactData.text)
                ws.content.objective = d.objective
                ws.content.queries = d.queries
                ws.content.items = d.results
                this.addWebSearchArtifact(ws)
                processed = true;
                break
            }
            case WebSearchHTMLViewSubsystemEventDataSubtype: {
                const searchId = artifactData?.extra?.search_id as string
                if (!searchId) {
                    console.warn("no search id found", artifactData)
                    break
                }
                const ws = this.getWebSearchArtifact(searchId, artifactData.ts_first)
                ws.content.reportContent = artifactData.text
                this.addWebSearchArtifact(ws)
                processed = true;
                break
            }
        }
        return processed;
    }

    flush(): UnknownArtifact[] {
        const r = this.artifacts
        this.artifacts = []
        return r
    }

}