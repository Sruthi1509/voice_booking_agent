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

# Common speech-to-text confusions for household items being moved.
ITEM_HOMOPHONES = {
    "share": "chair",
    "shares": "chairs",
    "cheer": "chair",
    "cheers": "chairs",
    "sheer": "chair",
    "chear": "chair",
    "chears": "chairs",
    "shair": "chair",
    "sofer": "sofa",
    "sofar": "sofa",
    "bridge": "fridge",
    "frig": "fridge",
    "frige": "fridge",
}

VEHICLE_TIERS = [
    # (keyword patterns, vehicle label, rough capacity note)
    (["bike", "scooter", "envelope", "document", "small bag", "couple of boxes",
      "few boxes", "one box"], "Two-wheeler", "up to ~20kg"),
    (["fridge", "washing machine", "sofa", "chair", "chairs", "table",
      "1bhk", "few pieces of furniture",
      "appliance", "study table", "mattress"], "Mini-van (Tata Ace class)", "up to ~750kg"),
    (["2bhk", "3bhk", "house shift", "moving house", "multiple rooms",
      "office shift", "several furniture"], "Mini-truck", "up to ~1500kg"),
]


# Common speech-to-text confusions for location names in India / service areas.
LOCATION_ALIASES = {
    "vizianagaram": "vijayanagar",
    "vijayanagaram": "vijayanagar",
    "vijaianagar": "vijayanagar",
    "vijayanagra": "vijayanagar",
    "white field": "whitefield",
    "indira nagar": "indiranagar",
    "kormangla": "koramangala",
    "koramangla": "koramangala",
    "hsr": "hsr layout",
    "btm": "btm layout",
    "electronic city": "electronic city",
    "mg road": "mg road",
    "jaya nagar": "jayanagar",
    "jp nagar": "jp nagar",
    "malleswaram": "malleswaram",
    "malleshwaram": "malleswaram",
    "rajaji nagar": "rajajinagar",
    "banashankari": "banashankari",
    "marathalli": "marathahalli",
    "marathahalli": "marathahalli",
    "bellandur": "bellandur",
    "sarjapur": "sarjapur road",
    "kr puram": "kr puram",
}

# Country name/alias to calling code mapping
COUNTRY_NAME_TO_CODE = {
    "india": "+91",
    "indian": "+91",
    "in": "+91",
    "ind": "+91",
    "united states": "+1",
    "us": "+1",
    "usa": "+1",
    "america": "+1",
    "american": "+1",
    "united kingdom": "+44",
    "uk": "+44",
    "britain": "+44",
    "great britain": "+44",
    "england": "+44",
    "canada": "+1",
    "australia": "+61",
    "uae": "+971",
    "united arab emirates": "+971",
    "dubai": "+971",
    "singapore": "+65",
    "germany": "+49",
    "france": "+33",
    "japan": "+81",
    "china": "+86",
}


def find_serviceable_match(raw_location: str) -> Optional[str]:
    """Fuzzy-match a (possibly mis-transcribed) locality name against the
    serviceable list. Returns the canonical name or None if unserviceable."""
    loc = raw_location.lower().strip()
    
    # Check explicit alias mapping first
    if loc in LOCATION_ALIASES:
        target = LOCATION_ALIASES[loc]
        for area in SERVICEABLE_AREAS:
            if area == target or target in area:
                return area.title()
                
    for area in SERVICEABLE_AREAS:
        if area in loc or loc in area:
            return area.title()
    close = difflib.get_close_matches(loc, SERVICEABLE_AREAS, n=1, cutoff=0.55)
    return close[0].title() if close else None


def find_close_location_suggestion(raw_location: str) -> Optional[str]:
    """Find a nearby or phonetically close serviceable location to offer as a suggestion."""
    loc = raw_location.lower().strip()
    if loc in LOCATION_ALIASES:
        return LOCATION_ALIASES[loc].title()
    close = difflib.get_close_matches(loc, SERVICEABLE_AREAS, n=1, cutoff=0.45)
    return close[0].title() if close else None


def validate_location(field_name: str, raw_value: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (resolved_value_or_None, error_message_or_None)."""
    match = find_serviceable_match(raw_value)
    if match:
        return match, None
        
    suggestion = find_close_location_suggestion(raw_value)
    if suggestion:
        return None, (
            f"'{raw_value}' is currently outside our service region. "
            f"Did you mean '{suggestion}'? Or could you provide a nearby area within our service zone?"
        )
        
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

    if parsed_date < today:
        return None, (
            f"That date ({parsed_date.isoformat()}) is in the past — "
            "Sorry, that date has already passed. Please provide today's date or a future date."
        )
    if parsed_date > today + timedelta(days=60):
        # Not a hard error -- just flag, most booking apps only allow near-term slots.
        return None, (
            f"{parsed_date.isoformat()} is quite far out — we currently only take "
            f"bookings within the next 60 days. Could you pick a nearer date?"
        )
    return parsed_date.isoformat(), None


def correct_item_homophones(raw_text: str) -> str:
    """Rewrite likely STT mistakes for items people typically move."""
    words = re.findall(r"[A-Za-z]+|[^A-Za-z]+", raw_text)
    corrected: list[str] = []
    for word in words:
        key = word.lower()
        replacement = ITEM_HOMOPHONES.get(key)
        if replacement:
            corrected.append(replacement if word.islower() else replacement.capitalize())
        else:
            corrected.append(word)
    return "".join(corrected)


def collapse_repeated_phrase(raw_text: str) -> str:
    """Keep a single copy when the same item/phrase is spoken twice."""
    words = [w for w in raw_text.split() if w]
    if not words:
        return raw_text.strip()

    collapsed: list[str] = []
    for word in words:
        if not collapsed or collapsed[-1].lower() != word.lower():
            collapsed.append(word)

    n = len(collapsed)
    if n >= 2 and n % 2 == 0:
        half = n // 2
        first = [w.lower() for w in collapsed[:half]]
        second = [w.lower() for w in collapsed[half:]]
        if first == second:
            collapsed = collapsed[:half]
    return " ".join(collapsed)


def is_repeated_value(existing: str, incoming: str) -> bool:
    """True when incoming is the same fact said again, not a new value."""
    old = collapse_repeated_phrase(existing).strip().lower()
    new = collapse_repeated_phrase(incoming).strip().lower()
    if not old or not new:
        return False
    if old == new:
        return True
    if new == f"{old} {old}":
        return True
    leftover = new.replace(old, " ").strip()
    return leftover in ("", old)


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


def normalize_country_code(raw_text: str) -> tuple[Optional[str], Optional[str]]:
    """Normalize a country name or calling code into standard country calling code format."""
    text = raw_text.strip().lower()
    
    # Check if raw_text matches a country name or alias
    for name, code in COUNTRY_NAME_TO_CODE.items():
        if text == name or f"in {name}" in text or f"from {name}" in text or name in text.split():
            return code, None

    # Check if it's already a numeric country code (e.g. "+91", "91", "1")
    digits = re.sub(r"\D", "", raw_text)
    if 1 <= len(digits) <= 3 and digits != "0":
        return f"+{digits}", None

    return None, (
        f"'{raw_text}' is not a recognized country name or calling code. "
        "Please specify your country (for example, India, United States, UK) or country code."
    )


COUNTRY_PHONE_SPECS: dict[str, tuple[str, tuple[int, ...]]] = {
    # calling_code: (Country Name, (allowed_national_digit_lengths,))
    "91": ("India", (10,)),
    "1": ("United States/Canada", (10,)),
    "44": ("United Kingdom", (10, 11)),
    "61": ("Australia", (9, 10)),
    "971": ("UAE", (9,)),
    "65": ("Singapore", (8,)),
    "49": ("Germany", (10, 11)),
    "33": ("France", (9,)),
    "81": ("Japan", (10,)),
    "86": ("China", (11,)),
    "92": ("Pakistan", (10,)),
    "880": ("Bangladesh", (10,)),
    "94": ("Sri Lanka", (9,)),
    "977": ("Nepal", (10,)),
    "60": ("Malaysia", (9, 10)),
    "62": ("Indonesia", (9, 10, 11)),
    "63": ("Philippines", (10,)),
    "66": ("Thailand", (9,)),
    "82": ("South Korea", (9, 10)),
    "39": ("Italy", (9, 10)),
    "34": ("Spain", (9,)),
    "55": ("Brazil", (10, 11)),
    "52": ("Mexico", (10,)),
    "27": ("South Africa", (9,)),
    "20": ("Egypt", (10,)),
    "234": ("Nigeria", (10,)),
    "7": ("Russia", (10,)),
    "64": ("New Zealand", (8, 9)),
    "31": ("Netherlands", (9,)),
    "41": ("Switzerland", (9,)),
    "46": ("Sweden", (9,)),
}


def normalize_phone(raw_text: str, country_code: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
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

    normalized_code = (country_code or "").lstrip("+")
    if normalized_code:
        if raw_text.strip().startswith("+"):
            if not digits.startswith(normalized_code):
                return None, (
                    f"That number does not start with the country code +{normalized_code}. "
                    "Please provide the complete number including the correct country code."
                )
            national_digits = digits[len(normalized_code):]
        else:
            if digits.startswith(normalized_code) and len(digits) >= len(normalized_code) + 7:
                national_digits = digits[len(normalized_code):]
            else:
                national_digits = digits

        # Strip optional leading zero in national portion (e.g. 09876543210 -> 9876543210)
        if len(national_digits) > 1 and national_digits.startswith("0"):
            national_digits = national_digits[1:]

        # Lookup country specification for exact digit length validation
        spec = COUNTRY_PHONE_SPECS.get(normalized_code)
        if spec:
            country_name, allowed_lengths = spec
            if len(national_digits) not in allowed_lengths:
                fmt_lengths = " or ".join(str(l) for l in allowed_lengths)
                return None, (
                    f"The phone number provided has {len(national_digits)} digits, but {fmt_lengths} digits "
                    f"are required for {country_name} (+{normalized_code}). "
                    "Could you please provide your full phone number?"
                )
            return f"+{normalized_code}{national_digits}", None

        # General fallback validation for any other country code
        if 7 <= len(national_digits) <= 12:
            return f"+{normalized_code}{national_digits}", None

        return None, (
            f"The phone number '{raw_text}' (has {len(national_digits)} digits) is invalid for country code +{normalized_code}. "
            "Please provide a complete, valid phone number."
        )

    if len(digits) == 10:
        return digits, None
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:], None
    return None, (
        f"The number '{raw_text}' is incomplete. Please provide your country name or code first, "
        "then your full phone number."
    )
