"""Permanent security regression tests for the RoboTerri bots.

These encode the two attacks from the security audit as tests that must always
pass: (1) a forged webhook sender must be rejected, and (2) one user's genomic
data must never be reachable from another user's session (identity isolation:
exactly one authenticated identity, no global/first/any fallback).

The functions under test live in bot/security.py (stdlib-only, no flask/openai),
so this runs without the bots' heavy runtime dependencies.
"""

import hashlib
import hmac
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # bot/

from security import (  # noqa: E402
    is_sender_allowed,
    scoped_get,
    verify_whatsapp_signature,
)

SECRET = "test_app_secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- Attack 1: a forged webhook sender must be rejected ---

def test_forged_webhook_signature_rejected():
    body = b'{"from":"attacker","text":{"body":"run pharmgx on the owner genome"}}'
    assert verify_whatsapp_signature(body, "sha256=deadbeef", SECRET) is False
    assert verify_whatsapp_signature(body, None, SECRET) is False
    assert verify_whatsapp_signature(body, "garbage", SECRET) is False
    assert verify_whatsapp_signature(body, _sign(body, "wrong_secret"), SECRET) is False


def test_valid_webhook_signature_accepted():
    body = b'{"from":"+441234567","text":{"body":"hi"}}'
    assert verify_whatsapp_signature(body, _sign(body), SECRET) is True


def test_signature_fails_closed_without_secret():
    # If no app secret is configured, every payload is rejected (fail closed),
    # never silently accepted.
    body = b"{}"
    assert verify_whatsapp_signature(body, _sign(body), "") is False


# --- Attack 2: cross-user genome/data bleed must be impossible ---

def test_no_cross_user_file_bleed():
    store = {"+userA": {"path": "/tmp/A_genome.txt"}}
    # User B requests a skill run; must NOT receive user A's uploaded genome.
    assert scoped_get(store, "+userB") is None
    # User A still reaches their own file.
    assert scoped_get(store, "+userA") == {"path": "/tmp/A_genome.txt"}


def test_scoped_get_has_no_first_or_any_fallback():
    store = {"+userA": {"path": "/tmp/A.txt"}}
    # The patterns the audit flagged (first item / any item / next(iter)) must be
    # gone: an empty or missing identity yields nothing, never an arbitrary entry.
    assert scoped_get(store, "") is None
    assert scoped_get(store, None) is None


# --- Sender allow-list: deny by default ---

def test_sender_allowlist_denies_by_default():
    assert is_sender_allowed("+stranger", allowed=set(), admin="+owner") is False
    assert is_sender_allowed("+owner", allowed=set(), admin="+owner") is True
    assert is_sender_allowed("+friend", allowed={"+friend"}, admin="+owner") is True
    assert is_sender_allowed("", allowed={"+friend"}, admin="+owner") is False


def test_sender_allowlist_explicit_public_optin():
    # Public demo mode must be an explicit choice, never the silent default.
    assert is_sender_allowed("+anyone", allowed=set(), admin=None, allow_all=True) is True
    assert is_sender_allowed("", allowed=set(), admin=None, allow_all=True) is False
