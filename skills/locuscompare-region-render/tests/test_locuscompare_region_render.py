"""Smoke tests for the locuscompare CLI.

Verifies the CLI contract (argument parsing, config loading, output
directory creation, helpful error messages) without exercising the full
fetch / harmonise / render pipeline. End-to-end tests with real network
calls live in test_live_locuscompare_region_render.py (marked @pytest.mark.live).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml


SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

import cli as locuscompare  # noqa: E402


def test_cli_requires_input_or_demo(tmp_path: Path):
    """argparse should reject invocations missing both --input and --demo."""
    with pytest.raises(SystemExit) as excinfo:
        locuscompare.main(["--output", str(tmp_path)])
    assert excinfo.value.code == 2


def test_cli_creates_output_directory_on_invalid_config(tmp_path: Path):
    """--output is created up-front, even when the config later fails validation."""
    out = tmp_path / "sub1" / "sub2"
    bad_config = {"schema_version": "1.0"}  # missing lead/exposure/outcome
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(bad_config))
    rc = locuscompare.main(["--input", str(config_path), "--output", str(out)])
    assert out.is_dir()
    assert rc == 2


def test_cli_rejects_missing_lead(tmp_path: Path, capsys):
    config = {
        "exposure": {"trait_label": "x", "fetch": {"source": "eqtl_catalogue", "dataset_id": "X"}},
        "outcome": {"trait_label": "y", "fetch": {"source": "gwas_catalog", "accession": "Y"}},
    }
    config_path = tmp_path / "c.json"
    config_path.write_text(json.dumps(config))
    rc = locuscompare.main(["--input", str(config_path), "--output", str(tmp_path / "out")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "lead" in captured.err.lower()


def test_cli_rejects_unsupported_exposure_source(tmp_path: Path, capsys):
    config = {
        "lead": {"variant_id": "1_1_A_T", "chromosome": "1", "position_bp": 1, "window_bp": 1000000},
        "exposure": {"trait_label": "x", "fetch": {"source": "made_up_source", "dataset_id": "X"}},
        "outcome": {"trait_label": "y", "fetch": {"source": "gwas_catalog", "accession": "Y"}},
    }
    config_path = tmp_path / "c.json"
    config_path.write_text(json.dumps(config))
    rc = locuscompare.main(["--input", str(config_path), "--output", str(tmp_path / "out")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "made_up_source" in captured.err


def test_cli_rejects_missing_exposure_input_vector(tmp_path: Path, capsys):
    """Exposure must declare either `fetch:` (live tabix) or `sumstats_path:`
    (pre-fetched canonical TSV); missing both is a hard error."""
    config = {
        "lead": {"variant_id": "1_1_A_T", "chromosome": "1", "position_bp": 1, "window_bp": 1000000},
        "exposure": {"trait_label": "x"},  # no fetch and no sumstats_path
        "outcome": {"trait_label": "y", "fetch": {"source": "gwas_catalog", "accession": "Y"}},
    }
    config_path = tmp_path / "c.json"
    config_path.write_text(json.dumps(config))
    rc = locuscompare.main(["--input", str(config_path), "--output", str(tmp_path / "out")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "sumstats_path" in captured.err and "fetch" in captured.err


def test_load_config_yaml(tmp_path: Path):
    """_load_config dispatches on file extension."""
    yaml_path = tmp_path / "c.yaml"
    yaml_path.write_text("schema_version: '1.0'\nfoo: bar\n")
    cfg = locuscompare._load_config(yaml_path)
    assert cfg == {"schema_version": "1.0", "foo": "bar"}


def test_load_config_json(tmp_path: Path):
    json_path = tmp_path / "c.json"
    json_path.write_text('{"schema_version": "1.0", "foo": "bar"}')
    cfg = locuscompare._load_config(json_path)
    assert cfg == {"schema_version": "1.0", "foo": "bar"}


def test_load_config_unknown_extension(tmp_path: Path):
    bad = tmp_path / "c.toml"
    bad.write_text("[section]\nfoo = 'bar'\n")
    with pytest.raises(ValueError, match="unsupported config extension"):
        locuscompare._load_config(bad)


def test_format_provenance_prefix_empty():
    assert locuscompare._format_provenance_prefix({}) == ""


def test_format_provenance_prefix_ot_release():
    assert locuscompare._format_provenance_prefix({"provenance": {"ot_release": "26.03"}}) == "OT release: 26.03 | "


def test_format_provenance_prefix_gwas_lookup():
    assert locuscompare._format_provenance_prefix(
        {"provenance": {"gwas_lookup_run_dir": "runs/gl/"}}
    ) == "gwas-lookup chain: runs/gl/ | "


# ----- Pre-fetched TSV input mode (INPUT_SCHEMA.md path) -----


CANONICAL_TSV_HEADER = (
    "variant_id\tchromosome\tposition_bp\tallele_a\tallele_b\tbeta\tse\tp"
)


def _write_canonical_tsv(path: Path, rows: list[str]) -> None:
    path.write_text(CANONICAL_TSV_HEADER + "\n" + "\n".join(rows) + "\n")


def test_prefetched_load_sumstats_tsv_parses_canonical_schema(tmp_path: Path):
    from _prefetched import load_sumstats_tsv

    tsv = tmp_path / "exposure.tsv"
    _write_canonical_tsv(tsv, [
        "1_500000_A_T\t1\t500000\tA\tT\t0.5\t0.05\t1e-22",
        "1_500100_C_G\t1\t500100\tC\tG\t0.3\t0.05\t1e-9",
    ])
    variants = load_sumstats_tsv(tsv)
    assert len(variants) == 2
    assert variants[0].variant_id == "1_500000_A_T"
    assert variants[0].ref == "A" and variants[0].alt == "T"
    assert variants[0].beta == 0.5 and variants[0].se == 0.05
    assert variants[0].p_value == 1e-22


def test_prefetched_load_sumstats_tsv_rejects_missing_columns(tmp_path: Path):
    from _prefetched import PrefetchedSchemaError, load_sumstats_tsv

    tsv = tmp_path / "bad.tsv"
    # Missing the required `p` column.
    tsv.write_text("variant_id\tchromosome\tposition_bp\tallele_a\tallele_b\tbeta\tse\n")
    with pytest.raises(PrefetchedSchemaError, match="missing required columns"):
        load_sumstats_tsv(tsv)


def test_prefetched_load_sumstats_tsv_drops_rows_with_na(tmp_path: Path):
    from _prefetched import load_sumstats_tsv

    tsv = tmp_path / "exposure.tsv"
    _write_canonical_tsv(tsv, [
        "1_500000_A_T\t1\t500000\tA\tT\t0.5\t0.05\t1e-22",
        "1_500100_C_G\t1\t500100\tC\tG\tNA\tNA\tNA",  # dropped
    ])
    variants = load_sumstats_tsv(tsv)
    assert len(variants) == 1
    assert variants[0].variant_id == "1_500000_A_T"


def test_prefetched_load_synthetic_ld_parses_two_columns(tmp_path: Path):
    from _prefetched import load_synthetic_ld

    tsv = tmp_path / "ld.tsv"
    tsv.write_text("partner_variant_id\tr2\n1_500100_C_G\t0.85\n1_499900_G_A\t0.42\n")
    r2 = load_synthetic_ld(tsv)
    assert r2 == {"1_500100_C_G": 0.85, "1_499900_G_A": 0.42}


def test_prefetched_load_synthetic_ld_rejects_out_of_range(tmp_path: Path):
    from _prefetched import PrefetchedSchemaError, load_synthetic_ld

    tsv = tmp_path / "ld.tsv"
    tsv.write_text("partner_variant_id\tr2\n1_500100_C_G\t1.5\n")
    with pytest.raises(PrefetchedSchemaError, match="out of"):
        load_synthetic_ld(tsv)


def test_prefetched_load_synthetic_gene_track_parses(tmp_path: Path):
    from _prefetched import load_synthetic_gene_track

    tsv = tmp_path / "genes.tsv"
    tsv.write_text(
        "gene_symbol\tstart\tend\tstrand\tbiotype\n"
        "DEMOGENE_A\t100\t200\t+\tprotein_coding\n"
        "DEMOGENE_B\t450\t550\t-\tprotein_coding\n"
    )
    genes = load_synthetic_gene_track(tsv)
    assert [g.gene_symbol for g in genes] == ["DEMOGENE_A", "DEMOGENE_B"]
    assert genes[0].start == 100 and genes[0].end == 200 and genes[0].strand == "+"
    assert genes[1].strand == "-"


def test_prefetched_synthetic_demo_runs_end_to_end_offline(tmp_path: Path):
    """01_synthetic_demo must run without network: TSV-loaded sumstats +
    synthetic LD matrix + synthetic gene track. Asserts a PNG + manifest land
    in the output directory and the expected lead emerges from harmonisation."""
    out = tmp_path / "out"
    demo_config = SKILL_DIR / "examples" / "01_synthetic_demo" / "config.json"
    rc = locuscompare.main(["--input", str(demo_config), "--output", str(out)])
    assert rc == 0, "01_synthetic_demo failed offline"
    plot = out / "1_500000_A_T_full_locuscompare.png"
    manifest = out / "manifest.yaml"
    assert plot.is_file() and plot.stat().st_size > 10_000
    assert manifest.is_file()
    manifest_text = manifest.read_text()
    assert "1_500000_A_T" in manifest_text
