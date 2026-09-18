"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { resetConversation, sendTurn, startConversation, TurnResponse } from "@/lib/api";
import { useSpeech } from "@/hooks/useSpeech";
import SummaryCard from "./SummaryCard";

type Message = { role: "assistant" | "user"; content: string };

export default function VoiceAgent() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [missingFields, setMissingFields] = useState<string[]>([]);
  const [isComplete, setIsComplete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failedMessage, setFailedMessage] = useState<string | null>(null);
  const [manualInput, setManualInput] = useState("");

  const {
    supported,
    status,
    interimTranscript,
    isSpeaking,
    listenOnce,
    stopListening,
    speak,
    stopSpeaking,
  } = useSpeech();

  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const init = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const res = await startConversation();
      applyResponse(res, true);
    } catch (e: any) {
      setError(e.message || "Failed to start conversation.");
    } finally {
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    init();
  }, [init]);

  function applyResponse(res: TurnResponse, isFirst = false) {
    setSessionId(res.session_id);
    setFields(res.fields);
    setMissingFields(res.missing_fields);
    setIsComplete(res.is_complete);
    setMessages((prev) => [...prev, { role: "assistant", content: res.agent_message }]);
    speak(res.agent_message);
  }

  const submitUserMessage = useCallback(
    async (text: string, addToTranscript = true) => {
      if (!sessionId) return;
      const displayText = text || "(no speech detected)";
      if (addToTranscript) {
        setMessages((prev) => [...prev, { role: "user", content: displayText }]);
      }
      setBusy(true);
      setError(null);
      try {
        const res = await sendTurn(sessionId, text);
        setFailedMessage(null);
        applyResponse(res);
      } catch (e: any) {
        setFailedMessage(text);
        setError(e.message || "Something went wrong. Please try again.");
      } finally {
        setBusy(false);
      }
    },
    [sessionId] // eslint-disable-line react-hooks/exhaustive-deps
  );

  const handleMicPress = useCallback(async () => {
    if (status === "listening") {
      stopListening();
      return;
    }
    if (isSpeaking) {
      // Barge-in: user tapping the mic while the agent is talking
      // interrupts playback immediately instead of waiting it out.
      stopSpeaking();
    }
    const transcript = await listenOnce();
    await submitUserMessage(transcript);
  }, [status, isSpeaking, stopListening, stopSpeaking, listenOnce, submitUserMessage]);

  const handleManualSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (!manualInput.trim()) return;
      submitUserMessage(manualInput.trim());
      setManualInput("");
    },
    [manualInput, submitUserMessage]
  );

  const handleRestart = useCallback(async () => {
    if (sessionId) await resetConversation(sessionId);
    setMessages([]);
    setFields({});
    setMissingFields([]);
    setIsComplete(false);
    init();
  }, [sessionId, init]);

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-4 h-96 overflow-y-auto flex flex-col gap-3">
        {messages.map((m, i) => (
          <div
            key={i}
            className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
              m.role === "assistant"
                ? "bg-neutral-800 text-neutral-100 self-start"
                : "bg-blue-600/80 text-white self-end"
            }`}
          >
            {m.content}
          </div>
        ))}
        {status === "listening" && interimTranscript && (
          <div className="self-end max-w-[85%] rounded-lg px-3 py-2 text-sm bg-blue-600/30 text-blue-200 italic">
            {interimTranscript}…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <SummaryCard fields={fields} missingFields={missingFields} isComplete={isComplete} />

      {error && (
        <div className="rounded-lg border border-red-800 bg-red-950/40 p-3 text-sm text-red-300 flex items-center justify-between gap-2">
          <span>{error}</span>
          <button
            onClick={() => {
              if (failedMessage !== null) {
                submitUserMessage(failedMessage, false);
              } else {
                init();
              }
            }}
            className="shrink-0 px-2.5 py-1 text-xs bg-red-900/80 hover:bg-red-800 text-white rounded border border-red-700/60 transition"
          >
            Retry
          </button>
        </div>
      )}

      {!supported && (
        <div className="rounded-lg border border-amber-800 bg-amber-950/30 px-3 py-2 text-sm text-amber-300">
          Your browser doesn&apos;t support voice input (try Chrome). You can still type below.
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={handleMicPress}
          disabled={busy || isComplete || !supported}
          className={`h-14 w-14 rounded-full flex items-center justify-center text-xl shrink-0 transition
            ${status === "listening" ? "bg-red-600 animate-pulse" : "bg-blue-600 hover:bg-blue-500"}
            disabled:opacity-40 disabled:cursor-not-allowed`}
          aria-label={status === "listening" ? "Stop recording" : "Start recording"}
        >
          🎤
        </button>
        <div className="text-xs text-neutral-500">
          {status === "listening"
            ? "Listening…"
            : isSpeaking
            ? "Agent speaking — tap mic to interrupt"
            : busy
            ? "Thinking…"
            : isComplete
            ? "Booking complete."
            : "Tap to speak"}
        </div>
      </div>

      <form onSubmit={handleManualSubmit} className="flex gap-2">
        <input
          value={manualInput}
          onChange={(e) => setManualInput(e.target.value)}
          disabled={busy || isComplete}
          placeholder="Or type instead…"
          className="flex-1 rounded-lg bg-neutral-900 border border-neutral-800 px-3 py-2 text-sm outline-none focus:border-blue-600"
        />
        <button
          type="submit"
          disabled={busy || isComplete}
          className="rounded-lg bg-neutral-800 px-4 py-2 text-sm hover:bg-neutral-700 disabled:opacity-40"
        >
          Send
        </button>
      </form>

      {isComplete && (
        <button
          onClick={handleRestart}
          className="self-start text-xs text-blue-400 hover:text-blue-300 underline"
        >
          Start a new booking
        </button>
      )}
    </div>
  );
}
