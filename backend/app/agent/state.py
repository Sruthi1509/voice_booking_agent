"""
Booking conversation state.

Design notes
------------
We deliberately do NOT model this as a flat "slot filling" dict of strings.
Each field carries enough metadata (raw text as heard, resolved/normalized
value, confidence, whether it still needs voice-confirmation) for the graph
to reason about *how* it knows something, not just *what* it knows.

This is what lets the agent:
- avoid re-asking for things it already has,
- know the difference between "I'm not sure I heard that right" (needs
  confirm-back) and "the user just changed their mind" (correction),
- explain *why* it's asking a clarifying question instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, TypedDict


class Stage(str, Enum):
    GREETING = "greeting"
    COLLECTING = "collecting"
    CLARIFYING = "clarifying"
    CONFIRMING_FIELD = "confirming_field"   # confirm-back a risky field (phone, date)
    CONFIRMING_SUMMARY = "confirming_summary"  # final full-summary confirmation
    COMPLETE = "complete"


# Fields we must have before we can summarize a booking.
REQUIRED_FIELDS = [
    "pickup_location",
    "drop_location",
    "date",
    "time",
    "load_description",
    "contact_number",
]

OPTIONAL_FIELDS = ["special_instructions"]

# Fields where a mis-transcription is costly enough that we always read
# them back to the user once captured, rather than trusting STT blindly.
CONFIRM_BACK_FIELDS = {"contact_number", "date"}


@dataclass
class FieldValue:
    raw_text: str                     # what the user said (post-STT)
    value: str                        # normalized/resolved value we'll use
    confidence: str = "high"          # "high" | "low"
    source_turn: int = 0
    confirmed: bool = False           # True once user has verbally confirmed it


@dataclass
class BookingState:
    session_id: str
    stage: Stage = Stage.GREETING
    turn_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    fields: dict[str, FieldValue] = field(default_factory=dict)

    # Fields we heard something for, but couldn't resolve confidently enough
    # to store -> {field_name: raw_text_heard}
    ambiguous_fields: dict[str, str] = field(default_factory=dict)

    # Fields currently awaiting a yes/no confirm-back from the user.
    pending_confirmation_field: Optional[str] = None

    # Set True once the user has explicitly confirmed that identical
    # pickup/drop locations are intentional (e.g. moving within the same
    # building). Prevents re-asking every subsequent turn.
    same_location_confirmed: bool = False

    # Validation problems blocking progress, e.g. {"date": "That date is in the past."}
    validation_errors: dict[str, str] = field(default_factory=dict)

    # Audit trail of corrections: [{"field", "old", "new", "turn"}]
    corrections_log: list[dict[str, Any]] = field(default_factory=list)

    # Full transcript for prompting + debugging: [{"role","content"}]
    history: list[dict[str, str]] = field(default_factory=list)

    # Consecutive turns with no usable input (silence / empty STT / gibberish)
    consecutive_unclear_turns: int = 0

    # Consecutive off-topic turns, used to decide whether to gently
    # re-steer or just answer and move on.
    consecutive_off_topic_turns: int = 0

    ended: bool = False

    # ---- derived helpers -------------------------------------------------

    def missing_required(self) -> list[str]:
        return [f for f in REQUIRED_FIELDS if f not in self.fields]

    def unconfirmed_critical_fields(self) -> list[str]:
        return [
            f for f in CONFIRM_BACK_FIELDS
            if f in self.fields and not self.fields[f].confirmed
        ]

    def to_summary_dict(self) -> dict[str, str]:
        return {k: v.value for k, v in self.fields.items()}

    def known_fields_text(self) -> str:
        """Human-readable snapshot of what we already know, for prompting."""
        if not self.fields:
            return "(nothing yet)"
        return "; ".join(f"{k}={v.value}" for k, v in self.fields.items())


class TurnResult(TypedDict):
    """What the graph returns to the API layer for a single turn."""
    agent_message: str
    stage: str
    state: dict
    missing_fields: list[str]
    validation_errors: dict[str, str]
    is_complete: bool
