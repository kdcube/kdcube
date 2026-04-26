/*
 * EmptyDetails — shown when nothing is selected in the graph.
 */
function EmptyDetails() {
    return (
        <div className="flex flex-col items-center justify-center h-full text-center text-slate-500 text-sm py-6">
            <div className="max-w-xs space-y-2">
                <p className="font-medium text-slate-600">Click a node in the graph above</p>
                <p>
                    Pick a class, concept, or style policy to see what it is, how
                    it relates to other code, and how to use it in your bundle.
                </p>
                <p className="text-xs text-slate-400 pt-2">
                    Or ask the assistant directly — the chat is on the left.
                </p>
            </div>
        </div>
    );
}

export default EmptyDetails;
