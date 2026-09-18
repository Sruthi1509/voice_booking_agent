"""Groq-backed LLM adapter for booking perception and spoken replies."""

from __future__ import annotations

import json
import os
from typing import Any

from groq import Groq

from .prompts import EXTRACTION_SYSTEM_PROMPT, RESPONSE_SYSTEM_PROMPT

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set. Add it to your environment.")
        _client = Groq(api_key=api_key)
    return _client


_BOOKING_FIELD_NAMES = [
    "pickup_location", "drop_location", "date", "time",
    "load_description", "country_code", "contact_number", "special_instructions",
]

_FIELD_ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "raw_text": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "low"]},
        "is_correction": {"type": "boolean"},
    },
    "required": ["raw_text", "confidence", "is_correction"],
    "additionalProperties": False,
}

_TURN_ANALYSIS_TOOL = {
    "type": "function",
    "function": {
        "name": "record_turn_analysis",
        "description": "Record the structured analysis of the latest user turn.",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": [
                        "information", "correction", "confirmation_yes",
                        "confirmation_no", "question", "off_topic",
                        "unclear_or_silence",
                    ],
                },
                "off_topic_note": {"type": "string"},
                "fields": {
                    "type": "object",
                    "properties": {
                        name: _FIELD_ENTRY_SCHEMA for name in _BOOKING_FIELD_NAMES
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["intent", "fields"],
            "additionalProperties": False,
        },
    },
}


def extract_turn(history: list[dict[str, str]], known_fields_text: str) -> dict[str, Any]:
    """Run the perception step and return the tool-call arguments as a dict."""
    convo_text = "\n".join(f"{h['role']}: {h['content']}" for h in history[-12:])
    user_prompt = (
        f"Fields already known and confirmed so far: {known_fields_text}\n\n"
        f"Conversation so far:\n{convo_text}\n\n"
        "Analyze the LATEST user turn (the final 'user:' line above) and call "
        "record_turn_analysis with the structured result."
    )

    response = _get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        tools=[_TURN_ANALYSIS_TOOL],
        tool_choice={"type": "function", "function": {"name": "record_turn_analysis"}},
        temperature=0,
    )

    try:
        tool_call = response.choices[0].message.tool_calls[0]
        data = json.loads(tool_call.function.arguments)
        if not isinstance(data, dict):
            raise ValueError("Tool arguments must be an object")
    except (AttributeError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return {"intent": "unclear_or_silence", "fields": {}}

    data.setdefault("intent", "unclear_or_silence")
    data.setdefault("fields", {})
    return data


def generate_reply(action: str, context: dict[str, Any], known_fields_text: str) -> str:
    """Run the natural-language response generation step."""
    user_prompt = (
        f"Known fields so far: {known_fields_text}\n"
        f"Action to take this turn: {action}\n"
        f"Context for this action: {json.dumps(context, ensure_ascii=False)}\n\n"
        "Write the assistant's spoken reply now. Keep the meaning of any previous_question, "
        "but do not repeat that previous question word for word."
    )
    response = _get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=300,
    )
    return (response.choices[0].message.content or "").strip()
