from __future__ import annotations

import json
from pathlib import Path

import pytest

from clawbio.contract_alerts import (
    CONTRACT_ALERT_LOG_SCHEMA,
    CONTRACT_ALERT_SCHEMA,
    append_contract_alert_log,
    make_contract_alert,
    normalise_contract_alerts,
    render_contract_alerts,
)


def test_make_contract_alert_sanitises_and_clamps():
    home_path = str(Path.home() / "secret" / "sample.vcf")
    alert = make_contract_alert(
        kind="planner.intent_input_mismatch",
        message="x" * 250,
        expected="genotype input",
        observed=f"{home_path} token=super-secret-value",
        evidence=[
            home_path,
            "api_key=abc123",
            "sk-1234567890abcdef",
            "extra1",
            "extra2",
            "extra3",
        ],
        remedies=[
            {"label": "Use affinity-proteomics", "skill": "affprot"},
            {"label": "Show compatible skills", "action": "list-compatible-skills"},
            {"label": "Invalid both", "skill": "a", "action": "b"},
        ],
    )

    assert alert["schema"] == CONTRACT_ALERT_SCHEMA
    assert len(alert["message"]) <= 200
    assert str(Path.home()) not in json.dumps(alert)
    assert "super-secret-value" not in json.dumps(alert)
    assert "abc123" not in json.dumps(alert)
    assert "sk-1234567890abcdef" not in json.dumps(alert)
    assert len(alert["evidence"]) == 5
    assert alert["remedies"] == [
        {"label": "Use affinity-proteomics", "skill": "affprot"},
        {"label": "Show compatible skills", "action": "list-compatible-skills"},
    ]


def test_normalise_contract_alerts_drops_invalid_entries():
    alerts = normalise_contract_alerts(
        [
            {"severity": "warning", "kind": "other", "message": "Valid"},
            {"severity": "loud", "kind": "other", "message": "Invalid severity"},
            {"severity": "warning", "kind": "unknown.kind", "message": "Invalid kind"},
            {"severity": "warning", "kind": "other", "message": ""},
            "not a dict",
        ]
    )

    assert [alert["message"] for alert in alerts] == ["Valid"]


def test_make_contract_alert_reports_invalid_fields():
    with pytest.raises(ValueError, match="invalid contract alert kind"):
        make_contract_alert(kind="not.a.kind", message="Bad kind")

    with pytest.raises(ValueError, match="invalid contract alert severity"):
        make_contract_alert(kind="other", message="Bad severity", severity="urgent")

    with pytest.raises(ValueError, match="invalid contract alert message"):
        make_contract_alert(kind="other", message="")


def test_render_contract_alerts_sorts_by_severity():
    rendered = render_contract_alerts(
        [
            {"severity": "info", "kind": "other", "message": "Later"},
            {"severity": "error", "kind": "skill.state_mismatch", "message": "First"},
            {"severity": "warning", "kind": "planner.missing_required_slot", "message": "Middle"},
        ]
    )

    assert rendered.splitlines()[0] == "Error: state mismatch"
    assert "Warning: missing required slot" in rendered
    assert rendered.endswith("Later")


def test_append_contract_alert_log_writes_sanitised_jsonl(tmp_path: Path):
    alert = make_contract_alert(
        kind="skill.state_mismatch",
        message="State mismatch",
        expected="request state_id",
        observed="recomputed state_id",
        blocking=True,
    )

    count = append_contract_alert_log(
        tmp_path / "contract_alerts.jsonl",
        [alert],
        run_id="affprot_20260525_141200",
        skill="affprot",
        intent_id="top-proteins",
        timestamp="2026-05-25T14:12:00+00:00",
    )

    assert count == 1
    line = (tmp_path / "contract_alerts.jsonl").read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["schema"] == CONTRACT_ALERT_LOG_SCHEMA
    assert record["run_id"] == "affprot_20260525_141200"
    assert record["skill"] == "affprot"
    assert record["intent_id"] == "top-proteins"
    assert record["alert"]["kind"] == "skill.state_mismatch"


def test_append_contract_alert_log_allows_pre_run_alerts(tmp_path: Path):
    alert = make_contract_alert(
        kind="planner.unregistered_skill",
        message="The selected route points at an unregistered skill.",
        severity="error",
        blocking=True,
    )

    count = append_contract_alert_log(
        tmp_path / "contract_alerts.jsonl",
        [alert],
        timestamp="2026-05-25T14:13:00+00:00",
    )

    assert count == 1
    record = json.loads((tmp_path / "contract_alerts.jsonl").read_text(encoding="utf-8"))
    assert record["schema"] == CONTRACT_ALERT_LOG_SCHEMA
    assert record["timestamp"] == "2026-05-25T14:13:00+00:00"
    assert record["alert"]["kind"] == "planner.unregistered_skill"
    assert "run_id" not in record
    assert "skill" not in record
    assert "intent_id" not in record
