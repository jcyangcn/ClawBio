"""On-demand 1000G LD region fetch + plink2 r² compute.

Replaces the multi-GB pre-baked PLINK2 panel dependency with a per-region
tabix fetch from EBI's 1000G FTP, super-pop-filtered, run through plink2 to
get r² between the lead and every variant in the window. Caches both the
region VCF and the LD output to `~/.clawbio/locuscompare_cache/1000g/`.

This means a fresh ClawBio install can render LD-colored plots without
asking the user to download a 3 GB PLINK2 panel first — the EBI fetch is a
~5-50 MB byte-range request per locus.

Constructor arg `super_pop` selects which 1000G samples to keep
(EUR/AFR/AMR/EAS/SAS). The sample-to-super-pop mapping is fetched once
(small TSV) and cached.

License: 1000G data are open-access with attribution (Auton 2015, Clarke
2017). plink2 binary is GPL-3 (subprocess invocation only; no GPL
contamination of MIT-licensed locuscompare code).
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import requests

# 1000G GRCh38 phased genotypes (NYGC re-imputed, 2019-03-12 release).
# These are the canonical liftover-free GRCh38 panel for LD reference work.
ONEKG_VCF_URL_TEMPLATE = (
    "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections/"
    "1000_genomes_project/release/20190312_biallelic_SNV_and_INDEL/"
    "ALL.chr{chrom}.shapeit2_integrated_snvindels_v2a_27022019.GRCh38.phased.vcf.gz"
)
ONEKG_PANEL_URL = (
    "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/"
    "integrated_call_samples_v3.20130502.ALL.panel"
)

DEFAULT_CACHE_DIR = Path(
    os.environ.get(
        "LOCUSCOMPARE_CACHE_DIR",
        Path.home() / ".clawbio" / "locuscompare_cache",
    )
) / "1000g"

DEFAULT_PLINK2_BIN = os.environ.get("PLINK2_BIN", "plink2")

PANEL_ID_DEFAULT = "1000g_phase3_v5b_grch38_basic"
PANEL_VERSION_DEFAULT = "5b_remote_2019_03_12"


@dataclass
class OnDemandLDPair:
    partner_variant_id: str
    r2: float
    dprime: float | None = None


@dataclass
class OnDemandLDResult:
    panel_id: str
    panel_version: str
    super_pop: str
    plink2_version: str
    chromosome: str
    lead_variant_id: str
    window_bp: int
    n_partners_requested: int
    n_partners_returned: int
    pairs: list[OnDemandLDPair]
    fetched_at_utc: str
    notes: list[str] = field(default_factory=list)


class OnDemandLDError(Exception):
    """Raised when on-demand LD compute cannot proceed."""


def _detect_plink2_version(plink2_bin: str) -> str:
    if shutil.which(plink2_bin) is None:
        raise OnDemandLDError(
            f"plink2 binary not found at `{plink2_bin}`. "
            "Install via `brew install --HEAD brewsci/bio/plink2` (macOS, brewsci tap) "
            "or `apt-get install plink2` (Linux); if neither package is available, "
            "download the macOS / Linux binary directly from "
            "https://www.cog-genomics.org/plink/2.0/. "
            "Then either ensure it's on PATH or set PLINK2_BIN to its absolute path."
        )
    proc = subprocess.run(
        [plink2_bin, "--version"], capture_output=True, text=True, check=False, timeout=10,
    )
    line = (proc.stdout or proc.stderr).strip().splitlines()
    return line[0] if line else "unknown"


def _resolve_super_pop_samples(super_pop: str, cache_dir: Path) -> list[str]:
    """Fetch (or read from cache) the 1000G sample → super_pop mapping;
    return the list of sample IDs in the requested super-pop.
    """
    panel_path = cache_dir / "integrated_call_samples_v3.20130502.ALL.panel"
    if not panel_path.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        resp = requests.get(ONEKG_PANEL_URL, timeout=60)
        resp.raise_for_status()
        panel_path.write_bytes(resp.content)
    samples: list[str] = []
    with panel_path.open() as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row.get("super_pop") == super_pop:
                samples.append(row["sample"])
    if not samples:
        raise OnDemandLDError(
            f"no samples found for super_pop={super_pop!r} in {panel_path}. "
            f"Valid super-pop codes: EUR, AFR, AMR, EAS, SAS."
        )
    return samples


def _fetch_region_vcf(
    chromosome: str,
    start_bp: int,
    end_bp: int,
    cache_dir: Path,
) -> Path:
    """Tabix-fetch a 1000G region VCF via pysam. Cached per (chrom, start, end)."""
    chrom_bare = chromosome.removeprefix("chr") if chromosome.startswith("chr") else chromosome
    cache_dir.mkdir(parents=True, exist_ok=True)
    region_vcf = cache_dir / f"chr{chrom_bare}_{start_bp}_{end_bp}.vcf.gz"
    if region_vcf.is_file() and region_vcf.stat().st_size > 0:
        return region_vcf

    import pysam  # lazy import; pysam is optional for non-LD-coloring runs

    url = ONEKG_VCF_URL_TEMPLATE.format(chrom=chrom_bare)
    tmp_vcf = region_vcf.with_suffix(".tmp.vcf")
    try:
        # pysam.VariantFile supports remote tabix-indexed VCFs; the .tbi at the
        # source URL is loaded transparently.
        with pysam.VariantFile(url) as src:
            # Use src's contig naming; 1000G files use `1`, `2`, ... (no `chr`).
            chrom_used = chrom_bare
            with tmp_vcf.open("w") as out:
                out.write(str(src.header))
                for rec in src.fetch(chrom_used, start_bp, end_bp):
                    out.write(str(rec))
    except Exception as e:
        if tmp_vcf.exists():
            tmp_vcf.unlink()
        raise OnDemandLDError(
            f"tabix-fetch from {url} for {chrom_bare}:{start_bp}-{end_bp} failed: {e!s}"
        ) from e

    # bgzip + tabix-index the cached region VCF for downstream tools, but
    # plink2 reads either form, so we don't strictly need to. Keep as plain
    # .vcf for simplicity; plink2 handles gzip too if we name it .vcf.gz.
    # Use Python's gzip to compress instead of relying on bgzip availability.
    import gzip
    with tmp_vcf.open("rb") as f_in, gzip.open(region_vcf, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    tmp_vcf.unlink()
    return region_vcf


class OnDemand1000GLDClient:
    """LD client that fetches per-region 1000G VCFs on demand from EBI FTP.

    Drop-in alternative to skills.execution.ld_reference.Plink2LDClient — same
    `r2_with_lead(...)` interface, no pre-baked panel required.
    """

    def __init__(
        self,
        super_pop: str = "EUR",
        plink2_bin: str = DEFAULT_PLINK2_BIN,
        cache_dir: Path | None = None,
        panel_id: str = PANEL_ID_DEFAULT,
        panel_version: str = PANEL_VERSION_DEFAULT,
    ) -> None:
        self.super_pop = super_pop
        self.plink2_bin = plink2_bin
        self.cache_dir = (cache_dir or DEFAULT_CACHE_DIR).expanduser()
        self.panel_id = panel_id
        self.panel_version = panel_version
        self.plink2_version = _detect_plink2_version(plink2_bin)
        self._super_pop_samples: list[str] | None = None

    def _samples(self) -> list[str]:
        if self._super_pop_samples is None:
            self._super_pop_samples = _resolve_super_pop_samples(self.super_pop, self.cache_dir)
        return self._super_pop_samples

    def r2_with_lead(
        self,
        lead: str,
        partners: Iterable[str],
        chromosome: str,
        window_bp: int,
    ) -> OnDemandLDResult:
        """Compute r² between `lead` and each `partner` via on-demand region fetch.

        `lead` and `partners` use OT-style chr_pos_ref_alt ids. `chromosome`
        is the chromosome name (with or without `chr` prefix). `window_bp`
        determines the region around the lead to fetch.
        """
        partner_list = list(partners)
        notes: list[str] = []
        if not partner_list:
            return OnDemandLDResult(
                panel_id=self.panel_id, panel_version=self.panel_version,
                super_pop=self.super_pop, plink2_version=self.plink2_version,
                chromosome=chromosome, lead_variant_id=lead, window_bp=window_bp,
                n_partners_requested=0, n_partners_returned=0, pairs=[],
                fetched_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                notes=["no partners requested"],
            )

        chrom_bare = chromosome.removeprefix("chr") if chromosome.startswith("chr") else chromosome
        # Parse lead position from variant_id (chr_pos_ref_alt)
        lead_parts = lead.split("_")
        if len(lead_parts) < 4:
            raise OnDemandLDError(f"cannot parse lead variant id: {lead!r}")
        lead_pos = int(lead_parts[1])
        half = max(window_bp // 2, 1)
        start_bp = max(0, lead_pos - half)
        end_bp = lead_pos + half

        region_vcf = _fetch_region_vcf(chrom_bare, start_bp, end_bp, self.cache_dir)
        notes.append(f"fetched 1000G region VCF to {region_vcf}")

        samples = self._samples()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            keep_path = td_path / "keep.txt"
            with keep_path.open("w") as f:
                # plink2 --vcf sets FID=0 for every sample by default.
                # --keep expects "FID\tIID" pairs.
                for s in samples:
                    f.write(f"0\t{s}\n")
            out_prefix = td_path / "ld_out"
            # plink2's variant ID for VCF input is the VCF ID column (3rd col).
            # The 1000G GRCh38 VCFs use rsids there, NOT chr:pos:ref:alt — so
            # we can't --ld-snp by chr_pos_ref_alt directly. Workaround: use
            # --set-all-var-ids '@:#:$r:$a' to rewrite IDs into chr:pos:ref:alt,
            # then ld-snp matches our partner ids (after _-to-: conversion).
            sep = ":"
            lead_panel = lead.replace("_", sep, 3)
            partner_panel = [p.replace("_", sep, 3) for p in partner_list]

            extract_path = td_path / "extract.txt"
            with extract_path.open("w") as f:
                f.write(lead_panel + "\n")
                for p in partner_panel:
                    f.write(p + "\n")

            cmd = [
                self.plink2_bin,
                "--vcf", str(region_vcf),
                "--keep", str(keep_path),
                "--set-all-var-ids", "@:#:$r:$a",
                "--new-id-max-allele-len", "100", "missing",
                "--extract", str(extract_path),
                "--r2-unphased",
                "--ld-snp", lead_panel,
                "--ld-window-r2", "0",
                "--out", str(out_prefix),
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=300)
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                raise OnDemandLDError(f"plink2 invocation failed: {e!s}") from e
            if proc.returncode != 0:
                raise OnDemandLDError(
                    f"plink2 exited with code {proc.returncode}. "
                    f"stderr (truncated): {(proc.stderr or '')[:1000]}"
                )

            vcor_path: Path | None = None
            for suffix in (".vcor", ".vcor2"):
                cand = Path(f"{out_prefix}{suffix}")
                if cand.exists():
                    vcor_path = cand
                    break
            if vcor_path is None:
                raise OnDemandLDError(
                    f"plink2 produced no .vcor / .vcor2 at {out_prefix}; "
                    f"stdout (truncated): {(proc.stdout or '')[:1000]}"
                )
            pairs = _parse_vcor(vcor_path, lead_panel, notes)
            for p in pairs:
                p.partner_variant_id = p.partner_variant_id.replace(":", "_", 3)

        return OnDemandLDResult(
            panel_id=self.panel_id, panel_version=self.panel_version,
            super_pop=self.super_pop, plink2_version=self.plink2_version,
            chromosome=chrom_bare, lead_variant_id=lead, window_bp=window_bp,
            n_partners_requested=len(partner_list), n_partners_returned=len(pairs),
            pairs=pairs,
            fetched_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            notes=notes,
        )


def _parse_vcor(path: Path, lead: str, notes: list[str]) -> list[OnDemandLDPair]:
    out: list[OnDemandLDPair] = []
    with path.open() as f:
        reader = csv.reader(f, delimiter="\t")
        header: list[str] | None = None
        for row in reader:
            if not row:
                continue
            if header is None:
                header = [c.lstrip("#") for c in row]
                continue
            r = dict(zip(header, row))
            id_a = r.get("ID_A") or ""
            id_b = r.get("ID_B") or ""
            partner = id_b if id_a == lead else (id_a if id_b == lead else None)
            if partner is None:
                continue
            r2_str = r.get("UNPHASED_R2") or r.get("R2") or ""
            if not r2_str or r2_str == "NA":
                notes.append(f"missing r² for partner {partner}")
                continue
            try:
                r2 = float(r2_str)
            except ValueError:
                continue
            out.append(OnDemandLDPair(partner_variant_id=partner, r2=r2))
    return out
