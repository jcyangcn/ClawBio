"""Tests for polars_bio_runner.py"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).parent.parent
SCRIPT = SKILL_DIR / "polars_bio_runner.py"
EX = SKILL_DIR / "examples"


def run(args, env=None, **kw):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True, text=True, env=e, **kw,
    )


def _load_module():
    spec = importlib.util.spec_from_file_location("pbr", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_parser_args(m, argv):
    return m.build_parser().parse_args(argv)


class TestScaffold:
    def test_help_exits_zero(self):
        r = run(["--help"])
        assert r.returncode == 0
        assert "subcommand" in (r.stdout + r.stderr).lower()

    def test_missing_output_errors(self):
        r = run(["overlap", "--a", str(EX / "demo_a.bed"), "--b", str(EX / "demo_b.bed")])
        assert r.returncode != 0

    def test_unknown_subcommand_errors(self):
        r = run(["frobnicate", "--output", "/tmp/x"])
        assert r.returncode != 0

    def test_missing_dependency_hint(self, tmp_path):
        r = run(["overlap", "--a", str(EX / "demo_a.bed"), "--b", str(EX / "demo_b.bed"),
                 "--output", str(tmp_path)],
                env={"POLARS_BIO_FORCE_IMPORT_ERROR": "1"})
        assert r.returncode == 2
        out = r.stdout + r.stderr
        assert "pip install polars-bio" in out
        assert "not installed" in out


class TestDemoData:
    def test_demo_files_exist(self):
        assert (EX / "demo_a.bed").exists()
        assert (EX / "demo_b.bed").exists()
        assert (EX / "demo.vcf").exists()

    def test_demo_bed_is_tab_delimited(self):
        line = (EX / "demo_a.bed").read_text().splitlines()[0]
        # polars-bio BED reader requires >=4 columns (chrom,start,end,name)
        assert len(line.split("\t")) >= 4


class TestOutputHelpers:
    def test_build_result_schema(self):
        m = _load_module()
        res = m.build_result("overlap", {"k": 1}, ["a.bed", "b.bed"],
                              output_rows=3, output_schema={"chrom": "str"}, figure=None)
        for key in ("skill", "subcommand", "params", "polars_bio_version",
                    "inputs", "output_rows", "output_schema", "figure", "timestamp"):
            assert key in res
        assert res["skill"] == "polars-bio"
        assert res["subcommand"] == "overlap"

    def test_write_result_json(self, tmp_path):
        m = _load_module()
        res = m.build_result("merge", {}, ["a.bed"], 2, {"chrom": "str"}, None)
        m.write_result_json(res, tmp_path)
        data = json.loads((tmp_path / "result.json").read_text())
        assert data["subcommand"] == "merge"

    def test_write_report_has_disclaimer_and_citation(self, tmp_path):
        m = _load_module()
        res = m.build_result("merge", {"k": 1}, ["a.bed"], 2, {"chrom": "str"}, None)
        m.write_report(res, tmp_path)
        text = (tmp_path / "report.md").read_text()
        assert "not a medical device" in text
        assert "btaf640" in text

    def test_write_report_flush_left(self, tmp_path):
        # Headings must not be indented (8-space indent => markdown code block).
        m = _load_module()
        res = m.build_result("merge", {"k": 1}, ["a.bed"], 2, {"chrom": "str"}, None)
        m.write_report(res, tmp_path)
        text = (tmp_path / "report.md").read_text()
        assert text.startswith("# polars-bio")
        assert "\n## Parameters" in text
        assert "\n    # polars-bio" not in text

    def test_write_reproducibility(self, tmp_path):
        m = _load_module()
        args = build_parser_args(m, ["merge", "--a", "a.bed", "--output", str(tmp_path)])
        m.write_reproducibility(args, tmp_path)
        assert (tmp_path / "reproducibility" / "commands.sh").exists()


class TestIntervalOps:
    def setup_method(self):
        pytest.importorskip("polars_bio")

    def test_demo_end_to_end(self, tmp_path):
        r = run(["--demo", "--output", str(tmp_path)])
        assert r.returncode == 0, r.stderr
        assert (tmp_path / "report.md").exists()
        data = json.loads((tmp_path / "result.json").read_text())
        assert data["subcommand"] == "overlap"
        assert data["output_rows"] is not None and data["output_rows"] >= 1
        # provenance is the actually-installed version, not a hardcoded string
        assert data["polars_bio_version"] not in (None, "", "unknown")

    def test_overlap_outputs(self, tmp_path):
        r = run(["overlap", "--a", str(EX / "demo_a.bed"), "--b", str(EX / "demo_b.bed"),
                 "--output", str(tmp_path)])
        assert r.returncode == 0, r.stderr
        assert (tmp_path / "result.json").exists()
        assert (tmp_path / "result.csv").exists()

    def test_merge_single_input(self, tmp_path):
        r = run(["merge", "--a", str(EX / "demo_a.bed"), "--output", str(tmp_path)])
        assert r.returncode == 0, r.stderr
        data = json.loads((tmp_path / "result.json").read_text())
        assert data["subcommand"] == "merge"

    def test_count_overlaps(self, tmp_path):
        r = run(["count-overlaps", "--a", str(EX / "demo_a.bed"), "--b", str(EX / "demo_b.bed"),
                 "--output", str(tmp_path)])
        assert r.returncode == 0, r.stderr
        data = json.loads((tmp_path / "result.json").read_text())
        assert data["subcommand"] == "count-overlaps"

    def test_two_input_op_requires_b(self, tmp_path):
        r = run(["overlap", "--a", str(EX / "demo_a.bed"), "--output", str(tmp_path)])
        assert r.returncode != 0

    def test_output_dir_created_if_missing(self, tmp_path):
        fresh = tmp_path / "does" / "not" / "exist"
        r = run(["--demo", "--output", str(fresh)])
        assert r.returncode == 0, r.stderr
        assert (fresh / "figure.png").exists()
        assert (fresh / "report.md").exists()


IO_FORMATS = [
    ("bed", "demo_a.bed"), ("vcf", "demo.vcf"), ("gff", "demo.gff3"),
    ("gtf", "demo.gtf"), ("fasta", "demo.fasta"), ("fastq", "demo.fastq"),
    ("bam", "demo.bam"), ("sam", "demo.sam"), ("pairs", "demo.pairs"),
    ("bigwig", "demo.bw"), ("bigbed", "demo.bb"),
]


class TestIO:
    def setup_method(self):
        pytest.importorskip("polars_bio")

    @pytest.mark.parametrize("fmt,fname", IO_FORMATS)
    def test_io_reads_each_format(self, fmt, fname, tmp_path):
        r = run(["io", "--input", str(EX / fname), "--format", fmt,
                 "--output", str(tmp_path)])
        assert r.returncode == 0, r.stderr
        data = json.loads((tmp_path / "result.json").read_text())
        assert data["subcommand"] == "io"
        assert data["output_schema"]
        assert data["output_rows"] is not None and data["output_rows"] >= 1

    def test_io_describe_vcf(self, tmp_path):
        r = run(["io", "--input", str(EX / "demo.vcf"), "--format", "vcf",
                 "--describe", "--output", str(tmp_path)])
        assert r.returncode == 0, r.stderr
        data = json.loads((tmp_path / "result.json").read_text())
        assert data["params"]["describe"] is True
        assert data["output_rows"] is None  # schema-only

    def test_io_describe_bam(self, tmp_path):
        r = run(["io", "--input", str(EX / "demo.bam"), "--format", "bam",
                 "--describe", "--output", str(tmp_path)])
        assert r.returncode == 0, r.stderr

    def test_io_requires_input_and_format(self, tmp_path):
        r = run(["io", "--output", str(tmp_path)])
        assert r.returncode != 0

    def test_io_unknown_format_errors(self, tmp_path):
        r = run(["io", "--input", str(EX / "demo.vcf"), "--format", "xyz",
                 "--output", str(tmp_path)])
        assert r.returncode != 0


class TestSQL:
    def setup_method(self):
        pytest.importorskip("polars_bio")

    def test_sql_query_on_vcf(self, tmp_path):
        r = run(["sql", "--input", str(EX / "demo.vcf"),
                 "--query", "SELECT chrom, start FROM t", "--output", str(tmp_path)])
        assert r.returncode == 0, r.stderr
        data = json.loads((tmp_path / "result.json").read_text())
        assert data["subcommand"] == "sql"
        assert data["output_rows"] is not None and data["output_rows"] >= 1

    def test_sql_query_on_bed(self, tmp_path):
        r = run(["sql", "--input", str(EX / "demo_a.bed"),
                 "--query", "SELECT * FROM t WHERE chrom = 'chr1'", "--output", str(tmp_path)])
        assert r.returncode == 0, r.stderr
        data = json.loads((tmp_path / "result.json").read_text())
        assert data["output_rows"] >= 1

    def test_sql_requires_query(self, tmp_path):
        r = run(["sql", "--input", str(EX / "demo.vcf"), "--output", str(tmp_path)])
        assert r.returncode != 0


class TestPileup:
    def test_pileup_requires_input(self, tmp_path):
        r = run(["pileup", "--output", str(tmp_path)])
        assert r.returncode != 0

    def test_pileup_on_demo_bam(self, tmp_path):
        pytest.importorskip("polars_bio")
        r = run(["pileup", "--input", str(EX / "demo.bam"), "--output", str(tmp_path)])
        assert r.returncode == 0, r.stderr
        data = json.loads((tmp_path / "result.json").read_text())
        assert data["subcommand"] == "pileup"
        assert data["output_rows"] is not None and data["output_rows"] >= 1

    def test_pileup_missing_bai_errors(self, tmp_path):
        pytest.importorskip("polars_bio")
        bam = tmp_path / "x.bam"
        bam.write_bytes(b"not a real bam")  # no .bai alongside
        r = run(["pileup", "--input", str(bam), "--output", str(tmp_path)])
        assert r.returncode != 0
        assert "index" in (r.stdout + r.stderr).lower()


class TestSkillMd:
    SKILL_MD = SKILL_DIR / "SKILL.md"

    def test_exists_and_under_500_lines(self):
        assert self.SKILL_MD.exists()
        assert len(self.SKILL_MD.read_text().splitlines()) < 500

    def test_required_sections(self):
        text = self.SKILL_MD.read_text()
        for h in ("## Trigger", "## Why This Exists", "## Scope", "## Workflow",
                  "## Example Output", "## Gotchas", "## Safety", "## Agent Boundary",
                  "## Chaining Partners", "## Maintenance",
                  "## Polars & the Python ecosystem"):
            assert h in text, f"missing section: {h}"

    def test_frontmatter_and_metadata(self):
        text = self.SKILL_MD.read_text()
        assert text.startswith("---")
        assert "name: polars-bio" in text
        assert "Apache-2.0" in text
        assert "btaf640" in text         # citation
        assert "trigger_keywords" in text
        # The skill must not pin a polars-bio version anywhere.
        assert "0.32.0" not in text


class TestReferences:
    REF = SKILL_DIR / "references"

    def test_all_six_present(self):
        for name in ("polars_primer", "interval_operations", "file_io",
                     "sql_processing", "pileup_operations", "configuration"):
            f = self.REF / f"{name}.md"
            assert f.exists(), f"missing {f}"
            assert len(f.read_text()) > 400, f"{f} too thin"

    def test_no_bioframe_migration(self):
        assert not (self.REF / "bioframe_migration.md").exists()

    def test_primer_has_citation_and_stack(self):
        text = (self.REF / "polars_primer.md").read_text()
        assert "btaf660" not in text  # guard against typo'd DOI
        assert "btaf640" in text
        assert "DataFusion" in text and "Arrow" in text


class TestRouting:
    ROOT = SKILL_DIR.parent.parent
    CLAUDE = ROOT / "CLAUDE.md"

    def test_routing_row_present(self):
        text = self.CLAUDE.read_text()
        assert "skills/polars-bio/" in text
        assert "polars_bio_runner.py" in text

    def test_trigger_keywords_in_routing(self):
        text = self.CLAUDE.read_text().lower()
        assert "interval overlap" in text
        assert "bioframe" in text or "polars-bio" in text


class TestCatalog:
    ROOT = SKILL_DIR.parent.parent
    CATALOG = ROOT / "skills" / "catalog.json"

    def test_polars_bio_in_catalog(self):
        data = json.loads(self.CATALOG.read_text())
        names = {s["name"] for s in data["skills"]}
        assert "polars-bio" in names

    def test_catalog_entry_flags(self):
        data = json.loads(self.CATALOG.read_text())
        entry = next(s for s in data["skills"] if s["name"] == "polars-bio")
        assert entry["has_script"] and entry["has_tests"] and entry["has_demo"]
