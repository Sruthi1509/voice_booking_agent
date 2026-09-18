"""
Business-rule validation: the "negative path" logic that is independent
of the LLM. Kept deterministic and testable on purpose -- we do not want
correctness of "is this date in the past" to depend on an LLM's mood.
"""

from __future__ import annotations

import difflib
import re
from datetime import datetime, timedelta
from typing import Optional

import dateparser
import dateparser.search

# --- Service area (demo scope) --------------------------------------------
# In a real system this would be a geocoding + service-polygon lookup.
# For the assessment we use a fixed serviceable-locality list so that
# "unserviceable route" is something we can actually detect and explain.
SERVICEABLE_AREAS = [
    "koramangala", "whitefield", "indiranagar", "hsr layout", "btm layout",
    "electronic city", "marathahalli", "jayanagar", "jp nagar", "yelahanka",
    "hebbal", "malleswaram", "rajajinagar", "banashankari", "bellandur",
    "sarjapur road", "kr puram", "mg road", "domlur", "richmond town",
    "vijayanagar", "basavanagudi", "cv raman nagar", "hennur",
]

# Keywords that signal a load far beyond what a booking-app vehicle fleet
# (two-wheeler -> mini-van -> mini-truck, matching Porter's tiers) can carry.
OVERSIZED_LOAD_PATTERNS = [
    r"\bcontainer\b", r"\bfull truckload\b", r"\b\d{2,}\s*tons?\b",
    r"\bentire house\b", r"\bwhole house\b", r"\bfactory\b",
    r"\bwarehouse\b", r"\bshipping container\b",
]

VEHICLE_TIERS = [
    # (keyword patterns, vehicle label, rough capacity note)
    (["bike", "scooter", "envelope", "document", "small bag", "couple of boxes",
      "few boxes", "one box"], "Two-wheeler", "up to ~20kg"),
    (["fridge", "washing machine", "sofa", "1bhk", "few pieces of furniture",
      "appliance", "study table", "mattress"], "Mini-van (Tata Ace class)", "up to ~750kg"),
    (["2bhk", "3bhk", "house shift", "moving house", "multiple rooms",
      "office shift", "several furniture"], "Mini-truck", "up to ~1500kg"),
]


def find_serviceable_match(raw_location: str) -> Optional[str]:
    """Fuzzy-match a (possibly mis-transcribed) locality name against the
    serviceable list. Returns the canonical name or None if unserviceable."""
    loc = raw_location.lower().strip()
    for area in SERVICEABLE_AREAS:
        if area in loc or loc in area:
            return area.title()
    close = difflib.get_close_matches(loc, SERVICEABLE_AREAS, n=1, cutoff=0.72)
    return close[0].title() if close else None


def validate_location(field_name: str, raw_value: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (resolved_value_or_None, error_message_or_None)."""
    match = find_serviceable_match(raw_value)
    if match:
        return match, None
    return None, (
        f"'{raw_value}' doesn't match any area we currently service. "
        f"Could you confirm the locality, or provide a nearby well-known area?"
    )


def resolve_date(raw_text: str, reference_dt: Optional[datetime] = None) -> tuple[Optional[str], Optional[str]]:
    """Resolve relative/natural date text ('tomorrow', 'next Monday', '5th Dec')
    into an ISO date, relative to `reference_dt` (defaults to now).
    Returns (iso_date_or_None, error_message_or_None)."""
    reference_dt = reference_dt or datetime.now()
    # Resolve a date without a year into the current year. This lets us
    # reject a date that has already passed rather than silently scheduling
    # it for the same day next year (e.g. "15 September" on 18 September).
    settings = {"RELATIVE_BASE": reference_dt, "PREFER_DATES_FROM": "current_period"}

    # search_dates copes much better than a plain parse() with phrases like
    # "tomorrow evening" or "next Monday around noon" where a time-of-day
    # phrase is glued onto the date phrase -- we only care about the date
    # part here (time is captured/validated separately).
    matches = dateparser.search.search_dates(raw_text, settings=settings)
    parsed = None
    if matches:
        # Prefer the longest matched fragment (usually the most specific one).
        _, parsed = max(matches, key=lambda m: len(m[0]))
    else:
        parsed = dateparser.parse(raw_text, settings=settings)

    if not parsed:
        return None, f"I couldn't understand '{raw_text}' as a date. Could you say it differently?"

    parsed_date = parsed.date()
    today = reference_dt.date()

    if parsed_date <= today:
        return None, (
            f"That date ({parsed_date.isoformat()}) is in the past — "
            f"Please schedule a date after today; {parsed_date.isoformat()} is not available."
        )
    if parsed_date > today + timedelta(days=60):
        # Not a hard error -- just flag, most booking apps only allow near-term slots.
        return None, (
            f"{parsed_date.isoformat()} is quite far out — we currently only take "
            f"bookings within the next 60 days. Could you pick a nearer date?"
        )
    return parsed_date.isoformat(), None


def check_load_feasibility(raw_description: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (suggested_vehicle_or_None, error_message_or_None).
    error is set only when the load is likely beyond any vehicle we offer."""
    text = raw_description.lower()

    for pattern in OVERSIZED_LOAD_PATTERNS:
        if re.search(pattern, text):
            return None, (
                "That load sounds larger than what our largest vehicle "
                "(mini-truck, ~1500kg) can carry. Could you clarify the exact "
                "items, or split this into multiple bookings?"
            )

    for keywords, vehicle, _capacity in VEHICLE_TIERS:
        if any(k in text for k in keywords):
            return vehicle, None

    # Unknown/ambiguous load description -- not an error, just no auto-suggestion.
    return None, None


def normalize_phone(raw_text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract a plausible phone number from noisy STT text.
    STT frequently renders numbers as words ('nine eight seven...') or with
    stray words/pauses mixed in, so we strip non-digits and validate length."""
    digits = re.sub(r"\D", "", raw_text)
    # Handle spelled-out digits STT sometimes fails to convert.
    word_to_digit = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    }
    if len(digits) < 10:
        words = re.findall(r"[a-zA-Z]+", raw_text.lower())
        spelled = "".join(word_to_digit[w] for w in words if w in word_to_digit)
        if len(spelled) >= 10:
            digits = spelled

    if len(digits) == 10:
        return digits, None
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:], None
    return None, (
        f"I heard '{raw_text}', which doesn't look like a complete 10-digit "
        f"phone number. Could you repeat it, ideally digit by digit?"
    )
