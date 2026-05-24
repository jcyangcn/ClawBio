from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_RUNNER_SPEC = importlib.util.spec_from_file_location("clawbio_runner", PROJECT_ROOT / "clawbio.py")
assert _RUNNER_SPEC and _RUNNER_SPEC.loader
clawbio_runner = importlib.util.module_from_spec(_RUNNER_SPEC)
_RUNNER_SPEC.loader.exec_module(clawbio_runner)


def test_run_skill_promotes_structured_result_fields(monkeypatch, tmp_path: Path):
    fake_script = tmp_path / "example_actions.py"
    fake_script.write_text("# placeholder\n", encoding="utf-8")

    monkeypatch.setitem(
        clawbio_runner.SKILLS,
        "example-actions",
        {
            "script": fake_script,
            "demo_args": ["--demo"],
            "description": "Example structured action skill",
            "allowed_extra_flags": set(),
        },
    )

    class Proc:
        returncode = 0
        stdout = "wrapper ok\n"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout, cwd):
        output_dir = Path(cmd[cmd.index("--output") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "report.md").write_text("# Fallback report\n", encoding="utf-8")
        (output_dir / "result.json").write_text(
            json.dumps(
                {
                    "schema": "example.skill_result.v1",
                    "chat_summary_lines": [
                        "The skill prepared a structured response.",
                    ],
                    "preferred_artifacts": ["report.md", "result.json"],
                    "workflow_state": {
                        "state_schema": "example.workflow_state.v1",
                        "state_id": "sha256:abc",
                        "lifecycle": "ready",
                        "state_label": "demo-ready",
                    },
                    "suggested_actions": [
                        {
                            "action_id": "show-demo-report",
                            "label": "Show demo report",
                            "kind": "navigation",
                            "request": {
                                "schema": "example.skill_request.v1",
                                "mode": "report",
                                "report_id": "demo",
                            },
                            "requires_confirmation": False,
                        }
                    ],
                    "report_md": "# Structured Report\n",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return Proc()

    monkeypatch.setattr(clawbio_runner.subprocess, "run", fake_run)

    output_dir = tmp_path / "runner_out"
    result = clawbio_runner.run_skill(
        skill_name="example-actions",
        demo=True,
        output_dir=str(output_dir),
    )

    assert result["success"] is True
    assert result["stdout"] == "wrapper ok\n"
    assert result["result_json_path"] == str(output_dir / "result.json")
    assert result["report_md"] == "# Structured Report\n"
    assert result["chat_summary_lines"] == ["The skill prepared a structured response."]
    assert result["preferred_artifacts"] == ["report.md", "result.json"]
    assert result["workflow_state"]["state_id"] == "sha256:abc"
    assert result["suggested_actions"][0]["action_id"] == "show-demo-report"
    assert result["skill_result_json"]["schema"] == "example.skill_result.v1"
