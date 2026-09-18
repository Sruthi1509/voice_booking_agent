# Voice Booking Agent — Porter-style AI Booking Assistant

An AI voice agent that turns a messy, spoken booking request ("I need to
move a few things from Koramangala to Whitefield tomorrow evening") into a
complete, validated, structured booking — asking only for what's actually
missing, catching STT errors, corrections, and impossible requests along
the way.

This README is the architecture write-up requested in the assessment. It
covers: what was built, why, how the "hard part" (the AI/state layer) is
designed, and what's deliberately out of scope for a one-day build.

---

## 1. Live Demo & Repo

- **Live demo:** `<ADD_YOUR_DEPLOYED_URL_HERE>`
- **Repo:** `<ADD_YOUR_GITHUB_URL_HERE>`
- **Best tested in:** Chrome desktop or Android (Web Speech API support).

---

## 2. High-level architecture

```
┌─────────────────────┐        REST (JSON)        ┌──────────────────────────┐
│   Next.js frontend   │ ───────────────────────▶  │   FastAPI backend        │
│                      │ ◀───────────────────────  │                          │
│ - Web Speech API     │                            │  LangGraph state machine │
│   (STT + TTS, in-    │                            │  (the AI layer)          │
│   browser)           │                            │                          │
│ - Chat + summary UI  │                            │  --> Groq API            │
└─────────────────────┘                            └──────────────────────────┘
```

- **Frontend (Next.js / React / TypeScript):** captures speech in the
  browser via the Web Speech API, sends the transcript to the backend as
  plain text, speaks the agent's reply back via `speechSynthesis`, and
  renders a live "booking so far" summary card. The chat UI is intentionally
  simple — the assignment is explicit that the AI layer is what's graded.
- **Backend (FastAPI + LangGraph + Groq):** the actual booking agent.
  Stateless HTTP endpoints wrap a per-session `BookingState` and a compiled
  LangGraph graph that runs one perceive → merge/validate → decide →
  respond cycle per user turn.

### Why browser-native speech instead of a server-side STT/TTS vendor?

The brief explicitly says the focus is the AI layer, not the
plumbing, and that stack choice is left to the candidate. The Web Speech
API:

- needs **no additional API keys or per-minute cost**, which matters for a
  live demo link that strangers will hit,
- has genuinely good STT/TTS quality in Chrome for a conversational demo,
- keeps 100% of the engineering effort where it's graded — the
  understanding/state/validation layer — rather than in wiring up an audio
  pipeline.

`hooks/useSpeech.ts` is the only place this decision lives. Swapping to
Deepgram/Whisper for STT and ElevenLabs/PlayHT for TTS is a drop-in
replacement behind the same `listenOnce()` / `speak()` interface, done
server-side so keys never reach the client — noted as the natural next
step for a production version.

---

## 3. The AI layer — how the agent actually thinks

This is a **LangGraph state machine**, not a single "do everything" prompt.
That split is deliberate and maps directly to the evaluation criteria:

```
        ┌───────────┐     ┌────────────────────┐     ┌────────┐
 turn ─▶│ perceive  │────▶│ merge_and_validate │────▶│ decide │
        │ (LLM #1)  │     │ (pure Python)      │     │(python)│
        └───────────┘     └────────────────────┘     └───┬────┘
                                                           │
                                          ┌────────────────┴───────────────┐
                                          ▼                                ▼
                                   ┌────────────┐                   ┌───────────┐
                                   │  respond   │                   │ finalize  │
                                   │  (LLM #2)  │                   │ (python)  │
                                   └────────────┘                   └───────────┘
```

| Node | What it does | LLM? |
|---|---|---|
| `perceive` | Reads the latest transcript + short history, and calls Groq using a forced function tool to classify intent (information / correction / confirmation / question / off-topic / unclear-or-silence) and extract any booking fields mentioned, each tagged with a `confidence` (`high`/`low`) and `is_correction` flag. | Yes — perception only |
| `merge_and_validate` | Pure Python. Applies extracted fields to the running `BookingState`. Low-confidence fields go to `ambiguous_fields` (never silently accepted). High-confidence fields are run through **deterministic validators** (date, phone, service-area, load-feasibility). Corrections overwrite and are logged. Critical fields (phone, date) are flagged for a mandatory confirm-back. | No |
| `decide` | Pure Python priority order (see below) picks exactly one thing to do this turn. | No |
| `respond` | Turns the decision + state into a natural, spoken sentence via Groq (free text). Never re-asks for known fields; never reads out internal field names. | Yes — generation only |
| `finalize` | Deterministic summary read-back once the user confirms. | No |

**Why two separate LLM calls instead of one?** Perception (what did the
user actually say, structured) and generation (how do we phrase the next
question, in natural language) are different jobs with different failure
modes. Forcing perception through a tool schema means the extracted data
is always valid JSON with known fields — it can never be corrupted by the
model deciding to be chatty. Generation is free-text and allowed to be
warm and natural, but it never touches the booking state directly. Neither
call can accidentally corrupt the other's job.

**Why is merge/validate/decide plain Python, not more LLM calls?**
"Is this date in the past", "is this phone number 10 digits", "have all
required fields been collected" are facts, not judgment calls. Putting
them in code makes them unit-testable (see `backend/tests/`) and immune to
prompt drift — an LLM should never be the thing deciding whether a booking
is allowed to complete.

### `decide`'s priority order (the actual policy)

On every turn, exactly one action is chosen, in this order:

1. Booking already confirmed → wrap up (`finalize`).
2. 3+ consecutive unusable turns → escalate (`handle_repeated_silence`).
3. This turn was unclear/silent → acknowledge and re-prompt once.
4. A validation error exists (bad date, unserviceable area, oversized load)
   → surface it and ask for a fix.
5. An ambiguous field exists → ask the user to disambiguate (with why).
6. User asked an off-topic question → answer briefly, then continue.
7. A critical field (phone/date) is unconfirmed → read it back for a yes/no.
8. A required field is still missing → ask for the next one, in a natural
   order (never re-asking for something already known).
9. Everything required is present and confirmed → present the full
   structured summary and ask for final confirmation.
10. Already at the summary stage and the reply wasn't a clear yes/no →
    ask again for a clear confirm/deny.

This ordering itself encodes a value judgement the assessment explicitly
asks for: **validation problems and ambiguity are resolved before the
agent is allowed to move forward**, rather than being deferred to the end.

### The booking data model

Each field (`pickup_location`, `drop_location`, `date`, `time`,
`load_description`, `contact_number`, optional `special_instructions`) is
stored as a `FieldValue { raw_text, value, confidence, confirmed,
source_turn }`, not a bare string. That's what makes it possible to know
*why* something was accepted, not just *that* it was — which is what lets
the agent tell the difference between "still needs clarifying" and
"heard clearly but should be read back before trusting it."

---

## 4. Hidden / negative-path case analysis

The brief weights this heavily, so each case below is handled explicitly
and covered by an offline test in `backend/tests/test_graph_offline.py`
(runs without hitting the live LLM, by mocking the perception call, so
the *control-flow* logic can be verified deterministically):

| Case | How it's handled |
|---|---|
| **Ambiguity** ("a few things", "sometime tomorrow") | Perception marks these `confidence: low`. They are *never* written into the booking state — they go into `ambiguous_fields` and the agent explicitly asks the user to be more specific, optionally with example answers. The agent never guesses a value it wasn't given. |
| **Corrections / contradictions** ("actually, make it Whitefield not Indiranagar") | Perception flags `is_correction: true`. The merge step overwrites the old value, appends an entry to `corrections_log` (field/old/new/turn — auditable), and if the corrected field is a critical one (date/phone) it's re-flagged for confirm-back rather than trusted blindly. |
| **STT errors — mis-transcribed names** | Location fields are resolved through a fuzzy-match (`difflib.get_close_matches`) against a serviceable-area list, so "Coramangala" still resolves to "Koramangala". If nothing matches closely, it's treated as an **unserviceable-route validation error**, not silently accepted or silently dropped. |
| **STT errors — mis-transcribed numbers** | Phone numbers are extracted by stripping non-digits *and* by converting spelled-out digits ("nine eight seven…") if the digit-only pass comes up short. If the result still isn't a valid 10-digit number, it's rejected with a specific, actionable message rather than stored as garbage. Phone number and date are additionally always **read back for a yes/no confirmation** before being trusted, precisely because STT errors on numbers/dates are both common and costly if wrong. |
| **Impossible dates** (today or past dates) | `resolve_date()` parses relative/natural language ("tomorrow evening", "next Monday") via `dateparser`, anchored to the real current time, and explicitly requires a date after today with a clear recovery prompt. |
| **Unserviceable routes** | A fixed serviceable-locality list (demo scope) with fuzzy matching; anything outside it is rejected with an explanation, not silently accepted or hallucinated as valid. |
| **Loads no vehicle can carry** | `check_load_feasibility()` pattern-matches for "container", "entire house", "N tons", "warehouse", etc. and rejects with an explanation instead of pretending a mini-truck can move a factory. When the load *is* feasible, it also opportunistically suggests a vehicle tier (two-wheeler / mini-van / mini-truck) as a bonus, not a requirement. |
| **Silence / unusable audio** | An empty transcript is passed through as an explicit `[NO_SPEECH_DETECTED]` marker (not silently skipped), classified as `unclear_or_silence`, and tracked via `consecutive_unclear_turns`. One unclear turn gets a gentle re-prompt; three in a row escalates to a different message (in a full production build: offer the typed-input fallback, which the demo UI already supports as a manual text box). |
| **Interruptions (barge-in)** | The mic button doubles as an interrupt: pressing it while the agent is still speaking calls `stopSpeaking()` before starting to listen, so the user is never stuck waiting out a long TTS reply to get a word in. |
| **Off-topic turns** | Classified separately from booking information. The agent is instructed to briefly acknowledge/answer and then continue toward the next missing field in the *same* reply, rather than derailing the whole conversation or ignoring the user's aside. |
| **Uncertainty in general** | The design principle throughout: **low confidence and validation failures are never silently resolved by guessing.** The agent either asks (ambiguous), confirms (risky-but-clear), or rejects with a reason (invalid) — and which of the three it does is a deliberate, inspectable decision in `decide()`, not implicit LLM behavior. |
| **API failures / timeouts** | `main.py` distinguishes Groq rate limits, API-status errors, and connection failures, returning clear HTTP 429/502/504 messages while logging details server-side. |

---

## 5. Project structure

```
voice-booking-agent/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI endpoints
│   │   ├── config.py
│   │   ├── session_store.py        # in-memory session store
│   │   └── agent/
│   │       ├── state.py            # BookingState / FieldValue data model
│   │       ├── validation.py       # deterministic business rules
│   │       ├── prompts.py          # system prompts (perception + generation)
│   │       ├── llm.py              # Groq API wrapper (tool calling + free text)
│   │       └── graph.py            # LangGraph state machine
│   ├── tests/
│   │   └── test_graph_offline.py   # control-flow tests w/ mocked LLM
│   ├── requirements.txt
│   ├── .env.example
│   └── Dockerfile
└── frontend/
    ├── app/                        # Next.js App Router
    ├── components/
    │   ├── VoiceAgent.tsx           # main chat + mic UI
    │   └── SummaryCard.tsx          # live booking summary
    ├── hooks/useSpeech.ts           # Web Speech API wrapper (STT/TTS)
    ├── lib/api.ts                   # backend client
    └── .env.local.example
```

---

## 6. Setup & installation

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install groq
cp .env.example .env        # then fill in GROQ_API_KEY
uvicorn app.main:app --reload --port 8000
```

Run the offline control-flow tests (no API key needed — the LLM call is
mocked):

```bash
cd backend
python tests/test_graph_offline.py
```

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # point NEXT_PUBLIC_API_BASE_URL at your backend
npm run dev
```

Open `http://localhost:3000` in Chrome, click the mic, and talk.

### Environment variables

| Var | Where | Purpose |
|---|---|---|
| `GROQ_API_KEY` | backend | Groq API key. **Server-side only** — never sent to or read by the browser. |
| `GROQ_MODEL` | backend | Optional model override (defaults to `openai/gpt-oss-120b`). |
| `FRONTEND_ORIGINS` | backend | Comma-separated CORS allow-list for the deployed frontend origin(s). |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | URL of the deployed backend. |

---

## 7. Deployment

- **Frontend:** Vercel (zero-config for Next.js — `vercel.json` included).
  Set `NEXT_PUBLIC_API_BASE_URL` in the Vercel project's environment
  variables to point at the deployed backend.
- **Backend:** any container/Python host with a persistent process (Render,
  Railway, Fly.io) — a `Dockerfile` is included. Vercel serverless functions
  were avoided for the backend because LangGraph + a stateful session store
  are a more natural fit for a long-lived process than a cold-start
  function per request. Set `GROQ_API_KEY` and `FRONTEND_ORIGINS` on
  whichever host is used.

---

## 8. Assumptions & known limitations

- **Session storage is in-memory** (`session_store.py`), scoped to a single
  process — acceptable for a demo/assessment, but would move to Redis (or
  similar) for multi-instance production use.
- **Speech is browser-native** (Web Speech API), so quality/support varies
  by browser (best in Chrome) and there's no true simultaneous
  listen-while-speaking barge-in — interruption is handled via a manual
  "tap mic to interrupt" affordance instead of always-on voice activity
  detection.
- **Serviceable-area list and vehicle-tier rules are a fixed, small demo
  set** (a real system would use geocoding + a service-area polygon lookup,
  and a proper vehicle-capacity table from ops).
- **Date parsing** (`dateparser`) handles the common natural-language cases
  well ("tomorrow evening", "next Monday", "5th December") but can
  occasionally mis-resolve compound phrases like "day after tomorrow" to
  just "tomorrow" — a known third-party library limitation, not something
  papered over.
- **No persistent database** — completed bookings are returned in the API
  response and read back to the user, not written to storage. Adding a
  `bookings` table behind `finalize()` would be a small, isolated addition.
- **Single-language (English)** conversation for this build.

---

## 9. Notes for the reviewer

The backend's `agent/` folder is where essentially all of the design effort
went, per the brief's stated evaluation weighting. The frontend is
intentionally minimal — it exists to make the agent testable end-to-end via
voice, not to demonstrate UI polish.
