"""Structured contract-discrepancy alerts for ClawBio.

Contract alerts are not biomedical findings. They describe places where the
chosen route, input, state, policy, or schema does not line up with the
declared ClawBio contracts.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTRACT_ALERT_SCHEMA = "clawbio.contract_alert.v1"
CONTRACT_ALERT_LOG_SCHEMA = "clawbio.contract_alert_log.v1"

ALERT_SEVERITIES = ("error", "warning", "info")
ALERT_KINDS = {
    "planner.intent_input_mismatch",
    "planner.missing_required_slot",
    "planner.demo_requires_explicit_request",
    "planner.unregistered_skill",
    "runner.descriptor_security_skip",
    "runner.input_contract_mismatch",
    "skill.state_mismatch",
    "skill.version_drift",
    "skill.missing_required_input",
    "policy.remote_execution_requires_consent",
    "other",
}

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_SEVERITY_LABEL = {"error": "Error", "warning": "Warning", "info": "Info"}
_MAX_MESSAGE_CHARS = 200
_MAX_FIELD_CHARS = 120
_MAX_EVIDENCE_ITEMS = 5
_MAX_EVIDENCE_CHARS = 100
_MAX_REMEDY_LABEL_CHARS = 80
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"
)
_SECRET_TOKEN_PATTERN = re.compile(
    r"\b(sk-[A-Za-z0-9_-]{8,}|gi_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9_]{8,})\b"
)


def _clamp(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _redact_home_paths(text: str) -> str:
    try:
        home = str(Path.home())
    except Exception:
        return text
    if not home or home == os.sep:
        return text
    return text.replace(home, "~")


def _redact_secrets(text: str) -> str:
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    redacted = _SECRET_TOKEN_PATTERN.sub("<redacted-secret>", redacted)
    return redacted


def _safe_text(value: Any, *, max_chars: int = _MAX_FIELD_CHARS) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    return _clamp(_redact_secrets(_redact_home_paths(text)), max_chars)


def _normalise_remedies(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    remedies: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        skill = item.get("skill")
        action = item.get("action")
        has_skill = isinstance(skill, str) and bool(skill.strip())
        has_action = isinstance(action, str) and bool(action.strip())
        if not isinstance(label, str) or not label.strip() or has_skill == has_action:
            continue
        remedy = {"label": _safe_text(label, max_chars=_MAX_REMEDY_LABEL_CHARS)}
        if has_skill:
            remedy["skill"] = _safe_text(skill, max_chars=_MAX_REMEDY_LABEL_CHARS)
        else:
            remedy["action"] = _safe_text(action, max_chars=_MAX_REMEDY_LABEL_CHARS)
        remedies.append(remedy)
    return remedies


def make_contract_alert(
    *,
    kind: str,
    message: str,
    severity: str = "warning",
    expected: Any | None = None,
    observed: Any | None = None,
    evidence: list[Any] | None = None,
    remedies: list[dict[str, Any]] | None = None,
    blocking: bool = False,
    detail: str | None = None,
) -> dict[str, Any]:
    """Create a normalised contract alert.

    ``expected`` and ``observed`` are display labels. Raw values belong in
    ``evidence`` only after sanitisation.
    """

    if severity not in ALERT_SEVERITIES:
        allowed = ", ".join(ALERT_SEVERITIES)
        raise ValueError(f"invalid contract alert severity: {severity!r}; expected one of {allowed}")
    if kind not in ALERT_KINDS:
        raise ValueError(f"invalid contract alert kind: {kind!r}")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("invalid contract alert message: expected non-empty string")

    alert = {
        "schema": CONTRACT_ALERT_SCHEMA,
        "severity": severity,
        "kind": kind,
        "message": message,
        "blocking": bool(blocking),
    }
    if expected is not None:
        alert["expected"] = expected
    if observed is not None:
        alert["observed"] = observed
    if evidence:
        alert["evidence"] = evidence
    if remedies:
        alert["remedies"] = remedies
    if detail:
        alert["detail"] = detail
    normalised = normalise_contract_alerts([alert])
    if not normalised:
        raise ValueError("invalid contract alert")
    return normalised[0]


def normalise_contract_alerts(value: Any) -> list[dict[str, Any]]:
    """Return valid, sanitised contract alerts; invalid entries are dropped."""

    if not isinstance(value, list):
        return []
    alerts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity")
        kind = item.get("kind")
        message = item.get("message")
        if severity not in ALERT_SEVERITIES:
            continue
        if kind not in ALERT_KINDS:
            continue
        if not isinstance(message, str) or not message.strip():
            continue

        alert: dict[str, Any] = {
            "schema": CONTRACT_ALERT_SCHEMA,
            "severity": severity,
            "kind": kind,
            "message": _safe_text(message, max_chars=_MAX_MESSAGE_CHARS),
            "blocking": bool(item.get("blocking", False)),
        }
        if item.get("schema") not in (None, CONTRACT_ALERT_SCHEMA):
            continue
        for key in ("expected", "observed"):
            if item.get(key) is not None:
                alert[key] = _safe_text(item[key], max_chars=_MAX_FIELD_CHARS)
        if isinstance(item.get("detail"), str) and item["detail"].strip():
            alert["detail"] = _safe_text(item["detail"], max_chars=_MAX_FIELD_CHARS)
        evidence = item.get("evidence")
        if isinstance(evidence, list):
            cleaned = [
                _safe_text(entry, max_chars=_MAX_EVIDENCE_CHARS)
                for entry in evidence[:_MAX_EVIDENCE_ITEMS]
                if str(entry).strip()
            ]
            if cleaned:
                alert["evidence"] = cleaned
        remedies = _normalise_remedies(item.get("remedies"))
        if remedies:
            alert["remedies"] = remedies
        alerts.append(alert)
    return alerts


def sorted_contract_alerts(alerts: Any) -> list[dict[str, Any]]:
    """Return alerts sorted by display severity."""

    return sorted(
        normalise_contract_alerts(alerts),
        key=lambda item: (_SEVERITY_ORDER[item["severity"]], item["kind"], item["message"]),
    )


def render_contract_alerts(alerts: Any) -> str:
    """Render contract alerts as compact chat text."""

    lines: list[str] = []
    for alert in sorted_contract_alerts(alerts):
        kind_label = alert["kind"].split(".", 1)[-1].replace("_", " ")
        lines.append(f"{_SEVERITY_LABEL[alert['severity']]}: {kind_label}")
        lines.append(alert["message"])
        expected = alert.get("expected")
        observed = alert.get("observed")
        if expected or observed:
            parts = []
            if expected:
                parts.append(f"expected {expected}")
            if observed:
                parts.append(f"observed {observed}")
            lines.append("; ".join(parts))
        remedies = alert.get("remedies")
        if isinstance(remedies, list) and remedies:
            labels = [str(item.get("label", "")).strip() for item in remedies if item.get("label")]
            if labels:
                lines.append("Remedies: " + "; ".join(labels))
        lines.append("")
    return "\n".join(lines).strip()


def append_contract_alert_log(
    log_path: str | Path,
    alerts: Any,
    *,
    run_id: str | None = None,
    skill: str | None = None,
    intent_id: str | None = None,
    timestamp: str | None = None,
) -> int:
    """Append sanitised alerts as JSONL and never raise on logging failure.

    ``intent_id`` is the routing intent identifier when an ``INTENTS.json``
    planner picked the route; it is omitted for direct CLI/API invocation.
    Required log fields are ``schema``, ``timestamp``, and ``alert``.
    ``run_id``, ``skill``, and ``intent_id`` may be absent for pre-run alerts.

    v1 uses append-only writes and does not deduplicate or rotate logs.
    Operators may archive the global fallback log manually.
    """

    cleaned = normalise_contract_alerts(alerts)
    if not cleaned:
        return 0
    path = Path(log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")
        count = 0
        with path.open("a", encoding="utf-8") as handle:
            for alert in cleaned:
                record: dict[str, Any] = {
                    "schema": CONTRACT_ALERT_LOG_SCHEMA,
                    "timestamp": stamp,
                    "alert": alert,
                }
                if run_id is not None:
                    record["run_id"] = str(run_id)
                if skill is not None:
                    record["skill"] = str(skill)
                if intent_id is not None:
                    record["intent_id"] = str(intent_id)
                handle.write(json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n")
                count += 1
        return count
    except OSError:
        return 0
