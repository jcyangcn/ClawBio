"""On-demand GENCODE region fetch via Ensembl REST API.

Avoids the multi-GB local-GTF dependency by HTTP-fetching against Ensembl's
`overlap/region` endpoint. Caches results to
`~/.clawbio/locuscompare_cache/gencode/<chr>_<start>_<end>.json` so repeated
runs over the same region are offline.

Returns the same `RegionGenesResult` shape that the in-repo GTF parser produces,
so the renderer's gene-track code is source-agnostic.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import requests

ENSEMBL_REST_BASE = "https://rest.ensembl.org"
DEFAULT_CACHE_DIR = Path(
    os.environ.get(
        "LOCUSCOMPARE_CACHE_DIR",
        Path.home() / ".clawbio" / "locuscompare_cache",
    )
) / "gencode"
DEFAULT_RELEASE_LABEL = "Ensembl REST (GRCh38)"


def fetch_region_genes_remote(
    chromosome: str,
    start_bp: int,
    end_bp: int,
    *,
    biotypes: Iterable[str] | None = ("protein_coding",),
    cache_dir: Path | None = None,
    timeout_s: float = 30.0,
):
    """Fetch genes + exons overlapping a window from Ensembl REST.

    Returns a tuple `(genes, release_meta, notes)` where `genes` is a list of
    objects compatible with `skills.execution.gencode_gtf.region_genes.Gene`
    (duck-typed: gene_id, gene_symbol, biotype, chromosome, start, end,
    strand, exons[(start, end)]).

    The function lazy-imports the local Gene/Exon types so this module stays
    independent of the in-repo GENCODE skill (helpful for the upstream PR
    inlining in task #18).
    """
    from skills.execution.gencode_gtf.region_genes import Gene, Exon

    chrom_bare = chromosome.removeprefix("chr") if chromosome.startswith("chr") else chromosome
    cache_dir = (cache_dir or DEFAULT_CACHE_DIR).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = f"{chrom_bare}_{start_bp}_{end_bp}.json"
    cache_path = cache_dir / cache_key

    rows: list[dict]
    if cache_path.is_file():
        rows = json.loads(cache_path.read_text())
        notes = [f"gene track from Ensembl REST cache at {cache_path}"]
    else:
        url = f"{ENSEMBL_REST_BASE}/overlap/region/human/{chrom_bare}:{start_bp}-{end_bp}"
        params = {"feature": ["gene", "exon"]}
        headers = {"Accept": "application/json"}
        resp = requests.get(url, params=params, headers=headers, timeout=timeout_s)
        resp.raise_for_status()
        rows = resp.json()
        cache_path.write_text(json.dumps(rows))
        notes = [f"gene track fetched from Ensembl REST and cached to {cache_path}"]

    biotypes_filter = tuple(biotypes) if biotypes else ()

    # Group by gene id; exons attach to genes via Parent transcript -> gene.
    # First pass: collect genes; build a transcript->gene map.
    genes_by_id: dict[str, Gene] = {}
    transcript_to_gene: dict[str, str] = {}

    for row in rows:
        if row.get("feature_type") != "gene":
            continue
        gid = row.get("gene_id") or row.get("id") or ""
        if not gid:
            continue
        biotype = row.get("biotype") or ""
        if biotypes_filter and biotype not in biotypes_filter:
            continue
        # Skip genes whose canonical span doesn't actually overlap our window.
        gene_start = int(row["start"])
        gene_end = int(row["end"])
        if gene_end < start_bp or gene_start > end_bp:
            continue
        strand = "+" if int(row.get("strand", 1)) >= 0 else "-"
        gene = Gene(
            gene_id=gid,
            gene_symbol=row.get("external_name", "") or "",
            biotype=biotype,
            chromosome=chrom_bare,
            start=gene_start,
            end=gene_end,
            strand=strand,
        )
        genes_by_id[gid] = gene
        canonical_tx = row.get("canonical_transcript", "")
        if canonical_tx:
            # canonical_transcript may include version: ENST...4 -> ENST...
            tx_bare = canonical_tx.split(".")[0]
            transcript_to_gene[tx_bare] = gid

    # Second pass: gather exons per transcript that maps to one of our genes.
    exons_by_gene: dict[str, list[Exon]] = defaultdict(list)
    for row in rows:
        if row.get("feature_type") != "exon":
            continue
        parent = row.get("Parent", "")
        parent_bare = parent.split(".")[0]
        gene_id = transcript_to_gene.get(parent_bare)
        if not gene_id:
            continue
        exons_by_gene[gene_id].append(
            Exon(
                start=int(row["start"]),
                end=int(row["end"]),
                exon_number=int(row.get("rank")) if row.get("rank") is not None else None,
                transcript_id=parent_bare,
            )
        )

    for gid, exons in exons_by_gene.items():
        if gid in genes_by_id:
            genes_by_id[gid].exons = exons

    release_meta = {
        "release_label": DEFAULT_RELEASE_LABEL,
        "source_path": ENSEMBL_REST_BASE,
        "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return list(genes_by_id.values()), release_meta, notes
