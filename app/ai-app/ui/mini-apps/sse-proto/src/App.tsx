// src/App.tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { SseChatService } from "./sse/SseChatService";
import type { ChatDeltaEnvelope } from "./types";
import { v4 as uuidv4 } from "uuid";

type Counters = Record<string, number>;

export default function App() {
    const svc = useMemo(
        () =>
            new SseChatService({
                // these default to env vars, but you can override here
            }),
        []
    );

    const [connected, setConnected] = useState(false);
    const [connecting, setConnecting] = useState(false);
    const [sessionId, setSessionId] = useState<string | undefined>();
    const [streamId, setStreamId] = useState<string | undefined>();
    const [message, setMessage] = useState("hello");
    const [file, setFile] = useState<File | null>(null);
    const [conversationId, setConversationId] = useState(uuidv4());
    const countersRef = useRef<Counters>({});

    const bump = (ev: string) => {
        countersRef.current[ev] = (countersRef.current[ev] || 0) + 1;
    };

    const [redraw, setRedraw] = useState(0);
    const force = () => setRedraw((n) => n + 1);

    const connect = async () => {
        try {
            setConnecting(true);
            const prof = await svc.fetchProfile();
            setSessionId(prof.session_id);

            await svc.connect({
                onReady: (payload) => {
                    bump("ready");
                    setStreamId(svc.getStreamId());
                    force();
                    console.log("ready:", payload);
                    setConnected(true);
                    setConnecting(false);
                },
                onChatStart: (e) => {
                    bump("chat_start");
                    force();
                    console.log("chat_start", e);
                },
                onChatStep: (e) => {
                    bump("chat_step");
                    force();
                    console.log("chat_step", e);
                },
                onChatDelta: (e: ChatDeltaEnvelope) => {
                    bump("chat_delta");
                    force();
                    // Live delta text optional:
                    // console.log("Δ:", e.delta.text);
                },
                onChatComplete: (e) => {
                    bump("chat_complete");
                    force();
                    console.log("chat_complete", e);
                },
                onChatError: (e) => {
                    bump("chat_error");
                    force();
                    console.error("chat_error", e);
                },
                onConvStatus: (e) => {
                    bump("conv_status");
                    force();
                    console.log("conv_status", e);
                },
                onError: (err) => {
                    console.error("SSE error:", err);
                    setConnected(false);
                    setConnecting(false);
                },
            });
        } catch (e) {
            console.error(e);
            setConnecting(false);
        }
    };

    const disconnect = () => {
        svc.disconnect();
        setConnected(false);
    };

    const send = async () => {
        try {
            const attachment = file ? [file] : undefined;
            await svc.sendChatMessage(
                {
                    message,
                    chat_history: [],
                    conversation_id: conversationId,
                    // optional route to a specific workflow:
                    bundle_id: import.meta.env.VITE_BUNDLE_ID || undefined,
                },
                attachment
            );
            console.log("enqueue sent");
        } catch (e) {
            console.error(e);
        }
    };

    const counters = countersRef.current;

    return (
        <div style={{ fontFamily: "system-ui, Arial, sans-serif", padding: 24 }}>
            <h2>SSE Chat Prototype</h2>
            <div style={{ marginBottom: 12 }}>
                <button disabled={connected || connecting} onClick={connect}>
                    {connecting ? "Connecting…" : "Connect"}
                </button>{" "}
                <button disabled={!connected} onClick={disconnect}>
                    Disconnect
                </button>
            </div>

            <div style={{ marginBottom: 12 }}>
                <div><b>Session:</b> {sessionId || "—"}</div>
                <div><b>Stream:</b> {streamId || "—"}</div>
                <div><b>Conversation:</b> {conversationId}</div>
                <button onClick={() => setConversationId(uuidv4())}>New Conversation ID</button>
            </div>

            <div style={{ display: "grid", gap: 8, maxWidth: 640 }}>
                <label>
                    Message
                    <input
                        style={{ width: "100%" }}
                        value={message}
                        onChange={(e) => setMessage(e.currentTarget.value)}
                        placeholder="Type your prompt…"
                    />
                </label>
                <label>
                    Attachment
                    <input type="file" onChange={(e) => setFile(e.currentTarget.files?.[0] || null)} />
                </label>
                <button disabled={!connected} onClick={send}>
                    Send
                </button>
            </div>

            <hr style={{ margin: "24px 0" }} />

            <h3>Event counters</h3>
            <pre style={{ background: "#111", color: "#0f0", padding: 12 }}>
        {JSON.stringify(counters, null, 2)}
      </pre>
            <p style={{ color: "#666" }}>
                You should see <code>chat_start</code>, a bunch of <code>chat_delta</code>, and a{" "}
                <code>chat_complete</code>.
            </p>
        </div>
    );
}
