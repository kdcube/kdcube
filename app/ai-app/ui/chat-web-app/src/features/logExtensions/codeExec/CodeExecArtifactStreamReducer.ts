import {ArtifactStreamDataItem, ArtifactStreamParser} from "../../conversations/conversationsTypes.ts";
import {
    UnknownArtifact
} from "../../chat/chatTypes.ts";
import {
    CodeExecArtifact, CodeExecArtifactType, CodeExecCodeSubsystemEventDataSubtype,
    CodeExecContractSubsystemEventDataSubtype,
    CodeExecObjectiveSubsystemEventDataSubtype,
    CodeExecProgramNameSubsystemEventDataSubtype, CodeExecStatusSubsystemEventDataSubtype
} from "./types.ts";

export class CodeExecArtifactStreamReducer implements ArtifactStreamParser {
    private artifacts: CodeExecArtifact[] = []

    private getCodeExecArtifact(executionId: string, defaultTimestamp: number): CodeExecArtifact {
        const r = this.artifacts.find(c => c.content.executionId === executionId);
        return r ?? {
            content: {executionId},
            artifactType: CodeExecArtifactType,
            timestamp: defaultTimestamp
        }
    }

    private addCodeExecArtifact(codeExec: CodeExecArtifact) {
        const idx = this.artifacts.findIndex(c => c.content.executionId === codeExec.content.executionId)
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
            case CodeExecCodeSubsystemEventDataSubtype: {
                const execId = artifactData?.extra?.execution_id as string
                if (!execId) {
                    console.warn("no execution id found", artifactData)
                    break
                }
                const ce = this.getCodeExecArtifact(execId, artifactData.ts_first)
                ce.content.program = {
                    name: artifactData.artifact_name,
                    timestamp: artifactData.ts_first,
                    language: artifactData?.extra?.language as string,
                    title: artifactData?.extra?.title as string,
                    content: artifactData.text,
                }
                ce.timestamp = artifactData.ts_first < ce.timestamp ? artifactData.ts_first : ce.timestamp
                this.addCodeExecArtifact(ce)
                processed = true;
                break
            }
            case CodeExecProgramNameSubsystemEventDataSubtype: {
                const execId = artifactData?.extra?.execution_id as string
                if (!execId) {
                    console.warn("no execution id found", artifactData)
                    break
                }
                const ce = this.getCodeExecArtifact(execId, artifactData.ts_first)
                ce.content.name = {
                    name: artifactData.artifact_name,
                    timestamp: artifactData.ts_first,
                    title: artifactData?.extra?.title as string,
                    content: artifactData.text,
                }
                ce.timestamp = artifactData.ts_first < ce.timestamp ? artifactData.ts_first : ce.timestamp
                this.addCodeExecArtifact(ce)
                processed = true;
                break
            }
            case CodeExecObjectiveSubsystemEventDataSubtype: {
                const execId = artifactData?.extra?.execution_id as string
                if (!execId) {
                    console.warn("no execution id found", artifactData)
                    break
                }
                const ce = this.getCodeExecArtifact(execId, artifactData.ts_first)
                ce.content.objective = {
                    name: artifactData.artifact_name,
                    timestamp: artifactData.ts_first,
                    title: artifactData?.extra?.title as string,
                    content: artifactData.text,
                }
                ce.timestamp = artifactData.ts_first < ce.timestamp ? artifactData.ts_first : ce.timestamp
                this.addCodeExecArtifact(ce)
                processed = true;
                break
            }
            case CodeExecContractSubsystemEventDataSubtype: {
                const execId = artifactData?.extra?.execution_id as string
                if (!execId) {
                    console.warn("no execution id found", artifactData)
                    break
                }
                const ce = this.getCodeExecArtifact(execId, artifactData.ts_first)
                ce.content.contract = {
                    name: artifactData.artifact_name,
                    timestamp: artifactData.ts_first,
                    title: artifactData?.extra?.title as string,
                    content: JSON.parse(artifactData.text).contract,
                }
                ce.timestamp = artifactData.ts_first < ce.timestamp ? artifactData.ts_first : ce.timestamp
                this.addCodeExecArtifact(ce)
                processed = true;
                break
            }
            case CodeExecStatusSubsystemEventDataSubtype: {
                const execId = artifactData?.extra?.execution_id as string
                if (!execId) {
                    console.warn("no execution id found", artifactData)
                    break
                }
                const ce = this.getCodeExecArtifact(execId, artifactData.ts_first)
                ce.content.status = {
                    name: artifactData.artifact_name,
                    timestamp: artifactData.ts_first,
                    title: artifactData?.extra?.title as string,
                    content: JSON.parse(artifactData.text),
                }
                ce.timestamp = artifactData.ts_first < ce.timestamp ? artifactData.ts_first : ce.timestamp
                this.addCodeExecArtifact(ce)
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