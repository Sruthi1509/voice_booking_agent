const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export type TurnResponse = {
  session_id: string;
  agent_message: string;
  stage: string;
  fields: Record<string, string>;
  missing_fields: string[];
  ambiguous_fields: Record<string, string>;
  is_complete: boolean;
};

async function handle(res: Response): Promise<TurnResponse> {
  if (!res.ok) {
    let detail = "Something went wrong talking to the booking service.";
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore parse errors, use default message */
    }
    throw new Error(detail);
  }
  return res.json();
}

async function safeFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (err: any) {
    if (err.name === "TypeError" || err.message?.includes("fetch")) {
      throw new Error(`Cannot connect to backend server (${API_BASE}). Please make sure backend server is running.`);
    }
    throw err;
  }
}

export async function startConversation(): Promise<TurnResponse> {
  const res = await safeFetch(`${API_BASE}/api/conversation/start`, { method: "POST" });
  return handle(res);
}

export async function sendTurn(sessionId: string, message: string): Promise<TurnResponse> {
  const res = await safeFetch(`${API_BASE}/api/conversation/turn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
  return handle(res);
}

export async function resetConversation(sessionId: string): Promise<void> {
  await safeFetch(`${API_BASE}/api/conversation/${sessionId}/reset`, { method: "POST" });
}

