"""
The conversation state machine, built with LangGraph.

Flow per turn:

    perceive  -->  merge_and_validate  -->  decide_next_action  -->  (conditional)
                                                                        |-- finalize   (booking complete)
                                                                        `-- respond    (say something, keep going)

`perceive` is the only node that calls the LLM for *understanding*.
`respond` is the only node that calls the LLM for *speaking*.
`merge_and_validate` and `decide_next_action` are pure Python -- this is a
deliberate choice: the actual booking-correctness logic (what counts as
missing, what counts as an error, when we're allowed to finalize) must be
deterministic and unit-testable, not left to LLM judgement call-to-call.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from . import llm, validation
from .state import (
    CONFIRM_BACK_FIELDS,
    BookingState,
    FieldValue,
    Stage,
)

LOCATION_FIELDS = {"pickup_location", "drop_location"}


class GraphState(TypedDict):
    booking: BookingState
    intent: str
    off_topic_note: str
    raw_fields: dict[str, dict]
    action: str
    action_context: dict[str, Any]
    reply: str


# ---------------------------------------------------------------------- #
# Node 1: perceive
# ---------------------------------------------------------------------- #
def node_perceive(state: GraphState) -> GraphState:
    booking = state["booking"]
    analysis = llm.extract_turn(booking.history, booking.known_fields_text())

    state["intent"] = analysis.get("intent", "unclear_or_silence")
    state["off_topic_note"] = analysis.get("off_topic_note", "")
    state["raw_fields"] = analysis.get("fields", {})
    return state


# ---------------------------------------------------------------------- #
# Node 2: merge_and_validate
# ---------------------------------------------------------------------- #
def _resolve_field(name: str, raw_text: str) -> tuple[str | None, str | None, str | None]:
    """Returns (resolved_value, error, suggested_extra_note)."""
    if name in LOCATION_FIELDS:
        value, err = validation.validate_location(name, raw_text)
        return value, err, None
    if name == "date":
        value, err = validation.resolve_date(raw_text)
        return value, err, None
    if name == "country_code":
        value, err = validation.normalize_country_code(raw_text)
        return value, err, None
    if name == "contact_number":
        value, err = validation.normalize_phone(raw_text)
        return value, err, None
    if name == "load_description":
        vehicle, err = validation.check_load_feasibility(raw_text)
        return raw_text, err, vehicle
    # time, special_instructions: free text, no hard validation
    return raw_text, None, None


def node_merge_and_validate(state: GraphState) -> GraphState:
    booking = state["booking"]
    booking.turn_count += 1
    booking.validation_errors.clear()
    intent = state["intent"]
    raw_fields: dict[str, dict] = state.get("raw_fields", {})

    # --- silence / unclear tracking ---
    if intent == "unclear_or_silence":
        booking.consecutive_unclear_turns += 1
    else:
        booking.consecutive_unclear_turns = 0

    if intent in ("off_topic", "question"):
        booking.consecutive_off_topic_turns += 1
    else:
        booking.consecutive_off_topic_turns = 0

    # --- confirmation replies (yes/no to a confirm-back or summary) ---
    if (
        intent == "confirmation_yes"
        and booking.pending_confirmation_field
        and booking.pending_confirmation_field != "same_location_check"
    ):
        f = booking.pending_confirmation_field
        if f in booking.fields:
            booking.fields[f].confirmed = True
        booking.pending_confirmation_field = None

    if (
        intent == "confirmation_no"
        and booking.pending_confirmation_field
        and booking.pending_confirmation_field != "same_location_check"
    ):
        f = booking.pending_confirmation_field
        booking.fields.pop(f, None)
        booking.pending_confirmation_field = None

    if intent == "confirmation_yes" and booking.stage == Stage.CONFIRMING_SUMMARY:
        booking.stage = Stage.COMPLETE
        booking.ended = True

    if intent == "confirmation_no" and booking.stage == Stage.CONFIRMING_SUMMARY:
        booking.stage = Stage.COLLECTING

    # --- merge extracted fields ---
    for name, info in raw_fields.items():
        raw_text = (info.get("raw_text") or "").strip()
        if not raw_text:
            continue
        confidence = info.get("confidence", "low")
        is_correction = bool(info.get("is_correction", False))

        if confidence == "low":
            booking.ambiguous_fields[name] = raw_text
            continue

        # confident value -> resolve/validate
        if name == "contact_number":
            country = booking.fields.get("country_code")
            value, err = validation.normalize_phone(
                raw_text, country.value if country else None
            )
            extra_note = None
        else:
            value, err, extra_note = _resolve_field(name, raw_text)
        booking.ambiguous_fields.pop(name, None)

        if err:
            # Don't destroy a previously-good value on a failed correction attempt.
            booking.validation_errors[name] = err
            continue

        # Models and speech recognition occasionally repeat a previously
        # captured value. Keep the original field instead of treating this as
        # a new correction or asking the user to confirm it again.
        if (
            name in booking.fields
            and booking.fields[name].value == value
            and not is_correction
        ):
            continue

        was_confirmed_before = (
            name in booking.fields and booking.fields[name].confirmed
        )
        if is_correction and name in booking.fields:
            booking.corrections_log.append({
                "field": name,
                "old": booking.fields[name].value,
                "new": value,
                "turn": booking.turn_count,
            })

        needs_reconfirm = name in CONFIRM_BACK_FIELDS
        booking.fields[name] = FieldValue(
            raw_text=raw_text,
            value=value,  # type: ignore[arg-type]
            confidence=confidence,
            source_turn=booking.turn_count,
            confirmed=not needs_reconfirm,  # critical fields start unconfirmed
        )
        if name in LOCATION_FIELDS:
            # A changed location makes any earlier same-location confirmation
            # inapplicable; re-check the pair once both are known.
            booking.same_location_confirmed = False
        if needs_reconfirm and (is_correction or not was_confirmed_before):
            booking.pending_confirmation_field = name

        if extra_note and "suggested_vehicle" not in booking.fields:
            booking.fields["suggested_vehicle"] = FieldValue(
                raw_text=extra_note, value=extra_note, confirmed=True
            )

    # --- same pickup/drop location check (not a per-field validator,
    # since it depends on two fields together) ---
    if (
        "pickup_location" in booking.fields
        and "drop_location" in booking.fields
        and booking.fields["pickup_location"].value == booking.fields["drop_location"].value
        and not booking.same_location_confirmed
    ):
        if booking.pending_confirmation_field == "same_location_check":
            if intent == "confirmation_yes":
                booking.same_location_confirmed = True
                booking.pending_confirmation_field = None
            elif intent == "confirmation_no":
                # Let them re-say either location; drop the one more likely
                # to have been the mistake (drop_location) and ask again.
                booking.fields.pop("drop_location", None)
                booking.pending_confirmation_field = None
        elif booking.pending_confirmation_field is None:
            booking.pending_confirmation_field = "same_location_check"

    return state


# ---------------------------------------------------------------------- #
# Node 3: decide_next_action
# ---------------------------------------------------------------------- #
def node_decide(state: GraphState) -> GraphState:
    booking = state["booking"]

    if booking.ended:
        state["action"] = "complete"
        state["action_context"] = {"summary": booking.to_summary_dict()}
        return state

    if booking.consecutive_unclear_turns >= 3:
        state["action"] = "handle_repeated_silence"
        state["action_context"] = {"attempts": booking.consecutive_unclear_turns}
        return state

    if state["intent"] == "unclear_or_silence":
        state["action"] = "handle_unclear"
        if booking.pending_confirmation_field:
            state["action_context"] = {
                "pending_field": booking.pending_confirmation_field,
            }
        else:
            missing = booking.missing_required()
            state["action_context"] = {
                "next_missing_field": missing[0] if missing else None,
            }
        return state

    if booking.validation_errors:
        field, message = next(iter(booking.validation_errors.items()))
        state["action"] = "handle_violation"
        state["action_context"] = {"field": field, "message": message}
        return state

    if booking.ambiguous_fields:
        field, raw_text = next(iter(booking.ambiguous_fields.items()))
        state["action"] = "clarify_ambiguous"
        state["action_context"] = {"field": field, "heard": raw_text}
        return state

    if state["intent"] in ("off_topic", "question") and state.get("off_topic_note"):
        next_missing = booking.missing_required()
        state["action"] = "handle_off_topic"
        state["action_context"] = {
            "user_said": state["off_topic_note"],
            "next_missing_field": next_missing[0] if next_missing else None,
        }
        return state

    if booking.pending_confirmation_field:
        f = booking.pending_confirmation_field
        if f == "same_location_check":
            state["action"] = "confirm_same_location"
            state["action_context"] = {"location": booking.fields["pickup_location"].value}
        else:
            state["action"] = "confirm_field"
            state["action_context"] = {"field": f, "value": booking.fields[f].value}
        return state

    missing = booking.missing_required()
    if missing:
        booking.stage = Stage.COLLECTING
        state["action"] = "ask_missing"
        state["action_context"] = {"field": missing[0]}
        return state

    if booking.stage != Stage.CONFIRMING_SUMMARY:
        booking.stage = Stage.CONFIRMING_SUMMARY
        state["action"] = "present_summary"
        state["action_context"] = {"summary": booking.to_summary_dict()}
        return state

    # Already in CONFIRMING_SUMMARY but got a non-yes/no reply (e.g. they
    # asked to change something instead of a plain yes/no).
    state["action"] = "clarify_confirmation"
    state["action_context"] = {"summary": booking.to_summary_dict()}
    return state


def route_after_decision(state: GraphState) -> str:
    return "finalize" if state["action"] == "complete" else "respond"


# ---------------------------------------------------------------------- #
# Node 4a: respond (LLM prose)
# ---------------------------------------------------------------------- #
def node_respond(state: GraphState) -> GraphState:
    booking = state["booking"]
    reply = llm.generate_reply(
        state["action"], state["action_context"], booking.known_fields_text()
    )
    state["reply"] = reply
    booking.history.append({"role": "assistant", "content": reply})
    return state


# ---------------------------------------------------------------------- #
# Node 4b: finalize (deterministic, no LLM needed)
# ---------------------------------------------------------------------- #
def node_finalize(state: GraphState) -> GraphState:
    booking = state["booking"]
    summary_lines = "\n".join(
        f"- {k.replace('_', ' ').title()}: {v}"
        for k, v in booking.to_summary_dict().items()
    )
    reply = (
        "Great, your booking is confirmed! Here's the final summary:\n"
        f"{summary_lines}\n"
        "We'll notify you once a driver is assigned."
    )
    state["reply"] = reply
    booking.history.append({"role": "assistant", "content": reply})
    return state


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("perceive", node_perceive)
    graph.add_node("merge_and_validate", node_merge_and_validate)
    graph.add_node("decide", node_decide)
    graph.add_node("respond", node_respond)
    graph.add_node("finalize", node_finalize)

    graph.set_entry_point("perceive")
    graph.add_edge("perceive", "merge_and_validate")
    graph.add_edge("merge_and_validate", "decide")
    graph.add_conditional_edges(
        "decide", route_after_decision, {"respond": "respond", "finalize": "finalize"}
    )
    graph.add_edge("respond", END)
    graph.add_edge("finalize", END)
    return graph.compile()


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def run_turn(booking: BookingState, user_message: str) -> BookingState:
    """Runs one turn of the conversation against the compiled graph and
    mutates+returns the booking state (history included)."""
    booking.history.append({"role": "user", "content": user_message})

    graph_state: GraphState = {
        "booking": booking,
        "intent": "",
        "off_topic_note": "",
        "raw_fields": {},
        "action": "",
        "action_context": {},
        "reply": "",
    }
    result = get_graph().invoke(graph_state)
    return result["booking"]
