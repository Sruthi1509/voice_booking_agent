"""
Offline test of the deterministic parts of the graph (merge/validate/decide)
by monkeypatching the LLM extraction call. This lets us verify control flow
-- missing-field ordering, ambiguity handling, corrections, validation
errors, confirm-back flow -- without needing a live ANTHROPIC_API_KEY.

Run with: python -m pytest tests/ -v   (from backend/)
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

from app.agent.state import BookingState, Stage
from app.agent import graph, validation
from datetime import datetime


def turn(booking, user_message, mocked_analysis):
    with patch("app.agent.graph.llm.extract_turn", return_value=mocked_analysis), \
         patch("app.agent.graph.llm.generate_reply", return_value="[mocked reply]"):
        return graph.run_turn(booking, user_message)


def test_ambiguous_load_not_accepted_blindly():
    b = BookingState(session_id="t1")
    b = turn(b, "I need to move a few things from Koramangala to Whitefield tomorrow evening", {
        "intent": "information",
        "fields": {
            "pickup_location": {"raw_text": "Koramangala", "confidence": "high", "is_correction": False},
            "drop_location": {"raw_text": "Whitefield", "confidence": "high", "is_correction": False},
            "date": {"raw_text": "tomorrow evening", "confidence": "high", "is_correction": False},
            "load_description": {"raw_text": "a few things", "confidence": "low", "is_correction": False},
        },
    })
    assert "load_description" not in b.fields
    assert "load_description" in b.ambiguous_fields
    print("OK: vague load description held back as ambiguous, not guessed.")


def test_correction_overwrites_and_flags_confirm_back():
    b = BookingState(session_id="t2")
    b = turn(b, "pickup from Indiranagar tomorrow, drop at Whitefield, moving a fridge, my number is 9876543210", {
        "intent": "information",
        "fields": {
            "pickup_location": {"raw_text": "Indiranagar", "confidence": "high", "is_correction": False},
            "drop_location": {"raw_text": "Whitefield", "confidence": "high", "is_correction": False},
            "date": {"raw_text": "tomorrow", "confidence": "high", "is_correction": False},
            "load_description": {"raw_text": "a fridge", "confidence": "high", "is_correction": False},
            "contact_number": {"raw_text": "9876543210", "confidence": "high", "is_correction": False},
        },
    })
    assert b.pending_confirmation_field in ("date", "contact_number")
    assert b.fields["pickup_location"].value == "Indiranagar"

    # user corrects pickup location
    b = turn(b, "actually pick it up from Koramangala instead", {
        "intent": "correction",
        "fields": {
            "pickup_location": {"raw_text": "Koramangala", "confidence": "high", "is_correction": True},
        },
    })
    assert b.fields["pickup_location"].value == "Koramangala"
    assert any(c["field"] == "pickup_location" for c in b.corrections_log)
    print("OK: correction overwrote old value and was logged.")


def test_past_date_rejected():
    b = BookingState(session_id="t3")
    b = turn(b, "book it for yesterday", {
        "intent": "information",
        "fields": {"date": {"raw_text": "yesterday", "confidence": "high", "is_correction": False}},
    })
    assert "date" not in b.fields
    assert "date" in b.validation_errors
    print("OK: past date rejected via validation_errors, not silently accepted.")


def test_today_allowed_and_explicit_past_date_rejected():
    reference = datetime(2026, 9, 18, 12, 0)
    value, error = validation.resolve_date("today", reference)
    assert value == "2026-09-18"
    assert error is None
    value, error = validation.resolve_date("15 September", reference)
    assert value is None
    assert error and "today's date or a future date" in error
    print("OK: today is allowed and an already-passed date without a year is rejected.")


def test_same_location_requires_one_explicit_confirmation():
    b = BookingState(session_id="t_same_location")
    b = turn(b, "pickup and drop are both in Whitefield", {
        "intent": "information",
        "fields": {
            "pickup_location": {"raw_text": "Whitefield", "confidence": "high", "is_correction": False},
            "drop_location": {"raw_text": "Whitefield", "confidence": "high", "is_correction": False},
        },
    })
    assert b.pending_confirmation_field == "same_location_check"

    b = turn(b, "yes, that is intentional", {
        "intent": "confirmation_yes", "fields": {},
    })
    assert b.same_location_confirmed is True
    assert b.pending_confirmation_field is None
    print("OK: matching pickup/drop is confirmed once and does not loop.")


def test_same_location_no_reopens_drop_location():
    b = BookingState(session_id="t_same_location_no")
    b.fields["pickup_location"] = graph.FieldValue("Whitefield", "Whitefield", confirmed=True)
    b.fields["drop_location"] = graph.FieldValue("Whitefield", "Whitefield", confirmed=True)
    b.pending_confirmation_field = "same_location_check"
    b = turn(b, "no", {"intent": "confirmation_no", "fields": {}})
    assert "drop_location" not in b.fields
    assert b.pending_confirmation_field is None
    print("OK: declining matching locations asks for the drop location again.")


def test_unserviceable_location_rejected():
    b = BookingState(session_id="t4")
    b = turn(b, "from Timbuktu to Whitefield", {
        "intent": "information",
        "fields": {
            "pickup_location": {"raw_text": "Timbuktu", "confidence": "high", "is_correction": False},
            "drop_location": {"raw_text": "Whitefield", "confidence": "high", "is_correction": False},
        },
    })
    assert "pickup_location" not in b.fields
    assert "drop_location" in b.fields
    print("OK: unserviceable pickup rejected while valid drop still accepted.")


def test_oversized_load_flagged():
    b = BookingState(session_id="t5")
    b = turn(b, "I need to move an entire factory", {
        "intent": "information",
        "fields": {"load_description": {"raw_text": "an entire factory", "confidence": "high", "is_correction": False}},
    })
    assert "load_description" not in b.fields
    assert "load_description" in b.validation_errors
    print("OK: oversized load flagged as out-of-scope instead of silently accepted.")


def test_silence_does_not_corrupt_state_and_escalates():
    b = BookingState(session_id="t6")
    b.fields_snapshot_before = dict(b.fields)
    for _ in range(3):
        b = turn(b, "", {"intent": "unclear_or_silence", "fields": {}})
    assert b.consecutive_unclear_turns == 3
    graph_state = {"booking": b, "intent": "unclear_or_silence", "off_topic_note": "",
                    "raw_fields": {}, "action": "", "action_context": {}, "reply": ""}
    graph.node_decide(graph_state)
    assert graph_state["action"] == "handle_repeated_silence"
    print("OK: repeated silence escalates action instead of looping forever.")


def test_off_topic_does_not_block_progress():
    b = BookingState(session_id="t7")
    b = turn(b, "by the way do you guys handle fragile items carefully?", {
        "intent": "off_topic",
        "off_topic_note": "asked whether fragile items are handled carefully",
        "fields": {},
    })
    assert b.stage != Stage.COMPLETE
    print("OK: off-topic question handled without derailing required-field collection.")


if __name__ == "__main__":
    test_ambiguous_load_not_accepted_blindly()
    test_correction_overwrites_and_flags_confirm_back()
    test_past_date_rejected()
    test_today_allowed_and_explicit_past_date_rejected()
    test_same_location_requires_one_explicit_confirmation()
    test_same_location_no_reopens_drop_location()
    test_unserviceable_location_rejected()
    test_oversized_load_flagged()
    test_silence_does_not_corrupt_state_and_escalates()
    test_off_topic_does_not_block_progress()
    print("\nAll offline control-flow tests passed.")
