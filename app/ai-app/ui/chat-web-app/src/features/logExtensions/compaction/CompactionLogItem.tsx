import {AlertCircle, Archive, CheckCircle2, Loader2} from "lucide-react";
import {ChatLogComponentProps} from "../../extensions/logExtesnions.ts";
import {CompactionArtifact, CompactionArtifactType} from "./types.ts";

const formatTokens = (value?: number | null): string => {
    if (!value || value <= 0) return "";
    return Math.round(value).toLocaleString();
}

const normalizeKind = (value?: string | null): string => {
    return (value ?? "").replaceAll("_", " ").trim();
}

const CompactionLogItem = ({item}: ChatLogComponentProps) => {
    if (item.artifactType !== CompactionArtifactType) {
        throw new Error("not a CompactionArtifact")
    }

    const compaction = item as CompactionArtifact;
    const status = (compaction.content.status || "update").toLowerCase();
    const kind = normalizeKind(compaction.content.kind);
    const compactedTokens = formatTokens(compaction.content.compactedTokens);
    const beforeTokens = formatTokens(compaction.content.beforeTokens);
    const afterTokens = formatTokens(compaction.content.afterTokens);
    const reason = normalizeKind(compaction.content.reason);

    const Icon = status === "started"
        ? Loader2
        : status === "completed"
            ? CheckCircle2
            : status === "skipped"
                ? Archive
                : AlertCircle;

    const title = compaction.content.title || (
        status === "started"
            ? "Context compaction started"
            : status === "completed"
                ? "Context compaction completed"
                : status === "skipped"
                    ? "Context compaction skipped"
                    : "Context compaction"
    );

    const detailParts = [
        kind,
        compactedTokens ? `compacted ~${compactedTokens} tokens` : "",
        !compactedTokens && beforeTokens && afterTokens ? `${beforeTokens} -> ${afterTokens} tokens` : "",
        reason ? `reason: ${reason}` : "",
    ].filter(Boolean);

    return (
        <div className="my-1 flex min-w-0 items-start gap-2 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs text-slate-700">
            <Icon size={15} className={`mt-0.5 shrink-0 ${status === "started" ? "animate-spin" : ""}`}/>
            <div className="min-w-0">
                <div className="font-semibold text-slate-800">{title}</div>
                {detailParts.length > 0 && (
                    <div className="mt-0.5 text-slate-600">{detailParts.join(" · ")}</div>
                )}
            </div>
        </div>
    )
}

export default CompactionLogItem
