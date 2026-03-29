from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_DIR.parents[1]
sys.path.insert(0, str(SKILL_DIR))

import bigquery_public as skill  # noqa: E402


_RUNNER_SPEC = importlib.util.spec_from_file_location("clawbio_runner", PROJECT_ROOT / "clawbio.py")
assert _RUNNER_SPEC and _RUNNER_SPEC.loader
clawbio_runner = importlib.util.module_from_spec(_RUNNER_SPEC)
_RUNNER_SPEC.loader.exec_module(clawbio_runner)


def test_validate_read_only_sql_accepts_select_and_with():
    select_sql = "SELECT 1 AS ok;"
    with_sql = """
    -- comment
    WITH example AS (SELECT 1 AS value)
    SELECT value FROM example
    """
    assert skill.validate_read_only_sql(select_sql) == "SELECT 1 AS ok"
    normalized = skill.validate_read_only_sql(with_sql)
    assert normalized.lstrip().startswith("-- comment")
    assert "WITH example" in normalized


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("DELETE FROM dataset.table", "Only read-only SELECT/WITH queries are supported."),
        ("SELECT 1; SELECT 2", "Multiple SQL statements are not supported."),
        ("WITH x AS (SELECT 1) CREATE TABLE t AS SELECT * FROM x", "Unsupported SQL keyword detected"),
    ],
)
def test_validate_read_only_sql_rejects_unsafe_queries(query: str, expected: str):
    with pytest.raises(skill.QueryValidationError, match=expected):
        skill.validate_read_only_sql(query)


def test_parse_scalar_param_valid_types():
    string_param = skill.parse_scalar_param("gene=STRING:TP53")
    int_param = skill.parse_scalar_param("limit=INT64:5")
    float_param = skill.parse_scalar_param("p=FLOAT64:0.01")
    bool_param = skill.parse_scalar_param("strict=BOOL:true")

    assert string_param.value == "TP53"
    assert int_param.value == 5
    assert float_param.value == pytest.approx(0.01)
    assert bool_param.value is True


def test_parse_scalar_param_invalid_format_and_type():
    with pytest.raises(ValueError, match="Expected name=type:value"):
        skill.parse_scalar_param("broken")
    with pytest.raises(ValueError, match="Unsupported parameter type"):
        skill.parse_scalar_param("gene=ARRAY:TP53")


def test_ensure_output_dir_ready_rejects_non_empty_dir(tmp_path: Path):
    output_dir = tmp_path / "occupied"
    output_dir.mkdir()
    (output_dir / "stale.txt").write_text("old", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists and is not empty"):
        skill._ensure_output_dir_ready(output_dir)


def test_execute_query_prefers_python_backend(monkeypatch: pytest.MonkeyPatch):
    expected = skill.QueryExecutionResult(
        backend="python-adc",
        project_id="demo-project",
        location="US",
        query="SELECT 1",
        dry_run=False,
        rows=[{"example": 1}],
        columns=["example"],
        estimated_bytes_processed=10,
        total_bytes_processed=10,
        row_count=1,
        job_id="python-job",
        raw_metadata={"backend": "python-adc"},
    )

    monkeypatch.setattr(skill, "execute_with_python_client", lambda **_: expected)
    monkeypatch.setattr(skill, "execute_with_bq_cli", lambda **_: (_ for _ in ()).throw(AssertionError("CLI fallback should not run")))

    result = skill.execute_query(
        query="SELECT 1",
        location="US",
        max_rows=10,
        max_bytes_billed=1000,
        parameters=[],
        dry_run=False,
    )
    assert result.backend == "python-adc"


def test_execute_query_falls_back_to_bq_cli(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        skill,
        "execute_with_python_client",
        lambda **_: (_ for _ in ()).throw(skill.BigQuerySetupError("ADC unavailable")),
    )
    monkeypatch.setattr(
        skill,
        "execute_with_bq_cli",
        lambda **_: skill.QueryExecutionResult(
            backend="bq-cli",
            project_id="demo-project",
            location="US",
            query="SELECT 1",
            dry_run=True,
            rows=[],
            columns=[],
            estimated_bytes_processed=123,
            total_bytes_processed=None,
            row_count=0,
            job_id="cli-job",
            raw_metadata={"backend": "bq-cli"},
        ),
    )

    result = skill.execute_query(
        query="SELECT 1",
        location="US",
        max_rows=10,
        max_bytes_billed=1000,
        parameters=[],
        dry_run=True,
    )
    assert result.backend == "bq-cli"
    assert result.estimated_bytes_processed == 123


def test_execute_query_reports_setup_message_when_all_backends_fail(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        skill,
        "execute_with_python_client",
        lambda **_: (_ for _ in ()).throw(skill.BigQuerySetupError("ADC unavailable")),
    )
    monkeypatch.setattr(
        skill,
        "execute_with_bq_cli",
        lambda **_: (_ for _ in ()).throw(skill.BigQuerySetupError("invalid_grant")),
    )
    monkeypatch.setattr(skill, "_get_gcloud_project", lambda: "demo-project")

    with pytest.raises(skill.BigQuerySetupError, match="gcloud auth login"):
        skill.execute_query(
            query="SELECT 1",
            location="US",
            max_rows=10,
            max_bytes_billed=1000,
            parameters=[],
            dry_run=False,
        )


def test_demo_main_creates_expected_outputs(tmp_path: Path):
    exit_code = skill.main(["--demo", "--output", str(tmp_path)])
    assert exit_code == 0
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "result.json").exists()
    assert (tmp_path / "tables" / "results.csv").exists()
    assert (tmp_path / "reproducibility" / "commands.sh").exists()
    assert (tmp_path / "reproducibility" / "query.sql").exists()
    assert (tmp_path / "reproducibility" / "job_metadata.json").exists()

    payload = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert payload["summary"]["mode"] == "demo"
    assert payload["summary"]["backend"] == "demo-fixture"
    assert "query" in payload["data"]
    assert payload["data"]["disclaimer"].startswith("ClawBio is a research and educational tool")

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Execution mode" in report
    assert "demo-fixture" in report
    assert "bigquery-public-data.samples.shakespeare" in report


def test_demo_main_respects_max_rows_and_dry_run(tmp_path: Path):
    exit_code = skill.main(
        ["--demo", "--output", str(tmp_path), "--max-rows", "2", "--dry-run", "--location", "EU"]
    )
    assert exit_code == 0
    payload = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert payload["summary"]["location"] == "EU"
    assert payload["summary"]["row_count"] == 0
    assert payload["summary"]["dry_run"] is True


def test_runner_demo_executes_bigquery_skill(tmp_path: Path):
    output_dir = tmp_path / "runner_demo"
    result = clawbio_runner.run_skill(
        skill_name="bigquery",
        demo=True,
        output_dir=str(output_dir),
    )
    assert result["success"] is True
    assert (output_dir / "report.md").exists()
    assert (output_dir / "result.json").exists()


def test_runner_security_filter_allows_bigquery_flags_and_ignores_unknown(tmp_path: Path):
    output_dir = tmp_path / "runner_filtered"
    result = clawbio_runner.run_skill(
        skill_name="bigquery",
        demo=True,
        output_dir=str(output_dir),
        extra_args=["--location", "EU", "--max-rows", "2", "--bogus", "nope"],
    )
    assert result["success"] is True
    payload = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["summary"]["location"] == "EU"
    assert payload["summary"]["max_rows"] == 2


def test_cli_subprocess_demo_round_trip(tmp_path: Path):
    output_dir = tmp_path / "subprocess_demo"
    proc = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "clawbio.py"),
            "run",
            "bigquery",
            "--demo",
            "--output",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (output_dir / "report.md").exists()
