"""Live smoke test for ld-1000g-region-compute.

Invokes plink2 against a 1000 Genomes Phase 3 GRCh38 PLINK2 panel for a
small SORT1-locus LD computation (1p13.3 LDL/CHD per Musunuru 2010).
Gated by @pytest.mark.live + RUN_LIVE_TESTS=1, and additionally skipped
when plink2 isn't on PATH or a panel path isn't supplied — both are
reviewer-side install steps (plink2 is GPL-3, panel is open-access but
multi-GB).

Env var overrides:
    PLINK2_BIN     absolute path to plink2 binary (default: `plink2` on PATH)
    LD_1000G_PANEL absolute path to 1000G Phase 3 GRCh38 PLINK2 panel
                   (a .pgen/.pvar/.psam triple stem, EUR sub-panel)

Run with:
    RUN_LIVE_TESTS=1 LD_1000G_PANEL=/data/1000g_phase3_grch38_eur \\
        pytest skills/ld-1000g-region-compute/tests/test_live_ld_1000g_region_compute.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ld_1000g_region_compute import (  # noqa: E402
    LDResult,
    Plink2LDClient,
    SuperPop,
)


pytestmark = pytest.mark.live


def _plink2_bin() -> str | None:
    explicit = os.getenv("PLINK2_BIN")
    if explicit and os.path.isfile(explicit):
        return explicit
    return shutil.which("plink2")


def _panel_path() -> str | None:
    return os.getenv("LD_1000G_PANEL")


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="live tests gated on RUN_LIVE_TESTS=1",
)
@pytest.mark.skipif(
    _plink2_bin() is None,
    reason="plink2 not on PATH and PLINK2_BIN not set",
)
@pytest.mark.skipif(
    _panel_path() is None,
    reason="LD_1000G_PANEL env var not set; live LD test requires a 1000G Phase 3 GRCh38 PLINK2 panel",
)
def test_live_r2_with_lead_sort1_smoke() -> None:
    """Real plink2 subprocess: SORT1 lead vs a small partner set in EUR."""
    client = Plink2LDClient(
        panel_path=_panel_path(),
        super_pop=SuperPop.EUR,
        panel_id="1000g_phase3_v5b_grch38_basic",
        panel_version="5b",
        plink2_bin=_plink2_bin(),
    )
    lead = "1_109817590_G_T"
    partners = ["1_109817192_G_A", "1_109817838_T_C"]
    result = client.r2_with_lead(
        lead=lead,
        partners=partners,
        chromosome="1",
        window_bp=200_000,
    )
    assert isinstance(result, LDResult)
    # r² for the lead vs itself is 1.0 by definition; partners get a value in
    # [0, 1] if present in 1000G, NaN otherwise. Shape-only check here —
    # magnitude checks live in golden parity fixtures.
    assert len(result.pairs) >= 1
