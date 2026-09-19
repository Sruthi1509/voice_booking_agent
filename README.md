# Voice Booking Agent — Porter-style AI Voice Assistant

An intelligent, state-driven AI voice booking assistant for a Porter-style logistics and moving service. It converts unstructured, noisy spoken requests into validated, structured bookings using **LangGraph**, **FastAPI**, **Groq LLM**, and the **Web Speech API**.

---

## 1. Key Capabilities & Enhancements

- **Tap-to-Talk Microphone Control**: The microphone stays **BLUE** when idle. Tapping the mic turns it **RED** to record continuous audio; tapping again turns it back to **BLUE** and triggers backend processing *only after* recording completes.
- **Phonetic Location Hearing & Intelligent Suggestions**: Resolves speech-to-text variations and regional location homophones (e.g., *"Vizianagaram"* / *"Vijayanagaram"* $\rightarrow$ *"Vijayanagar"*). If a location is outside the direct service zone but close to a served area, the agent proactively suggests: *"Did you mean Vijayanagar? Or is there a nearby landmark within our service zone?"*
- **Country Name Conversion & Non-Repeating Prompts**: Asks users for their country name (e.g., *"India"*, *"United States"*, *"UK"*), converts it to standard E.164 calling codes (`+91`, `+1`, `+44`), and **never re-asks for the country** once it is captured in the conversation state.
- **Strict Country Phone Number Validation**: Validates national digit length according to country rules (e.g., strictly enforcing **10 digits** for India `+91`). If the user provides an incomplete number (e.g., 8 digits: `623 875 12`), the system rejects it with an explicit explanation instead of silently accepting invalid numbers.
- **Informative Vehicle Tier Suggestions**: Automatically suggests appropriate vehicle fleet tiers (Two-wheeler, Mini-van, Mini-truck) based on item descriptions as an informative note without requiring separate confirmation turns.

---

## 2. High-Level Architecture

```
┌────────────────────────────────┐        REST (JSON)        ┌───────────────────────────────────┐
│     Next.js Frontend           │ ────────────────────────▶ │       FastAPI Backend             │
│                                │ ◀──────────────────────── │                                   │
│ - Continuous Mic Power Toggle  │                           │  LangGraph State Machine          │
│ - Web Speech (STT + TTS)       │                           │  (Deterministic + Perception LLM) │
│ - Chat & Live Summary Card     │                           │                                   │
│                                │                           │  --> Groq API (gpt-oss-120b)      │
└────────────────────────────────┘                           └───────────────────────────────────┘
```

- **Frontend (Next.js / React / TypeScript)**: Captures user speech using the browser-native Web Speech API, provides continuous listening loops, handles voice barge-in (interruption), speaks responses using `SpeechSynthesis`, and displays a live structured booking card.
- **Backend (FastAPI + LangGraph + Groq)**: Houses the conversation state machine (`BookingState`), structured perception layer, business rule validator, action decider, and spoken response generator.

---

## 3. The AI Layer — LangGraph State Machine

The conversation is modeled as a 4-step state graph:

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

| Node | Responsibility | LLM Usage |
|---|---|---|
| `perceive` | Classifies turn intent (`information`, `correction`, `confirmation_yes`, `confirmation_no`, `question`, `off_topic`, `unclear_or_silence`) and extracts structured candidate fields with confidence scores. | Groq Tool Call (`record_turn_analysis`) |
| `merge_and_validate` | Merges extracted values into `BookingState`. Low-confidence values go to `ambiguous_fields`. Runs deterministic validators (date, phone, country lookup, location fuzzy matching, load feasibility). | Pure Python |
| `decide` | Evaluates priority policy to pick the exact next action to perform. | Pure Python |
| `respond` | Generates a natural, spoken response steering the conversation forward without reading out internal field names or re-asking for confirmed data. | Groq LLM (Free-Text Spoken Prose) |
| `finalize` | Generates final structured booking summary upon user confirmation. | Pure Python |

### `decide` Policy Priority Order

1. **Booking Already Confirmed** $\rightarrow$ Finalize session and present summary.
2. **Repeated Silence / Unclear Turns (3+)** $\rightarrow$ Escalate to help/re-prompt.
3. **Unclear or Silent Turn** $\rightarrow$ Rephrase the pending question gently.
4. **Validation Violation** (e.g. past date, unserviceable area, oversized load) $\rightarrow$ Explain error and request alternative / offer close location suggestion.
5. **Ambiguous Field** $\rightarrow$ Ask user to clarify with examples.
6. **Off-Topic Remark / Question** $\rightarrow$ Answer briefly and gently steer back to pending field.
7. **Unconfirmed Critical Field** (Date / Contact Number) $\rightarrow$ Ask explicit confirm-back.
8. **Missing Required Field** $\rightarrow$ Ask for next missing field in logical order (`pickup_location`, `drop_location`, `date`, `time`, `load_description`, `country_code`, `contact_number`).
9. **All Fields Present & Confirmed** $\rightarrow$ Present full summary for final confirmation.

---

## 4. Edge Cases & Negative-Path Handling

| Case | Handling Mechanism |
|---|---|
| **Location Homophones & Close Suggestions** | Explicit alias lookup (`LOCATION_ALIASES`) maps regional variations (e.g. *"Vizianagaram"* $\rightarrow$ *"Vijayanagar"*). Unserviceable locations triggers `find_close_location_suggestion` to offer proactive suggestions (*"Did you mean Vijayanagar?"*). |
| **Country Name Conversion** | Converts spoken country names (*"India"*, *"US"*, *"UK"*, *"Australia"*, *"UAE"*) into standard calling codes (`+91`, `+1`, `+44`, `+61`, `+971`) and validates phone length against the country specification. |
| **Past Date Rejection** | Parses natural dates (*"tomorrow"*, *"next Monday"*, *"15th Sept"*) relative to reference time via `dateparser`. Rejects past dates and prompts for today or future dates. |
| **Oversized Load Detection** | Detects fleet load violations (*"container"*, *"factory"*, *"10 tons"*) and prompts user to clarify items or split the load. |
| **Continuous Mic Toggle** | User retains full control to turn Mic ON/OFF. Silence does not force mic shutdown; the continuous loop waits and resumes listening automatically until toggled OFF. |
| **Corrections & Overwrites** | Overwrites earlier values when `is_correction` is set, logs old/new values in `corrections_log`, and re-triggers confirm-back for critical fields. |

---

## 5. Directory Structure

```
voice-booking-agent/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI endpoints & CORS configuration
│   │   ├── config.py               # Environment configuration
│   │   ├── session_store.py        # In-memory session state storage
│   │   └── agent/
│   │       ├── state.py            # BookingState data model
│   │       ├── validation.py       # Deterministic rules (locations, dates, country codes)
│   │       ├── prompts.py          # Perception and Spoken Response prompts
│   │       ├── llm.py              # Groq tool call & generation client
│   │       └── graph.py            # LangGraph state machine workflow
│   ├── tests/
│   │   └── test_graph_offline.py   # Deterministic control-flow unit tests (16 tests)
│   ├── requirements.txt
│   ├── .env.example
│   └── Dockerfile
└── frontend/
    ├── app/                        # Next.js App Router
    ├── components/
    │   ├── VoiceAgent.tsx           # Mic toggle button, continuous audio loop & chat UI
    │   └── SummaryCard.tsx          # Live structured booking summary card
    ├── hooks/useSpeech.ts           # Web Speech API wrapper (STT + TTS)
    ├── lib/api.ts                   # Backend API client
    └── .env.local.example
```

---

## 6. Local Setup & Installation

### Prerequisites
- Python 3.10+
- Node.js 18+
- Groq API Key (Sign up at [Groq Console](https://console.groq.com/))

### Backend Setup

```bash
cd backend
python -m venv .venv
# On Windows PowerShell:
.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env` and set your Groq API key:
```env
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=openai/gpt-oss-120b
FRONTEND_ORIGINS=http://localhost:3000
```

Start the FastAPI backend server:
```bash
uvicorn app.main:app --reload --port 8000
```

### Running Backend Unit Tests

Run the offline deterministic test suite (no live API key needed):
```bash
cd backend
python -m pytest tests/ -v
```

### Frontend Setup

```bash
cd frontend
npm install
cp .env.local.example .env.local
```

Ensure `frontend/.env.local` contains:
```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Start the Next.js development server:
```bash
npm run dev
```

Open `http://localhost:3000` in Google Chrome, click **"🎤 Turn On Microphone"** to toggle continuous listening, and speak your booking request!

---

## 7. Environment Variables Reference

| Variable | Scope | Description |
|---|---|---|
| `GROQ_API_KEY` | Backend | Groq API Key used for Perception tool calling and Spoken response generation. |
| `GROQ_MODEL` | Backend | LLM model identifier (Default: `openai/gpt-oss-120b`). |
| `FRONTEND_ORIGINS` | Backend | Comma-separated list of allowed frontend origins for CORS. |
| `NEXT_PUBLIC_API_BASE_URL` | Frontend | Base HTTP URL of the FastAPI backend. |

---

## 8. License & Acknowledgments

Built for the AI Voice Agent Assessment using LangGraph, FastAPI, Groq, and Next.js.
