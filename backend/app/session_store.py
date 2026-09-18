"""
In-memory session store.

For a one-day assessment this is intentionally simple: a process-local dict
keyed by session_id. Documented as a known limitation in the README -- a
production deployment would back this with Redis (or similar) so state
survives restarts and works across multiple server instances.
"""

from __future__ import annotations

import threading

from .agent.state import BookingState

_lock = threading.Lock()
_sessions: dict[str, BookingState] = {}


def get_or_create(session_id: str) -> BookingState:
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = BookingState(session_id=session_id)
        return _sessions[session_id]


def save(booking: BookingState) -> None:
    with _lock:
        _sessions[booking.session_id] = booking


def reset(session_id: str) -> None:
    with _lock:
        _sessions.pop(session_id, None)
