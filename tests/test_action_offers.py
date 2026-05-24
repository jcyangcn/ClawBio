from __future__ import annotations

import json
from pathlib import Path

from bot.action_offers import (
    execute_stored_action,
    extract_action_offer,
    is_pending_action_expired,
    load_bundle_fields,
    make_pending_action_entry,
    parse_action_reply,
    render_action_offer,
    render_workflow_state_header,
)


def _demo_actions() -> list[dict]:
    return [
        {
            "action_id": "show-demo-report",
            "label": "Show demo report",
            "kind": "navigation",
            "shell_line": "example unsafe text that should not be executed",
            "request": {
                "schema": "example.skill_request.v1",
                "mode": "report",
                "report_id": "demo",
            },
            "requires_confirmation": False,
            "timeout_secs": 123,
            "expected_artifacts": ["report.md"],
        },
        {
            "action_id": "prepare-resource",
            "label": "Prepare local resource",
            "kind": "request",
            "request": {
                "schema": "example.skill_request.v1",
                "mode": "prepare",
                "resource": "demo",
            },
            "requires_confirmation": True,
        },
    ]


def test_render_action_offer_lists_choices_and_safe_refresh_hint():
    rendered = render_action_offer(_demo_actions())
    assert "I can do the next step for you:" in rendered
    assert "1. Show demo report (safe refresh)" in rendered
    assert "2. Prepare local resource" in rendered
    assert "Reply with `1`, `2`" in rendered


def test_render_action_offer_adds_state_header_and_estimate():
    actions = [{**_demo_actions()[0], "estimate": "~5s"}]
    rendered = render_action_offer(
        actions,
        workflow_state={
            "lifecycle": "ready",
            "state_label": "demo-ready",
        },
    )

    assert rendered.startswith("State: ready — demo-ready\n")
    assert "1. Show demo report (~5s)" in rendered
    assert "(safe refresh)" not in rendered


def test_render_workflow_state_header_requires_explicit_lifecycle():
    assert render_workflow_state_header({"state_label": "implicit-ready"}) == ""
    assert render_workflow_state_header({"lifecycle": "expired"}) == "State: expired"


def test_render_action_offer_can_render_state_without_actions():
    rendered = render_action_offer(
        [],
        workflow_state={
            "lifecycle": "expired",
            "state_label": "stale-action-request",
        },
    )

    assert rendered == "State: expired — stale-action-request"


def test_extract_action_offer_requires_structured_request():
    actions = _demo_actions() + [
        {
            "action_id": "shell-only",
            "label": "Do not offer shell-only action",
            "shell_line": "example unsafe text that should not be executed",
        }
    ]
    assert extract_action_offer({"suggested_actions": actions}) == _demo_actions()


def test_parse_action_reply_matches_numeric_choice_without_confirming():
    parsed = parse_action_reply("1", _demo_actions())
    assert parsed["kind"] == "matched"
    assert parsed["action"]["action_id"] == "show-demo-report"
    assert parsed["confirmed"] is False


def test_parse_action_reply_matches_yes_with_index_as_confirmed():
    parsed = parse_action_reply("yes 2", _demo_actions())
    assert parsed["kind"] == "matched"
    assert parsed["action"]["action_id"] == "prepare-resource"
    assert parsed["confirmed"] is True


def test_parse_action_reply_matches_yes_with_single_action_as_confirmed():
    parsed = parse_action_reply("please show me", [_demo_actions()[0]])
    assert parsed["kind"] == "matched"
    assert parsed["action"]["action_id"] == "show-demo-report"
    assert parsed["confirmed"] is True


def test_parse_action_reply_marks_plain_show_me_ambiguous_for_multiple_actions():
    parsed = parse_action_reply("please show me", _demo_actions())
    assert parsed["kind"] == "ambiguous"


def test_parse_action_reply_matches_exact_label():
    parsed = parse_action_reply("Prepare local resource", _demo_actions())
    assert parsed["kind"] == "matched"
    assert parsed["action"]["action_id"] == "prepare-resource"


def test_parse_action_reply_does_not_match_generic_single_word_fragment():
    actions = [
        {
            "action_id": "show-guide",
            "label": "Show guide",
            "request": {
                "schema": "example.skill_request.v1",
                "mode": "guide",
            },
        }
    ]

    assert parse_action_reply("show", actions)["kind"] == "none"


def test_parse_action_reply_handles_cancel():
    parsed = parse_action_reply("never mind", _demo_actions())
    assert parsed["kind"] == "cancel"


def test_pending_action_entry_expires_after_ttl():
    entry = make_pending_action_entry(
        skill="example-actions",
        actions=_demo_actions(),
        timestamp=100.0,
    )
    assert is_pending_action_expired(entry, now_ts=130.0, ttl_seconds=60) is False
    assert is_pending_action_expired(entry, now_ts=161.0, ttl_seconds=60) is True


def test_execute_stored_action_materializes_request_and_uses_runner(tmp_path: Path):
    pending = make_pending_action_entry(
        skill="example-actions",
        actions=_demo_actions(),
    )
    action = _demo_actions()[0]
    captured: dict[str, object] = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        request_path = Path(str(kwargs["input_path"]))
        captured["request_payload"] = json.loads(request_path.read_text(encoding="utf-8"))
        return {"success": True, "output_dir": str(kwargs["output_dir"])}

    result = execute_stored_action(
        pending,
        action,
        runner=fake_runner,
        output_root=tmp_path,
    )

    assert result["success"] is True
    assert captured["skill_name"] == "example-actions"
    assert captured["demo"] is False
    assert captured["timeout"] == 123
    assert captured["request_payload"] == action["request"]
    assert str(captured["output_dir"]).startswith(str(tmp_path / "example-actions_"))


def test_load_bundle_fields_promotes_structured_chat_fields(tmp_path: Path):
    payload = {
        "chat_summary_lines": ["The skill found one follow-up action."],
        "suggested_actions": _demo_actions(),
        "workflow_state": {
            "state_schema": "example.workflow_state.v1",
            "state_id": "sha256:abc",
            "lifecycle": "ready",
            "state_label": "demo-ready",
        },
        "preferred_artifacts": [{"path": "generated/demo.png"}],
        "report_md": "# Embedded report\n",
    }
    (tmp_path / "result.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    (tmp_path / "report.md").write_text("# Fallback report\n", encoding="utf-8")

    fields = load_bundle_fields(tmp_path)

    assert fields["skill_result_json"] == payload
    assert fields["chat_summary_lines"] == ["The skill found one follow-up action."]
    assert fields["suggested_actions"] == _demo_actions()
    assert fields["workflow_state"]["state_id"] == "sha256:abc"
    assert fields["preferred_artifacts"] == [{"path": "generated/demo.png"}]
    assert fields["report_md"] == "# Embedded report\n"
