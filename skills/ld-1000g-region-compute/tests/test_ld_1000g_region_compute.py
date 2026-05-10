"""Unit tests for the plink2 LD wrapper.

We mock subprocess + filesystem so tests are offline and don't require plink2
to be installed. Live smoke (real plink2 + 1000G EUR panel) goes in test_live_ld_1000g_region_compute.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ld_1000g_region_compute import (
    LDComputeError,
    Plink2LDClient,
    SuperPop,
)
import ld_1000g_region_compute as ld_module


# -----------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------


def _make_panel(tmp_path: Path) -> Path:
    """Create the three PLINK2 sibling files so _validate_panel passes."""
    base = tmp_path / "panel_eur"
    for suf in (".pgen", ".pvar", ".psam"):
        (tmp_path / f"panel_eur{suf}").write_bytes(b"")
    return base


def _patch_plink2_version(monkeypatch, version: str = "PLINK v2.00a5LM"):
    """Make _detect_plink2_version return a fixed string without invoking shutil/subprocess."""
    monkeypatch.setattr(
        ld_module, "_detect_plink2_version", lambda _bin: version
    )


def _write_vcor2(out_prefix: Path, lead: str, partner_pairs: list[tuple[str, float]],
                 sep: str = ":"):
    """Emit a plink2 .vcor2 file at out_prefix.vcor2.

    `sep` is the variant-id separator the panel uses. Real plink2 against the
    1000G GRCh38 distribution writes ids with colons.
    """
    def _to_panel(ot_id: str) -> str:
        return ot_id.replace("_", sep, 3) if sep != "_" else ot_id
    rows = ["#CHROM_A\tPOS_A\tID_A\tREF_A\tALT_A\tCHROM_B\tPOS_B\tID_B\tREF_B\tALT_B\tUNPHASED_R2\tDP"]
    for partner, r2 in partner_pairs:
        rows.append(
            f"2\t36910110\t{_to_panel(lead)}\tC\tT\t"
            f"2\t36932656\t{_to_panel(partner)}\tA\tG\t{r2:.6f}\t0.95"
        )
    Path(f"{out_prefix}.vcor2").write_text("\n".join(rows) + "\n")


# -----------------------------------------------------------------
# Construction / validation
# -----------------------------------------------------------------


def test_client_validates_plink2_present(monkeypatch, tmp_path):
    panel = _make_panel(tmp_path)
    _patch_plink2_version(monkeypatch)
    c = Plink2LDClient(
        panel_path=panel, super_pop=SuperPop.EUR,
        panel_id="1000g_phase3_v5b_grch38_basic", panel_version="5b",
    )
    assert c.plink2_version == "PLINK v2.00a5LM"
    assert c.super_pop == SuperPop.EUR


def test_client_raises_when_plink2_missing(monkeypatch, tmp_path):
    panel = _make_panel(tmp_path)
    monkeypatch.setattr(
        ld_module, "_detect_plink2_version",
        lambda _bin: (_ for _ in ()).throw(LDComputeError("plink2 not found"))
    )
    with pytest.raises(LDComputeError, match="plink2 not found"):
        Plink2LDClient(
            panel_path=panel, super_pop=SuperPop.EUR,
            panel_id="1000g_phase3_v5b_grch38_basic", panel_version="5b",
        )


def test_client_raises_on_missing_panel_siblings(monkeypatch, tmp_path):
    _patch_plink2_version(monkeypatch)
    # Only create .pgen, missing .pvar and .psam.
    panel = tmp_path / "incomplete"
    (tmp_path / "incomplete.pgen").write_bytes(b"")
    with pytest.raises(LDComputeError, match="missing"):
        Plink2LDClient(
            panel_path=panel, super_pop=SuperPop.EUR,
            panel_id="1000g_phase3_v5b_grch38_basic", panel_version="5b",
        )


# -----------------------------------------------------------------
# r2_with_lead happy path
# -----------------------------------------------------------------


def test_r2_with_lead_parses_vcor2(monkeypatch, tmp_path):
    panel = _make_panel(tmp_path)
    _patch_plink2_version(monkeypatch)

    captured_cmd = {}

    def fake_run(cmd, capture_output, text, check, timeout):
        captured_cmd["cmd"] = cmd
        # cmd[-1] is the --out prefix.
        out_prefix = Path(cmd[cmd.index("--out") + 1])
        _write_vcor2(out_prefix, lead="2_36910110_C_T", partner_pairs=[
            ("2_36932656_A_G", 0.94),
            ("2_36905984_C_T", 0.81),
            ("2_36897612_T_C", 0.40),
        ])
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    client = Plink2LDClient(
        panel_path=panel, super_pop=SuperPop.EUR,
        panel_id="1000g_phase3_v5b_grch38_basic", panel_version="5b",
    )
    result = client.r2_with_lead(
        lead="2_36910110_C_T",
        partners=["2_36932656_A_G", "2_36905984_C_T", "2_36897612_T_C"],
        chromosome="2",
        window_bp=1_000_000,
    )

    assert result.lead_variant_id == "2_36910110_C_T"
    assert result.super_pop == SuperPop.EUR
    assert result.panel_version == "5b"
    assert result.plink2_version == "PLINK v2.00a5LM"
    assert result.n_partners_requested == 3
    assert result.n_partners_returned == 3
    r2_by_id = {p.partner_variant_id: p.r2 for p in result.pairs}
    assert r2_by_id["2_36932656_A_G"] == pytest.approx(0.94)
    assert r2_by_id["2_36905984_C_T"] == pytest.approx(0.81)
    assert r2_by_id["2_36897612_T_C"] == pytest.approx(0.40)
    # Verify cmd shape (audit-friendly).
    assert "--r2-unphased" in captured_cmd["cmd"]
    assert "--ld-snp" in captured_cmd["cmd"]


def test_r2_with_lead_handles_empty_partners(monkeypatch, tmp_path):
    panel = _make_panel(tmp_path)
    _patch_plink2_version(monkeypatch)
    client = Plink2LDClient(
        panel_path=panel, super_pop=SuperPop.EUR,
        panel_id="1000g_phase3_v5b_grch38_basic", panel_version="5b",
    )
    result = client.r2_with_lead(lead="2_1_C_T", partners=[])
    assert result.n_partners_requested == 0
    assert result.pairs == []
    assert any("no partners requested" in n for n in result.notes)


def test_r2_with_lead_raises_on_plink2_nonzero_exit(monkeypatch, tmp_path):
    panel = _make_panel(tmp_path)
    _patch_plink2_version(monkeypatch)

    def boom(cmd, capture_output, text, check, timeout):
        return MagicMock(returncode=1, stdout="", stderr="missing variant id")
    monkeypatch.setattr("subprocess.run", boom)

    client = Plink2LDClient(
        panel_path=panel, super_pop=SuperPop.EUR,
        panel_id="1000g_phase3_v5b_grch38_basic", panel_version="5b",
    )
    with pytest.raises(LDComputeError, match="exited with code 1"):
        client.r2_with_lead(lead="2_1_C_T", partners=["2_2_A_G"])


def test_r2_with_lead_raises_when_vcor2_absent(monkeypatch, tmp_path):
    panel = _make_panel(tmp_path)
    _patch_plink2_version(monkeypatch)

    def succeeds_but_writes_nothing(cmd, capture_output, text, check, timeout):
        return MagicMock(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr("subprocess.run", succeeds_but_writes_nothing)

    client = Plink2LDClient(
        panel_path=panel, super_pop=SuperPop.EUR,
        panel_id="1000g_phase3_v5b_grch38_basic", panel_version="5b",
    )
    with pytest.raises(LDComputeError, match=r"no \.vcor"):
        client.r2_with_lead(lead="2_1_C_T", partners=["2_2_A_G"])


def test_vcor2_parser_skips_rows_without_lead_match(monkeypatch, tmp_path):
    """A .vcor2 row whose ID_A and ID_B are both unrelated to the lead should be ignored."""
    panel = _make_panel(tmp_path)
    _patch_plink2_version(monkeypatch)

    def fake_run(cmd, capture_output, text, check, timeout):
        out_prefix = Path(cmd[cmd.index("--out") + 1])
        rows = [
            "#CHROM_A\tPOS_A\tID_A\tREF_A\tALT_A\tCHROM_B\tPOS_B\tID_B\tREF_B\tALT_B\tUNPHASED_R2\tDP",
            "2\t1\t2_x_C_T\tC\tT\t2\t2\t2_y_A_G\tA\tG\t0.5\t0.6",   # neither id matches lead
            "2\t1\tlead\tC\tT\t2\t2\t2_z_A_G\tA\tG\t0.7\t0.9",       # matches
        ]
        Path(f"{out_prefix}.vcor2").write_text("\n".join(rows) + "\n")
        return MagicMock(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    client = Plink2LDClient(
        panel_path=panel, super_pop=SuperPop.EUR,
        panel_id="1000g_phase3_v5b_grch38_basic", panel_version="5b",
    )
    result = client.r2_with_lead(lead="lead", partners=["2_z_A_G"])
    assert result.n_partners_returned == 1
    assert result.pairs[0].partner_variant_id == "2_z_A_G"
    assert result.pairs[0].r2 == pytest.approx(0.7)
