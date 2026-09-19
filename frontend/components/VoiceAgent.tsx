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
  const [isMicActive, setIsMicActive] = useState(false);

  const {
    supported,
    status,
    interimTranscript,
    isSpeaking,
    isMuted,
    listenOnce,
    stopListening,
    speak,
    stopSpeaking,
    toggleMuted,
  } = useSpeech();

  const bottomRef = useRef<HTMLDivElement>(null);
  const hasInitializedRef = useRef(false);
  const isMicActiveRef = useRef(false);
  const loopRunningRef = useRef(false);
  const sessionIdRef = useRef<string | null>(null);
  const missingFieldsRef = useRef<string[]>([]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const applyResponseData = useCallback((res: TurnResponse) => {
    setSessionId(res.session_id);
    sessionIdRef.current = res.session_id;
    setFields(res.fields);
    setMissingFields(res.missing_fields);
    missingFieldsRef.current = res.missing_fields;
    setIsComplete(res.is_complete);
    setMessages((prev) => [...prev, { role: "assistant", content: res.agent_message }]);
  }, []);

  const runTurnProcess = useCallback(
    async (text: string, addToTranscript = true) => {
      const activeSession = sessionIdRef.current;
      if (!activeSession) return;
      const displayText = text || "(no speech detected)";
      if (addToTranscript) {
        setMessages((prev) => [...prev, { role: "user", content: displayText }]);
      }
      setBusy(true);
      setError(null);
      try {
        const res = await sendTurn(activeSession, text);
        setFailedMessage(null);
        applyResponseData(res);
        if ("speechSynthesis" in window && res.agent_message) {
          await speak(res.agent_message);
        }
      } catch (e: any) {
        setFailedMessage(text);
        setError(e.message || "Something went wrong. Please try again.");
      } finally {
        setBusy(false);
      }
    },
    [applyResponseData, speak]
  );

  const startContinuousListeningLoop = useCallback(async () => {
    if (loopRunningRef.current) return;
    loopRunningRef.current = true;

    while (isMicActiveRef.current && supported) {
      if (isSpeaking) {
        stopSpeaking();
      }

      const transcript = await listenOnce({
        itemHint: missingFieldsRef.current[0] === "load_description",
      });

      if (!isMicActiveRef.current) break;

      if (!transcript.trim()) {
        // Silence or no speech detected in this cycle.
        // Wait briefly and loop back as long as user keeps Mic ON.
        await new Promise((r) => setTimeout(r, 400));
        continue;
      }

      // Valid utterance captured! Send to backend and speak reply
      await runTurnProcess(transcript, true);

      if (!isMicActiveRef.current) break;
    }

    loopRunningRef.current = false;
  }, [supported, isSpeaking, stopSpeaking, listenOnce, runTurnProcess]);

  const init = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const res = await startConversation();
      applyResponseData(res);
      if ("speechSynthesis" in window && res.agent_message) {
        await speak(res.agent_message);
      }
    } catch (e: any) {
      setError(e.message || "Failed to start conversation.");
    } finally {
      setBusy(false);
    }
  }, [applyResponseData, speak]);

  useEffect(() => {
    if (hasInitializedRef.current) return;
    hasInitializedRef.current = true;
    init();
  }, [init]);

  const handleMicToggle = useCallback(() => {
    if (isMicActiveRef.current) {
      // User turning Mic OFF
      isMicActiveRef.current = false;
      setIsMicActive(false);
      stopListening();
      stopSpeaking();
    } else {
      // User turning Mic ON
      isMicActiveRef.current = true;
      setIsMicActive(true);
      void startContinuousListeningLoop();
    }
  }, [startContinuousListeningLoop, stopListening, stopSpeaking]);

  const handleManualSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (!manualInput.trim()) return;
      if (isSpeaking) stopSpeaking();
      const text = manualInput.trim();
      setManualInput("");
      void runTurnProcess(text, true);
    },
    [manualInput, isSpeaking, stopSpeaking, runTurnProcess]
  );

  const handleRestart = useCallback(async () => {
    isMicActiveRef.current = false;
    setIsMicActive(false);
    stopListening();
    stopSpeaking();
    if (sessionId) await resetConversation(sessionId);
    setMessages([]);
    setFields({});
    setMissingFields([]);
    setIsComplete(false);
    init();
  }, [sessionId, init, stopListening, stopSpeaking]);

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
                runTurnProcess(failedMessage, false);
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
          onClick={handleMicToggle}
          disabled={!supported}
          className={`h-14 w-14 rounded-full flex items-center justify-center text-xl shrink-0 transition
            ${isMicActive ? "bg-red-600 animate-pulse shadow-lg shadow-red-600/50" : "bg-blue-600 hover:bg-blue-500"}
            disabled:opacity-40 disabled:cursor-not-allowed`}
          aria-label={isMicActive ? "Turn Off Microphone" : "Turn On Microphone"}
        >
          {isMicActive ? "🎙️" : "🎤"}
        </button>
        <div className="text-xs text-neutral-400">
          {isMicActive
            ? status === "listening"
              ? "Mic ON (Listening continuously… Tap to turn OFF)"
              : isSpeaking
              ? "Agent speaking (Mic ON… Tap to interrupt)"
              : busy
              ? "Processing input…"
              : "Mic ON (Waiting for speech…)"
            : isSpeaking
            ? "Agent speaking (Mic OFF)"
            : busy
            ? "Thinking…"
            : "Mic OFF (Tap button to turn ON continuous listening)"}
        </div>
        <button
          type="button"
          onClick={toggleMuted}
          className="ml-auto rounded-lg border border-neutral-700 px-3 py-2 text-xs text-neutral-300 hover:bg-neutral-800"
          aria-label={isMuted ? "Unmute assistant voice" : "Mute assistant voice"}
        >
          {isMuted ? "Unmute voice" : "Mute voice"}
        </button>
      </div>

      <form onSubmit={handleManualSubmit} className="flex gap-2">
        <input
          value={manualInput}
          onChange={(e) => setManualInput(e.target.value)}
          disabled={busy}
          placeholder="Or type instead…"
          className="flex-1 rounded-lg bg-neutral-900 border border-neutral-800 px-3 py-2 text-sm outline-none focus:border-blue-600"
        />
        <button
          type="submit"
          disabled={busy}
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
