from __future__ import annotations

import logging
import uuid
from copy import deepcopy

import groq
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .agent.graph import run_turn
from .agent.state import Stage
from .config import FRONTEND_ORIGINS
from .session_store import get_or_create, reset, save

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-agent")

app = FastAPI(title="Voice Booking Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TurnRequest(BaseModel):
    session_id: str | None = None
    message: str


class TurnResponse(BaseModel):
    session_id: str
    agent_message: str
    stage: str
    fields: dict[str, str]
    missing_fields: list[str]
    ambiguous_fields: dict[str, str]
    is_complete: bool


GREETING = (
    "Hi! I can help you book a pickup and delivery. "
    "What would you like to move, and where from and to?"
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/conversation/start", response_model=TurnResponse)
def start_conversation():
    session_id = str(uuid.uuid4())
    booking = get_or_create(session_id)
    booking.stage = Stage.COLLECTING
    booking.history.append({"role": "assistant", "content": GREETING})
    save(booking)
    return TurnResponse(
        session_id=session_id,
        agent_message=GREETING,
        stage=booking.stage.value,
        fields=booking.to_summary_dict(),
        missing_fields=booking.missing_required(),
        ambiguous_fields=booking.ambiguous_fields,
        is_complete=False,
    )


@app.post("/api/conversation/turn", response_model=TurnResponse)
def take_turn(req: TurnRequest):
    if not req.session_id:
        raise HTTPException(400, "session_id is required (call /start first)")

    # Work on a copy so a failed provider call cannot leave a half-processed
    # turn in the in-memory session. Only save the state after full success.
    booking = deepcopy(get_or_create(req.session_id))
    if booking.stage == Stage.COMPLETE:
        raise HTTPException(400, "This booking is already complete. Start a new session.")

    message = (req.message or "").strip()
    # Empty transcript (e.g. STT produced nothing from silence / dead air)
    # is itself meaningful signal -- feed it through as an explicit marker
    # rather than sending a blank string to the LLM.
    if not message:
        message = "[NO_SPEECH_DETECTED]"

    try:
        booking = run_turn(booking, message)
    except groq.RateLimitError as e:
        logger.exception("Groq rate limit reached")
        raise HTTPException(429, "Rate limit reached, please wait and retry.") from e
    except groq.APIStatusError as e:
        logger.exception("Groq API status error")
        if e.status_code == 401:
            detail = "Groq rejected the API key. Set a valid GROQ_API_KEY and restart the backend."
        elif e.status_code == 403:
            detail = "This Groq project is not allowed to use the selected model. Check model permissions in Groq."
        elif e.status_code == 404:
            detail = "The configured Groq model is unavailable. Restart the backend to use the current default model."
        elif e.status_code == 400:
            detail = "Groq rejected the AI request. Check the backend terminal for the exact request validation error."
        else:
            detail = "AI service rejected the request. Check the backend terminal for details."
        raise HTTPException(502, detail) from e
    except groq.APIConnectionError as e:
        logger.exception("Groq API connection error")
        raise HTTPException(504, "AI service is temporarily unavailable, please try again.") from e
    except Exception:
        logger.exception("Unexpected error handling turn")
        raise HTTPException(500, "Something went wrong processing that turn.")

    save(booking)

    # Pull the last assistant message out of history as the reply text.
    reply = next(
        (h["content"] for h in reversed(booking.history) if h["role"] == "assistant"), ""
    )

    return TurnResponse(
        session_id=booking.session_id,
        agent_message=reply,
        stage=booking.stage.value,
        fields=booking.to_summary_dict(),
        missing_fields=booking.missing_required(),
        ambiguous_fields=booking.ambiguous_fields,
        is_complete=booking.ended,
    )


@app.post("/api/conversation/{session_id}/reset")
def reset_conversation(session_id: str):
    reset(session_id)
    return {"status": "reset"}
