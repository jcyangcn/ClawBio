"""Tests for executor.py — log placement and process handling."""
from __future__ import annotations

from pathlib import Path

import pytest

import executor as executor_module
from errors import SkillError
from executor import execute_nextflow


def test_logs_written_under_reproducibility(tmp_path: Path):
    """Run logs must live inside reproducibility/, not as a stray top-level dir.

    SKILL.md guarantees the output directory has exactly two children
    (``upstream/`` and ``reproducibility/``); the executor's stdout/stderr
    capture therefore belongs under ``reproducibility/logs/``.
    """
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    result = execute_nextflow(
        ["sh", "-c", "echo hello; echo oops 1>&2"],
        cwd=output_dir,
        output_dir=output_dir,
        timeout_seconds=30,
    )

    logs = output_dir / "reproducibility" / "logs"
    assert (logs / "stdout.txt").exists()
    assert (logs / "stderr.txt").exists()
    # No stray top-level logs/ directory.
    assert not (output_dir / "logs").exists()
    assert result["exit_code"] == 0
    assert (logs / "stdout.txt").read_text().strip() == "hello"
    assert (logs / "stderr.txt").read_text().strip() == "oops"
    # Reported paths point inside reproducibility/.
    assert "reproducibility/logs/stdout.txt" in Path(str(result["stdout_path"])).as_posix()


def test_macos_tmp_failure_hint_appended_on_failure(tmp_path, monkeypatch):
    """On macOS, a failed run with --output under /tmp must append the actionable
    Colima / 'No such file or directory' hint to the fix (parity with
    nfcore-scrnaseq / nfcore-rnaseq)."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(executor_module.sys, "platform", "darwin")
    monkeypatch.setattr(executor_module, "is_under_tmp", lambda _p: True, raising=False)
    with pytest.raises(SkillError) as exc:
        execute_nextflow(["sh", "-c", "exit 1"], cwd=output_dir, output_dir=output_dir, timeout_seconds=30)
    assert "No such file or directory" in exc.value.fix
    assert "home" in exc.value.fix.lower()


def test_macos_tmp_failure_hint_absent_when_not_under_tmp(tmp_path, monkeypatch):
    """The macOS /tmp hint must not appear for a non-/tmp output directory."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(executor_module.sys, "platform", "darwin")
    monkeypatch.setattr(executor_module, "is_under_tmp", lambda _p: False, raising=False)
    with pytest.raises(SkillError) as exc:
        execute_nextflow(["sh", "-c", "exit 1"], cwd=output_dir, output_dir=output_dir, timeout_seconds=30)
    assert "No such file or directory" not in exc.value.fix
