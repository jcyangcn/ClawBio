"""Helpers for discovering and parsing Illumina/DRAGEN export bundles."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SAMPLE_SHEET_PATTERNS = (
    "SampleSheet.csv",
    "samplesheet.csv",
    "*SampleSheet*.csv",
    "*sample_sheet*.csv",
)
VCF_PATTERNS = ("*.vcf.gz", "*.vcf")
QC_PATTERNS = (
    "qc_metrics.json",
    "*qc*.json",
    "*metrics*.json",
    "qc_metrics.csv",
    "*qc*.csv",
    "*metrics*.csv",
)
MOCK_ICA_FILENAME = "mock_ica_metadata.json"


@dataclass(frozen=True)
class BundleArtifacts:
    """Discovered paths that define a DRAGEN export bundle."""

    bundle_dir: Path
    vcf_path: Path
    qc_path: Path
    sample_sheet_path: Path
    mock_ica_metadata_path: Path | None = None


def _sorted_matches(bundle_dir: Path, patterns: tuple[str, ...]) -> list[Path]:
    matches: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(bundle_dir.rglob(pattern), key=lambda p: str(p).lower()):
            if path.is_file() and path not in seen:
                matches.append(path)
                seen.add(path)
    return matches


def _resolve_artifact(
    bundle_dir: Path,
    override: str | Path | None,
    patterns: tuple[str, ...],
    label: str,
) -> Path:
    if override:
        path = Path(override).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"{label} override not found: {path}")
        return path

    matches = _sorted_matches(bundle_dir, patterns)
    if not matches:
        raise FileNotFoundError(
            f"No {label} found in bundle '{bundle_dir}'. Expected one of: {', '.join(patterns)}"
        )
    return matches[0]


def discover_bundle_artifacts(
    bundle_dir: str | Path,
    *,
    vcf_override: str | Path | None = None,
    qc_override: str | Path | None = None,
    sample_sheet_override: str | Path | None = None,
) -> BundleArtifacts:
    """Discover the canonical artifact set for an Illumina export bundle."""

    bundle_dir = Path(bundle_dir).expanduser().resolve()
    if not bundle_dir.exists():
        raise FileNotFoundError(f"Bundle directory not found: {bundle_dir}")
    if not bundle_dir.is_dir():
        raise NotADirectoryError(f"Expected a bundle directory, got: {bundle_dir}")

    sample_sheet_path = _resolve_artifact(
        bundle_dir,
        sample_sheet_override,
        SAMPLE_SHEET_PATTERNS,
        "SampleSheet",
    )
    vcf_path = _resolve_artifact(bundle_dir, vcf_override, VCF_PATTERNS, "VCF")
    qc_path = _resolve_artifact(bundle_dir, qc_override, QC_PATTERNS, "QC metrics")

    mock_ica = bundle_dir / MOCK_ICA_FILENAME
    return BundleArtifacts(
        bundle_dir=bundle_dir,
        vcf_path=vcf_path,
        qc_path=qc_path,
        sample_sheet_path=sample_sheet_path,
        mock_ica_metadata_path=mock_ica if mock_ica.exists() else None,
    )


def is_recognizable_illumina_bundle(bundle_dir: str | Path) -> bool:
    """Heuristic used by the orchestrator to detect DRAGEN-like export folders."""

    try:
        bundle_dir = Path(bundle_dir).expanduser().resolve()
    except OSError:
        return False
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        return False

    has_sample_sheet = bool(_sorted_matches(bundle_dir, SAMPLE_SHEET_PATTERNS))
    has_vcf = bool(_sorted_matches(bundle_dir, VCF_PATTERNS))
    return has_sample_sheet and has_vcf


def _extract_data_section(lines: list[str]) -> list[str]:
    in_data = False
    data_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_data = line.lower() == "[data]"
            continue
        if in_data:
            data_lines.append(raw_line)
    return data_lines


def _normalize_sample_row(row: dict[str, str]) -> dict[str, str]:
    get = lambda *keys: next((row.get(key, "").strip() for key in keys if row.get(key, "").strip()), "")
    return {
        "sample_id": get("Sample_ID", "SampleID", "sample_id"),
        "sample_name": get("Sample_Name", "SampleName", "sample_name"),
        "sample_project": get("Sample_Project", "SampleProject", "sample_project"),
        "lane": get("Lane", "lane"),
        "index": get("index", "Index"),
        "index2": get("index2", "Index2"),
        "description": get("Description", "description"),
    }


def parse_sample_sheet(sample_sheet_path: str | Path) -> list[dict[str, str]]:
    """Parse a standard Illumina SampleSheet and return normalized sample rows."""

    sample_sheet_path = Path(sample_sheet_path)
    lines = sample_sheet_path.read_text(encoding="utf-8").splitlines(keepends=True)
    data_lines = _extract_data_section(lines)

    if data_lines:
        reader = csv.DictReader(data_lines)
    else:
        reader = csv.DictReader(lines)

    normalized_rows: list[dict[str, str]] = []
    for row in reader:
        cleaned = {
            (key or "").strip(): (value or "").strip()
            for key, value in row.items()
        }
        if not any(cleaned.values()):
            continue
        normalized = _normalize_sample_row(cleaned)
        if not normalized["sample_id"]:
            raise ValueError(
                f"SampleSheet row missing Sample_ID in '{sample_sheet_path.name}': {cleaned}"
            )
        normalized_rows.append(normalized)

    if not normalized_rows:
        raise ValueError(f"No sample rows found in SampleSheet: {sample_sheet_path}")
    return normalized_rows


def _normalize_qc_keys(raw: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        "run_id": ("run_id", "runId", "analysis_id", "analysisId"),
        "instrument": ("instrument", "instrument_name", "instrumentName", "sequencer"),
        "yield_gb": ("yield_gb", "yieldGb", "yield_gbases", "yield"),
        "percent_q30": ("percent_q30", "percentQ30", "q30_percentage", "q30Percent"),
        "pf_reads": ("pf_reads", "pfReads", "pass_filter_reads", "passFilterReads"),
        "cluster_density_k_mm2": (
            "cluster_density_k_mm2",
            "clusterDensityKmm2",
            "cluster_density",
        ),
    }
    normalized: dict[str, Any] = {}
    for target, keys in aliases.items():
        for key in keys:
            if key in raw:
                normalized[target] = raw[key]
                break
    normalized["raw_metrics"] = raw
    return normalized


def parse_qc_metrics(qc_path: str | Path) -> dict[str, Any]:
    """Parse QC metrics from JSON or CSV into a stable summary shape."""

    qc_path = Path(qc_path)
    suffix = qc_path.suffix.lower()

    if suffix == ".json":
        try:
            raw = json.loads(qc_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed QC metrics JSON: {qc_path}") from exc

        if isinstance(raw, dict):
            if "summary" in raw and isinstance(raw["summary"], dict):
                return _normalize_qc_keys(raw["summary"])
            return _normalize_qc_keys(raw)
        raise ValueError(f"Unsupported QC metrics JSON structure in {qc_path}")

    if suffix == ".csv":
        rows = list(csv.DictReader(qc_path.read_text(encoding="utf-8").splitlines()))
        if not rows:
            raise ValueError(f"QC metrics CSV is empty: {qc_path}")

        if {"metric", "value"}.issubset(rows[0].keys()):
            raw = {row["metric"].strip(): row["value"].strip() for row in rows if row.get("metric")}
            return _normalize_qc_keys(raw)

        first_row = rows[0]
        if len(first_row) == 1:
            raise ValueError(f"QC metrics CSV is malformed: {qc_path}")
        return _normalize_qc_keys({k.strip(): v.strip() for k, v in first_row.items() if k})

    raise ValueError(f"Unsupported QC metrics format: {qc_path.suffix}")


def summarize_sample_sheet(sample_rows: list[dict[str, str]]) -> dict[str, Any]:
    """Compute a stable summary used in reports and manifests."""

    sample_ids = [row["sample_id"] for row in sample_rows]
    projects = sorted({row["sample_project"] for row in sample_rows if row["sample_project"]})
    return {
        "sample_count": len(sample_rows),
        "sample_ids": sample_ids,
        "sample_projects": projects,
    }
