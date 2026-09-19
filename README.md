# Voice Booking Agent - Porter-style AI Voice Assistant

An intelligent, state-driven AI voice booking assistant for a Porter-style logistics and moving service. It converts unstructured, noisy spoken requests into validated, structured bookings using **LangGraph**, **FastAPI**, **Groq LLM**, and the **Web Speech API**.

---

## 1. Key Capabilities

- **Tap-to-Talk Microphone Control** -- Mic stays **BLUE** when idle; turns **RED** while recording. Processing is triggered only after the user manually stops, avoiding premature or partial captures.
- **Speaker Volume Muting** -- A dedicated speaker button mutes/unmutes TTS output in real time (`utterance.volume = 0/1`). Unmuting lets the user hear the remainder of a response already in progress without resetting state or hanging the UI.
- **Phonetic STT Homophone Correction (Locations & Items)** -- Automatically maps misheard locations and item names to their canonical form before any business-rule validation.
- **Generalized Non-Transportable & Illogical Item Filtering** -- Detects pets, passengers, hazardous goods, abstract/natural elements, and completely unrecognized objects. Rejects them explicitly or asks the user once to confirm what they meant.
- **Item Quantity Validation** -- When plural items are named without a count, the assistant asks how many, because quantity drives vehicle tier selection.
- **Country-Aware Phone Validation** -- Converts spoken country names to E.164 codes, then enforces the exact national digit count for 30+ countries.
- **Non-Repeating Country Prompts** -- Once the country code is captured, the assistant never re-asks for it.
- **Informative Vehicle Tier Suggestions** -- Suggests Two-wheeler / Mini-van / Mini-truck as a brief informational note, without requiring a separate confirmation turn.
- **Natural Date & Time Prompts** -- Asks for date/time conversationally without hardcoded year examples (current year is injected dynamically).

---

## 2. High-Level Architecture

```
+--------------------------------+        REST (JSON)        +-----------------------------------+
|     Next.js Frontend           | ------------------------> |       FastAPI Backend             |
|                                | <------------------------ |                                   |
| - Tap-to-Talk Mic Toggle       |                           |  LangGraph State Machine          |
| - Speaker Volume Mute Button   |                           |  (Deterministic + Perception LLM) |
| - Web Speech (STT + TTS)       |                           |                                   |
| - Chat & Live Summary Card     |                           |  --> Groq API (gpt-oss-120b)      |
+--------------------------------+                           +-----------------------------------+
```

- **Frontend (Next.js / React / TypeScript)**: Captures user speech via the browser-native Web Speech API. Provides Tap-to-Talk recording, real-time speaker mute/unmute, STT homophone correction in-browser, and a live structured booking summary card.
- **Backend (FastAPI + LangGraph + Groq)**: Houses the conversation state machine (`BookingState`), structured perception layer (`perceive`), deterministic business-rule validator (`merge_and_validate`), action decider (`decide`), and spoken response generator (`respond`).

---

## 3. The AI Layer - LangGraph State Machine

The conversation is modeled as a 4-step state graph:

```
        +-----------+     +--------------------+     +--------+
 turn ->|  perceive |---->| merge_and_validate |---->| decide |
        |  (LLM #1) |     | (pure Python)      |     |(python)|
        +-----------+     +--------------------+     +---+----+
                                                          |
                                         +----------------+---------------+
                                         v                                v
                                  +------------+                   +-----------+
                                  |  respond   |                   | finalize  |
                                  |  (LLM #2)  |                   | (python)  |
                                  +------------+                   +-----------+
```

| Node | Responsibility | LLM Usage |
|---|---|---|
| `perceive` | Classifies turn intent and extracts structured candidate fields with confidence scores. | Groq Tool Call (`record_turn_analysis`) |
| `merge_and_validate` | Merges extracted values into `BookingState`. Low-confidence values go to `ambiguous_fields`. Runs all deterministic validators. | Pure Python |
| `decide` | Evaluates priority policy to select the exact next action. | Pure Python |
| `respond` | Generates natural, spoken output -- never reads internal field names, never re-asks for confirmed data. | Groq LLM |
| `finalize` | Generates the final confirmed booking summary. | Pure Python |

### `decide` Policy - Priority Order

1. **Booking Already Confirmed** -> Finalize session and read summary.
2. **Repeated Silence / Unclear Turns (>= 3)** -> Escalate with a help message.
3. **Unclear or Silent Turn** -> Rephrase the pending question without copying it word-for-word.
4. **Validation Error** (past date, unserviceable area, oversized load, bad phone, non-transportable item) -> State the problem plainly and ask for a valid alternative.
5. **Ambiguous Field** -> Ask the user to clarify with context.
6. **Off-Topic Remark** -> Answer briefly and steer back to the next missing field.
7. **Unconfirmed Critical Field** (date, contact number) -> Trigger a confirm-back read-out.
8. **Missing Required Field** -> Ask in order: pickup_location -> drop_location -> date -> time -> load_description -> country_code -> contact_number.
9. **All Fields Present & Confirmed** -> Present full summary for final confirmation.

---

## 4. Edge Cases & Negative-Path Handling

Every case listed below is enforced deterministically in Python -- independent of the LLM -- and covered by offline unit tests.

### 4.1 Location Handling

| Case | Behaviour |
|---|---|
| **STT homophones** | `LOCATION_ALIASES` maps regional speech variations: Vizianagaram / Vijayanagaram / Vijaianagar -> Vijayanagar; Marathalli -> Marathahalli; Malleshwaram -> Malleswaram; Rajaji Nagar -> Rajajinagar; White Field -> Whitefield; Indira Nagar -> Indiranagar; Koramangla -> Koramangala; HSR -> HSR Layout; Sarjapur -> Sarjapur Road; etc. |
| **Fuzzy matching** | `difflib.get_close_matches` (cutoff 0.55) catches minor typos or accent variations not covered by aliases. |
| **Unserviceable + close match** | If outside the service zone but phonetically close (cutoff 0.45), the agent proactively suggests: "Did you mean Vijayanagar?" rather than a flat rejection. |
| **Fully unserviceable** | Informs the user plainly and asks for a nearby locality. |
| **Same pickup & drop** | Detected once both locations are known. The agent asks the user to confirm intentional same-location use, or re-provide the drop location. Confirmation is stored; the question is never repeated every turn. |
| **Locked booking** | After the booking is confirmed, any attempt to change pickup or drop is blocked. The agent apologises and provides the customer support number. |

### 4.2 Date & Time Handling

| Case | Behaviour |
|---|---|
| **Natural language dates** | `dateparser` resolves "tomorrow", "next Monday", "15th September", "around 6pm" relative to the current system time (dynamic, not hardcoded). |
| **Past date** | Any date before today is rejected: "Sorry, that date has already passed. Please give me today's date or a future date." |
| **Far-future date (> 60 days)** | Flagged as outside the booking window; user is asked to pick a nearer date. |
| **Unparseable date text** | Returns a gentle prompt to rephrase. |
| **Stale year examples** | Prompts never include hardcoded year examples. The current year is injected dynamically into every LLM call. |

### 4.3 Item / Load Description Handling

#### STT Homophone Correction

| Misheard Word | Corrected To |
|---|---|
| tails, tail, tiles, tile, tales, tale, share, cheer, sheer, shair, char, chear, chare (and plurals) | chair / chairs |
| bridge, frig, frige, freeze, freezer | fridge |
| sofer, sofar, sopher | sofa |
| matras, matress | mattress |
| almeera | almirah |
| teble, tabel | table |
| cubboard | cupboard |

#### Item Logic Checks

| Case | Behaviour |
|---|---|
| **Duplicate item names** | "chair chair" or "a fridge, a fridge" is de-duplicated to a single mention via `collapse_repeated_phrase`. |
| **Plural items without quantity** | If the user says "chairs", "tables", "sofas", "fridges", "beds", "boxes", "almirahs", "mattresses", "tvs", "cots", "washing machines", etc. without specifying a count, the agent asks: "How many chairs are you moving? The number of items helps us assign the right vehicle size." |
| **Oversized load** | Triggered by: "container", "full truckload", numbers >= 10 tons, "entire house", "whole house", "factory", "warehouse", "shipping container". Informs user the load exceeds the largest vehicle (~1500 kg) and asks to clarify or split into multiple bookings. |
| **Live animals / pets** | Inputs containing "dog", "cat", "pet", "animal", "livestock", "cow", "goat", "bird", "snake", "horse", "pig", "chicken", etc. are rejected: "We are unable to transport live animals or pets. Our service is for household items, furniture, appliances, and cargo boxes." |
| **Passengers / people** | Inputs containing "passengers", "people", "persons", "kids", "children", "humans", "relatives", "family" are rejected with a similar explanation. |
| **Hazardous / illegal goods** | Inputs containing "explosives", "fireworks", "gasoline", "petrol", "diesel", "flammables", "poisons", "toxic", "weapons", "guns", "drugs", "contraband" are rejected: "We are unable to transport hazardous, flammable, or illegal materials." |
| **Non-physical / abstract items** | Inputs containing "clouds", "weather", "sky", "ocean", "sunlight", "time", "air", "thoughts", "ghosts", "volcano" are rejected: "We are unable to transport non-physical items or natural elements." |
| **Completely unrecognized items** | If the description contains no recognized household goods keywords and matches no non-transportable pattern, the agent prompts once: "I could not confirm '[item]' as a standard transportable item. Could you please confirm or re-state what you need moved?" |

### 4.4 Vehicle Tier Logic

| Tier | Triggered By | Capacity |
|---|---|---|
| **Two-wheeler** | Bike, scooter, envelope, document, small bag, one box, couple/few boxes | ~20 kg |
| **Mini-van (Tata Ace class)** | Fridge, washing machine, sofa, chair/chairs, table, mattress, appliance, 1BHK, few pieces of furniture | ~750 kg |
| **Mini-truck** | 2BHK, 3BHK, house shift, moving house, multiple rooms, office shift, several furniture | ~1500 kg |

The tier is announced as a **non-blocking informative note** -- no confirmation turn is required. Tier suggestion only fires after the item quantity check passes.

### 4.5 Contact Number Handling

| Case | Behaviour |
|---|---|
| **Country not yet captured** | Asks "Which country is your phone number from?" -- exactly once. |
| **Country already captured** | Skips the country question entirely; directly asks for the phone number. |
| **Country name spoken** | India / Indian / IND -> +91; United States / US / USA / America -> +1; UK / Britain / England -> +44; UAE / Dubai -> +971; Australia -> +61; Singapore -> +65; Germany -> +49; France -> +33; Japan -> +81; China -> +86; Pakistan -> +92; Bangladesh -> +880; Sri Lanka -> +94; Nepal -> +977; Malaysia -> +60; Philippines -> +63; Brazil -> +55; South Africa -> +27; and more. |
| **Digit length enforcement** | Country-specific rules applied strictly: India (+91): 10 digits; UAE (+971): 9 digits; US/Canada (+1): 10 digits; UK (+44): 10-11 digits; Singapore (+65): 8 digits; etc. Short numbers are rejected with an explicit message stating how many digits are expected. |
| **Spelled-out digits** | STT sometimes transcribes digits as words ("nine eight seven..."). These are converted to numerals before validation. |
| **Leading country code included** | If the user says "+91 98765 43210", the prefix is stripped before counting national digits. |
| **Leading zero in national number** | Stripped before validation (e.g. 09876543210 -> 9876543210). |
| **Read-back confirmation** | Phone numbers are always read back to the user for verbal confirmation before being stored as confirmed. |

### 4.6 Conversation Robustness

| Case | Behaviour |
|---|---|
| **Silence / empty STT** | Classified as `unclear_or_silence`; the same pending question is rephrased (different wording, same meaning). |
| **3+ consecutive silent turns** | Escalates to `handle_repeated_silence` to avoid an infinite loop. |
| **Repeated values** | If the user says the same thing twice ("Whitefield" after "Whitefield" was already stored), it is treated as confirmation, not a new entry. `is_repeated_value` collapses the duplicate. |
| **Corrections mid-conversation** | User can say "actually, make it Koramangala, not Whitefield". The correction is logged in `corrections_log`, the field is overwritten, and a confirm-back is re-triggered for critical fields. |
| **Off-topic / small talk** | Answered briefly; the agent then steers back to the next missing field without losing state. |
| **Low-confidence extractions** | Values marked `confidence: low` by the LLM go to `ambiguous_fields` and trigger a clarification question rather than being stored directly. |

---

## 5. Directory Structure

```
voice-booking-agent/
+-- backend/
|   +-- app/
|   |   +-- main.py                 # FastAPI endpoints & CORS configuration
|   |   +-- config.py               # Environment configuration
|   |   +-- session_store.py        # In-memory session state storage
|   |   +-- agent/
|   |       +-- state.py            # BookingState data model & REQUIRED_FIELDS
|   |       +-- validation.py       # All deterministic rules (locations, dates, phone,
|   |       |                       #   country codes, item homophones, load feasibility,
|   |       |                       #   non-transportable detection, quantity checks)
|   |       +-- prompts.py          # Perception and Spoken Response system prompts
|   |       +-- llm.py              # Groq tool call & generation client
|   |       +-- graph.py            # LangGraph state machine workflow
|   +-- tests/
|   |   +-- test_graph_offline.py   # Deterministic control-flow unit tests (19 tests)
|   +-- requirements.txt
|   +-- .env.example
|   +-- Dockerfile
+-- frontend/
    +-- app/                        # Next.js App Router
    +-- components/
    |   +-- VoiceAgent.tsx           # Tap-to-Talk mic, speaker mute button & chat UI
    |   +-- SummaryCard.tsx          # Live structured booking summary card
    +-- hooks/useSpeech.ts           # Web Speech API wrapper (STT + TTS + homophones)
    +-- lib/api.ts                   # Backend API client
    +-- .env.local.example
```

---

## 6. Local Setup & Installation

### Prerequisites
- Python 3.10+
- Node.js 18+
- Groq API Key (sign up at [Groq Console](https://console.groq.com/))

### Backend Setup

```bash
cd backend
python -m venv .venv

# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux / macOS:
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

Start the FastAPI server:
```bash
uvicorn app.main:app --reload --port 8000
```

### Running the Offline Test Suite

All 19 control-flow tests run without a live API key:
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

Set the backend URL in `frontend/.env.local`:
```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Start the Next.js dev server:
```bash
npm run dev
```

Open `http://localhost:3000` in **Google Chrome**, tap the blue mic button to start recording (turns red), tap again to stop and process (turns blue), and hear the agent's spoken response.

---

## 7. Environment Variables

| Variable | Scope | Description |
|---|---|---|
| `GROQ_API_KEY` | Backend | Groq API key for LLM calls. |
| `GROQ_MODEL` | Backend | Model identifier (default: `openai/gpt-oss-120b`). |
| `FRONTEND_ORIGINS` | Backend | Comma-separated list of allowed CORS origins. |
| `NEXT_PUBLIC_API_BASE_URL` | Frontend | Base HTTP URL of the FastAPI backend. |

---

## 8. Assumptions & Known Limitations

- **Service area is demo-scoped**: Serviceable localities are a fixed list (Bangalore neighbourhoods). In production this would be replaced by a geocoding + service-polygon API.
- **STT engine**: Uses the browser-native Web Speech API (Chrome). For production, a server-side provider (Whisper / Deepgram) is a drop-in replacement for `useSpeech.ts`.
- **TTS engine**: Uses browser `SpeechSynthesis`. For production, ElevenLabs or similar can replace the `speak()` function.
- **Session storage**: Booking state is kept in-memory per session ID. A Redis or DB-backed store would be needed for multi-instance deployments.
- **Phone validation**: Digit-length rules are stored statically in `COUNTRY_PHONE_SPECS`. Number portability and network-level checks are outside scope.

---

## 9. License & Acknowledgments

Built for the AI Voice Agent Assessment using LangGraph, FastAPI, Groq, and Next.js.
