"""Shared security helpers for the RoboTerri bots.

Stdlib-only (no flask / openai / telegram imports) so it is unit-testable in
isolation and importable from every bot adapter. This module encodes the
identity-isolation invariant: every action is attributable to exactly one
authenticated user, with no global / first / any fallback.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping, Optional, Set


def verify_whatsapp_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    app_secret: str,
) -> bool:
    """Verify a Meta WhatsApp webhook payload signature (``X-Hub-Signature-256``).

    Returns True only if ``app_secret`` is set, ``signature_header`` is a
    well-formed ``sha256=<hex>``, and the HMAC-SHA256 of ``raw_body`` under
    ``app_secret`` matches in constant time. Fails closed (returns False) on any
    missing or malformed input, so an unconfigured secret never silently accepts.
    """
    if not app_secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    sent = signature_header[len("sha256="):].strip()
    if not sent:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sent, expected)


def scoped_get(store: Mapping[str, Any], user_id: Optional[str]) -> Optional[Any]:
    """Return the entry for exactly this user, or None. No first / any fallback.

    This is the identity-isolation invariant in code: a user's data is reachable
    only by their own id. Never iterate the store and return an arbitrary entry
    (the ``for _uid, info in store.items(): break`` / ``next(iter(store))``
    patterns the audit flagged are forbidden in user-facing execution paths).
    """
    if not user_id:
        return None
    return store.get(user_id)


def is_sender_allowed(
    user_id: Optional[str],
    allowed: Set[str],
    admin: Optional[str] = None,
    allow_all: bool = False,
) -> bool:
    """True iff the sender may use the bot. Denies by default (fail closed).

    A sender is allowed when they are the configured ``admin`` or appear in the
    explicit ``allowed`` set. Public access (``allow_all``) must be an explicit
    operator choice, never the silent default. An empty / None ``user_id`` is
    always rejected.
    """
    if not user_id:
        return False
    if allow_all:
        return True
    if admin and user_id == admin:
        return True
    return user_id in allowed
