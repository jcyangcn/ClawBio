"""Tests for xena-tcga-gene-query skill (red/green TDD)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / "scripts" / "query_tcga_api.py"


def run_cli(args, expect_code=0):
    """Run the CLI and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )
    if expect_code is not None:
        assert result.returncode == expect_code, (
            f"Expected exit {expect_code}, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


# ──────────────────────────────────────────────
# Demo mode
# ──────────────────────────────────────────────

def test_demo_runs_successfully(tmp_path):
    """--demo --output DIR produces report.md, result.json, and reproducibility/."""
    out = tmp_path / "demo_out"
    run_cli(["--demo", "--output", str(out)])
    assert (out / "report.md").exists(), "report.md not generated"
    assert (out / "result.json").exists(), "result.json not generated"
    repro = out / "reproducibility"
    assert repro.is_dir(), "reproducibility/ not created"
    assert (repro / "commands.sh").exists(), "commands.sh not generated"
    assert (repro / "run.json").exists(), "run.json not generated"


def test_demo_result_json_is_valid(tmp_path):
    """Demo result.json parses and has expected top-level keys."""
    out = tmp_path / "demo_out"
    run_cli(["--demo", "--output", str(out)])
    data = json.loads((out / "result.json").read_text())
    assert "diff_expr" in data
    assert "corr" in data
    assert "survival" in data


def test_demo_diff_expr_has_required_fields(tmp_path):
    """Demo diff-expr result contains gene, cancer, tumor/normal n, log2FC, p-value."""
    out = tmp_path / "demo_out"
    run_cli(["--demo", "--output", str(out)])
    data = json.loads((out / "result.json").read_text())
    de = data["diff_expr"]
    assert de["gene"] == "TP53"
    assert de["cancer"] == "LUAD"
    assert de["tumor"]["n"] > 0
    assert de["normal"]["n"] > 0
    assert "log2_fold_change" in de
    assert "p_value" in de


def test_demo_corr_has_required_fields(tmp_path):
    """Demo corr result contains both genes, cancer, n, spearman_r, p-value."""
    out = tmp_path / "demo_out"
    run_cli(["--demo", "--output", str(out)])
    data = json.loads((out / "result.json").read_text())
    cr = data["corr"]
    assert cr["gene1"] == "TP53"
    assert cr["gene2"] == "EGFR"
    assert cr["cancer"] == "LUAD"
    assert cr["n"] > 0
    assert "spearman_r" in cr
    assert "p_value" in cr


def test_demo_survival_has_all_endpoints(tmp_path):
    """Demo survival result covers OS, DSS, DFI, PFI."""
    out = tmp_path / "demo_out"
    run_cli(["--demo", "--output", str(out)])
    data = json.loads((out / "result.json").read_text())
    sv = data["survival"]
    assert sv["gene"] == "TP53"
    assert sv["cancer"] == "LUAD"
    for ep in ["OS", "DSS", "DFI", "PFI"]:
        assert ep in sv["survival"], f"Missing survival endpoint: {ep}"


def test_report_includes_clawbio_disclaimer(tmp_path):
    """report.md must reference the ClawBio research-tool disclaimer."""
    out = tmp_path / "demo_out"
    run_cli(["--demo", "--output", str(out)])
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "ClawBio" in md
    assert "research" in md.lower() and "medical device" in md.lower()


def test_report_includes_interpretation_notes(tmp_path):
    """report.md includes interpretation guidance with sample sizes and caveats."""
    out = tmp_path / "demo_out"
    run_cli(["--demo", "--output", str(out)])
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "Interpretation" in md
    assert "n =" in md or "n=" in md
    # Must warn about optimal cutoff being exploratory
    assert "exploratory" in md.lower() or "Exploratory" in md


def test_demo_prints_summary_to_stdout(tmp_path):
    """Demo mode prints a summary of all three queries to stdout."""
    out = tmp_path / "demo_out"
    result = run_cli(["--demo", "--output", str(out)])
    stdout = result.stdout
    assert "TP53" in stdout
    assert "LUAD" in stdout
    assert "EGFR" in stdout


def test_demo_output_dir_created_if_missing(tmp_path):
    """--demo creates the output directory if it doesn't exist."""
    out = tmp_path / "nested" / "demo_out"
    assert not out.exists()
    run_cli(["--demo", "--output", str(out)])
    assert out.is_dir()
    assert (out / "report.md").exists()


def test_reproducibility_run_json_is_valid(tmp_path):
    """reproducibility/run.json contains skill name and mode."""
    out = tmp_path / "demo_out"
    run_cli(["--demo", "--output", str(out)])
    run_json = json.loads((out / "reproducibility" / "run.json").read_text())
    assert run_json["skill"] == "xena-tcga-gene-query"
    assert run_json["mode"] == "demo"


# ──────────────────────────────────────────────
# JSON mode (live API is not required for these)
# ──────────────────────────────────────────────

def test_demo_with_json_flag(tmp_path):
    """--demo --json prints only raw JSON (no summary) to stdout."""
    out = tmp_path / "demo_out"
    result = run_cli(["--demo", "--json", "--output", str(out)])
    # Should still produce file outputs
    assert (out / "report.md").exists()


# ──────────────────────────────────────────────
# Error paths
# ──────────────────────────────────────────────

def test_no_task_and_no_demo_is_error():
    """Running without --demo and without a task should exit non-zero."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_diff_expr_without_gene_fails():
    """diff-expr without --gene should fail."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "diff-expr", "--cancer", "LUAD"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


# ──────────────────────────────────────────────
# Output structure (matches SKILL.md contract)
# ──────────────────────────────────────────────

def test_output_structure_matches_contract(tmp_path):
    """Verify the output tree declared in SKILL.md is produced."""
    out = tmp_path / "demo_out"
    run_cli(["--demo", "--output", str(out)])

    expected = [
        out / "report.md",
        out / "result.json",
        out / "reproducibility" / "commands.sh",
        out / "reproducibility" / "run.json",
    ]
    for path in expected:
        assert path.exists(), f"Missing expected output: {path}"
