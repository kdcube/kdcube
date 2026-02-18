import {Timestamped} from "../../../types/common.ts";
import {Artifact, SubsystemEventData, TurnEvent} from "../../chat/chatTypes.ts";

export interface CodeExecBase<T> extends Timestamped {
    name: string;
    title?: string | null;
    description?: string | null;
    content: T;
}

export type CodeExecProgramName = CodeExecBase<string>
export type CodeExecObjective = CodeExecBase<string>

export interface CodeExecProgram extends CodeExecBase<string> {
    language: string;
}

export interface CodeExecContractArtifact {
    "artifact_name": string,
    "mime": string,
    "filename": string,
    "description": string,
}

export type CodeExecContract = CodeExecBase<CodeExecContractArtifact[]>

export interface CodeExecStatusFormat {
    status: "gen" | "exec" | "done" | "error";
    timings: {
        "codegen": number,
        "exec": number
    };
    "error": Record<string, string>;
}

export type CodeExecStatus = CodeExecBase<CodeExecStatusFormat>

export interface CodeExecData {
    executionId: string
    program?: CodeExecProgram;
    name?: CodeExecProgramName;
    objective?: CodeExecObjective;
    contract?: CodeExecContract;
    status?: CodeExecStatus;
}

export const CodeExecArtifactType = "code_exec";

export interface CodeExecArtifact extends Artifact<CodeExecData> {
    artifactType: typeof CodeExecArtifactType;
}

export interface CodeExecSubsystemEventData extends SubsystemEventData {
    executionId: string;
}

export const CodeExecCodeSubsystemEventDataSubtype = "code_exec.code"

export interface CodeExecCodeSubsystemEventData extends CodeExecSubsystemEventData {
    subtype: typeof CodeExecCodeSubsystemEventDataSubtype
    language: string;
}

export const CodeExecProgramNameSubsystemEventDataSubtype = "code_exec.program.name"

export interface CodeExecProgramNameSubsystemEventData extends CodeExecSubsystemEventData {
    subtype: typeof CodeExecProgramNameSubsystemEventDataSubtype
}

export const CodeExecObjectiveSubsystemEventDataSubtype = "code_exec.objective"

export interface CodeExecObjectiveSubsystemEventData extends CodeExecSubsystemEventData {
    subtype: typeof CodeExecObjectiveSubsystemEventDataSubtype
}

export const CodeExecContractSubsystemEventDataSubtype = "code_exec.contract"

export interface CodeExecContractSubsystemEventData extends CodeExecSubsystemEventData {
    subtype: typeof CodeExecContractSubsystemEventDataSubtype
}

export const CodeExecStatusSubsystemEventDataSubtype = "code_exec.status"

export interface CodeExecStatusSubsystemEventData extends CodeExecSubsystemEventData {
    subtype: typeof CodeExecStatusSubsystemEventDataSubtype
}

export const CodeExecEventSubtypes = [CodeExecCodeSubsystemEventDataSubtype, CodeExecProgramNameSubsystemEventDataSubtype,
    CodeExecObjectiveSubsystemEventDataSubtype, CodeExecContractSubsystemEventDataSubtype, CodeExecStatusSubsystemEventDataSubtype]

export type CodeExecMetaEventData = CodeExecCodeSubsystemEventData
    | CodeExecProgramNameSubsystemEventData
    | CodeExecObjectiveSubsystemEventData
    | CodeExecStatusSubsystemEventData
    | CodeExecContractSubsystemEventData

export type CodeExecEvent = TurnEvent<CodeExecMetaEventData>