from __future__ import annotations

import json
from pathlib import Path

from clawbio.skill_intents import SCHEMA, plan_skill_intent


def _write_descriptor(skill_dir: Path, descriptor: dict) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "INTENTS.json").write_text(
        json.dumps(descriptor, indent=2) + "\n",
        encoding="utf-8",
    )


def _registered_skill(script_path: Path) -> dict:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("# placeholder\n", encoding="utf-8")
    return {
        "script": script_path,
        "demo_args": ["--demo"],
        "description": "Example skill",
        "allowed_extra_flags": set(),
    }


def test_descriptor_plan_alerts_unregistered_skill(tmp_path: Path):
    _write_descriptor(
        tmp_path / "skills" / "router",
        {
            "schema": SCHEMA,
            "skill": "router",
            "description": "Routes to a missing skill.",
            "routes": [
                {
                    "intent_id": "missing_skill_route",
                    "trigger_terms": ["missing skill route"],
                    "plan": [
                        {
                            "kind": "skill_run",
                            "skill": "not-registered",
                        }
                    ],
                }
            ],
        },
    )

    plan = plan_skill_intent(
        "please run missing skill route",
        requested_skill=None,
        requested_mode=None,
        attachments=[],
        skill_registry={},
        project_root=tmp_path,
    )

    assert plan.status == "needs_registration"
    assert plan.warnings == ["Register not-registered before exposing it for execution."]
    assert plan.contract_alerts[0]["kind"] == "planner.unregistered_skill"
    assert plan.contract_alerts[0]["blocking"] is True


def test_descriptor_plan_alerts_missing_required_slot(tmp_path: Path):
    script_path = tmp_path / "skills" / "example" / "example.py"
    _write_descriptor(
        script_path.parent,
        {
            "schema": SCHEMA,
            "skill": "example",
            "description": "Runs an example gene route.",
            "routes": [
                {
                    "intent_id": "gene_route",
                    "trigger_terms": ["gene route"],
                    "plan": [
                        {
                            "kind": "skill_run",
                            "input_template": {"gene": "{gene_symbol}"},
                            "slots": {
                                "gene_symbol": {
                                    "required": True,
                                    "pattern": r"gene\s+([A-Z0-9]{3,12})",
                                    "ignore_case": False,
                                }
                            },
                        }
                    ],
                }
            ],
        },
    )

    plan = plan_skill_intent(
        "please run gene route",
        requested_skill=None,
        requested_mode=None,
        attachments=[],
        skill_registry={"example": _registered_skill(script_path)},
        project_root=tmp_path,
    )

    assert plan.status == "needs_input"
    assert plan.warnings == ["Missing required slot(s): gene_symbol."]
    assert plan.contract_alerts[0]["kind"] == "planner.missing_required_slot"
    assert plan.contract_alerts[0]["evidence"] == ["slot: gene_symbol"]


def test_legacy_fallback_alerts_implicit_demo_block(tmp_path: Path):
    plan = plan_skill_intent(
        "please run pharmacogenomics",
        requested_skill="pharmgx",
        requested_mode="demo",
        attachments=[],
        skill_registry={},
        project_root=tmp_path,
    )

    assert plan.status == "needs_input"
    assert plan.warnings == ["Demo mode is only planned when the user explicitly asks for a demo."]
    assert plan.contract_alerts[0]["kind"] == "planner.demo_requires_explicit_request"
    assert plan.contract_alerts[0]["blocking"] is True
