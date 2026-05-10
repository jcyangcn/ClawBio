"""Tier-2 LocusCompare orchestrator.

Wires the three execution skills (eQTL Catalogue, GWAS Catalog harmonised, LD
reference via plink2 + 1000G) + the wald_ratio harmoniser + the
render_full_locuscompare renderer + the manifest block builder, all driven by
a small per-row StudyIdMapping that resolves OT studyIds to the
upstream-source identifiers each fetcher needs.

Inputs:
- lead variant id (chr_pos_ref_alt, GRCh38)
- chromosome + lead_position_bp + window_bp
- StudyIdMapping (one per Tier-1 coloc row that gates Tier-2 rendering)
- pre-built clients (so callers can mock for tests)

Outputs:
- a PNG at the requested out_path (the 4-panel render_full_locuscompare figure)
- a `regional_locuscompare` manifest block documenting the four input
  panels, the lead variant, and the LD reference panel.

If `ld_client` is None (e.g. plink2 not installed in the current environment),
the renderer still produces all four panels but with r² coloring substituted
by a uniform grey; the manifest block records `ld_panel: "none"` and a caveat
in the caveats list.

When the eQTL Catalogue or GWAS Catalog source can't resolve the studyId, the
orchestrator raises Tier2NotAvailable so the caller can route to a credible-
set-only fallback.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sys
from pathlib import Path

import yaml

# Resolve sibling-skill imports for the fork's flat layout. This skill
# orchestrates eqtl-catalogue-region-fetch, gwas-catalogue-region-fetch,
# and ld-1000g-region-compute, which live as sibling skill directories
# under skills/ in the same checkout. Inject each sibling-skill dir
# onto sys.path so the modules import cleanly.
_SKILL_DIR = Path(__file__).resolve().parent
_SKILLS_ROOT = _SKILL_DIR.parent
for _p in (
    _SKILL_DIR,
    _SKILLS_ROOT / "eqtl-catalogue-region-fetch",
    _SKILLS_ROOT / "gwas-catalogue-region-fetch",
    _SKILLS_ROOT / "ld-1000g-region-compute",
):
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Sibling-skill imports (one per upstream primitive in the suite).
from eqtl_catalogue_region_fetch import (
    EQTLCatalogueAPIError,
    EQTLCatalogueClient,
    RegionResult as EQTLRegionResult,
)
from gwas_catalog_region_fetch import (
    GWASCatalogClient,
    GWASCatalogFetchError,
    RegionResult as GWASRegionResult,
)
from ld_1000g_region_compute import (
    LDComputeError,
    Plink2LDClient,
    SuperPop,
)
from regional_plot import (
    GeneTrackEntry,
    LocusVariant,
    RegionalLocusCompareInput,
    harmonise_regions_for_locuscompare,
    render_full_locuscompare,
)

# GENCODE gene-track parser: bundled embedded helper, lives next to this
# script under _fetchers/. The on-demand Ensembl-REST fetcher avoids the
# multi-GB local-GTF dependency.
from _fetchers.gencode_ondemand import (
    GTFFetchError,
    fetch_region_genes,
)
# Manifest builder stays in target_validation (it's OT-shaped tooling, not
# part of the generic locuscompare contribution).
from skills.decision.target_validation.renderers import (
    build_regional_locuscompare_block,
)


@dataclass
class StudyIdMapping:
    """Per-row resolution of OT studyIds to upstream-source ids.

    The OT studyId of the QTL credible set (e.g.
    `quach_2016_ge_monocyte_iav_ensg00000115808`) maps to an eQTL Catalogue
    `dataset_id` (e.g. `QTD000110`). The OT outcome studyId (e.g.
    `FINNGEN_R12_I9_HEARTFAIL`) maps to a GWAS Catalog `accession` (e.g.
    `GCST90475990`).

    The canonical mapping table is auto-resolvable for most rows; manual
    lookup is acceptable as a starting point. This dataclass is the row-
    level contract; the lookup mechanism (auto or manual) is the
    orchestrator's call.

    `outcome_trait_label` is human-readable text for the panel title (e.g.
    "hypertrophic cardiomyopathy"). The exposure side's tissue / condition /
    quant labels come from the eQTL Catalogue dataset metadata at fetch time
    so they don't need to be repeated in the YAML lookup.
    """
    ot_left_study_id: str
    eqtl_catalogue_dataset_id: str
    ot_right_study_id: str
    gwas_catalog_accession: str
    ancestry_left: str = "EUR"
    ancestry_right: str = "EUR"
    outcome_trait_label: str = ""
    exposure_gene_symbol: str = ""
    notes: list[str] = field(default_factory=list)


class Tier2NotAvailable(Exception):
    """Raised when one of the upstream sources cannot resolve the requested
    studyId or region. Caller should fall back to a credible-set-only
    rendering path.
    """


@dataclass
class Tier2Result:
    plot_path: Path
    manifest_block: dict[str, Any]
    n_pairs: int
    n_palindromic_excluded: int
    notes: list[str]


@dataclass
class LocusCompareSpec:
    """Generic input to the locuscompare core. Decoupled from OT.

    Built either directly (non-OT entry vectors: gwas-lookup follow-up,
    fine-mapping chain, raw user-supplied harmonised TSVs) or by the OT-shim
    wrapper `render_tier2_for_lead` which translates a `StudyIdMapping` into
    this generic spec.
    """
    lead_variant_id: str
    chromosome: str
    lead_position_bp: int
    window_bp: int

    eqtl_dataset_id: str
    molecular_trait_id: str | None

    gwas_accession: str

    exposure_gene_symbol: str = ""
    outcome_trait_label: str = ""

    exposure_id_extra: str = ""
    outcome_id_extra: str = ""
    provenance_prefix: str = ""

    release_tag: str = ""

    notes: list[str] = field(default_factory=list)

    super_pop: SuperPop = SuperPop.EUR
    intersected_pip_product: float | None = None
    extra_caveats: list[str] = field(default_factory=list)
    gencode_gtf_path: Path | None = None
    gene_biotypes: tuple[str, ...] | None = ("protein_coding",)
    # Optional pre-fetched gene track. When provided, bypasses the local-GTF
    # path entirely. Use this to inject genes resolved from a remote source
    # (e.g. locuscompare's Ensembl REST on-demand fetcher) without writing a
    # synthetic GTF to disk.
    prefetched_gene_track: list | None = None


def render_locuscompare_for_lead(
    spec: LocusCompareSpec,
    *,
    eqtl_client: EQTLCatalogueClient,
    gwas_client: GWASCatalogClient,
    ld_client: Plink2LDClient | None,
    out_path: Path,
) -> Tier2Result:
    """Run the full locuscompare pipeline for one lead variant. Generic, no OT.

    Steps:
    1. Fetch exposure region from eQTL Catalogue.
    2. Fetch outcome region from GWAS Catalog harmonised.
    3. Compute r² between the lead and every harmonised partner via plink2.
    4. Join + harmonise per skills.knowledge.wald_ratio.harmonise_regions_for_locuscompare.
    5. Render the 4-panel figure.
    6. Build the manifest block.
    """
    return _render_for_spec(
        spec=spec,
        eqtl_client=eqtl_client,
        gwas_client=gwas_client,
        ld_client=ld_client,
        out_path=out_path,
    )


def render_tier2_for_lead(
    *,
    lead_variant_id: str,
    chromosome: str,
    lead_position_bp: int,
    window_bp: int,
    study_mapping: StudyIdMapping,
    eqtl_client: EQTLCatalogueClient,
    gwas_client: GWASCatalogClient,
    ld_client: Plink2LDClient | None,
    out_path: Path,
    ot_release: str,
    super_pop: SuperPop = SuperPop.EUR,
    intersected_pip_product: float | None = None,
    extra_caveats: list[str] | None = None,
    gencode_gtf_path: Path | None = None,
    gene_biotypes: tuple[str, ...] | None = ("protein_coding",),
) -> Tier2Result:
    """OT-shim wrapper. Resolves an OT row into a generic LocusCompareSpec
    and delegates to `render_locuscompare_for_lead`.

    Existing callers (tier2_cli, tests, downstream orchestrators) continue
    to work unchanged. The OT-specific work is concentrated here:
    - Extract ENSG from OT QTL studyId for the eQTL Cat molecular_trait_id.
    - Trigger the FinnGen ancestry caveat.
    - Format OT-flavored label / provenance strings.
    """
    molecular_trait_id = _extract_ensg_from_ot_study_id(study_mapping.ot_left_study_id)

    runtime_caveats = list(extra_caveats or [])
    if "finngen" in study_mapping.ot_right_study_id.lower():
        runtime_caveats.append(
            "FinnGen Finnish-EUR; 1000G EUR proxy used. "
            "Common-variant LD agrees within ~0.05 r² per Locke 2019."
        )

    spec = LocusCompareSpec(
        lead_variant_id=lead_variant_id,
        chromosome=chromosome,
        lead_position_bp=lead_position_bp,
        window_bp=window_bp,
        eqtl_dataset_id=study_mapping.eqtl_catalogue_dataset_id,
        molecular_trait_id=molecular_trait_id,
        gwas_accession=study_mapping.gwas_catalog_accession,
        exposure_gene_symbol=study_mapping.exposure_gene_symbol,
        outcome_trait_label=study_mapping.outcome_trait_label,
        exposure_id_extra=f" (OT studyId {study_mapping.ot_left_study_id})",
        outcome_id_extra=f" (OT studyId {study_mapping.ot_right_study_id})",
        provenance_prefix=f"OT release: {ot_release} | ",
        release_tag=ot_release,
        notes=list(study_mapping.notes),
        super_pop=super_pop,
        intersected_pip_product=intersected_pip_product,
        extra_caveats=runtime_caveats,
        gencode_gtf_path=gencode_gtf_path,
        gene_biotypes=gene_biotypes,
    )
    return _render_for_spec(
        spec=spec,
        eqtl_client=eqtl_client,
        gwas_client=gwas_client,
        ld_client=ld_client,
        out_path=out_path,
    )


def _render_for_spec(
    *,
    spec: LocusCompareSpec,
    eqtl_client: EQTLCatalogueClient,
    gwas_client: GWASCatalogClient,
    ld_client: Plink2LDClient | None,
    out_path: Path,
) -> Tier2Result:
    """Core implementation shared by both entry points.

    Lifted from the original render_tier2_for_lead body, with all
    `study_mapping.X` references replaced by `spec.X` reads.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lead_variant_id = spec.lead_variant_id
    chromosome = spec.chromosome
    window_bp = spec.window_bp
    lead_position_bp = spec.lead_position_bp
    super_pop = spec.super_pop
    intersected_pip_product = spec.intersected_pip_product
    gencode_gtf_path = spec.gencode_gtf_path
    gene_biotypes = spec.gene_biotypes

    half = max(window_bp // 2, 1)
    start_bp = max(0, lead_position_bp - half)
    end_bp = lead_position_bp + half

    notes: list[str] = []
    extra_caveats = list(spec.extra_caveats)

    eqtl_dataset_id = spec.eqtl_dataset_id
    gwas_accession = spec.gwas_accession
    molecular_trait_id = spec.molecular_trait_id

    # 1. Exposure region (eQTL Catalogue).
    try:
        exposure: EQTLRegionResult = eqtl_client.fetch_region(
            dataset_id=eqtl_dataset_id,
            chromosome=chromosome.lstrip("chr"),
            start_bp=start_bp,
            end_bp=end_bp,
            molecular_trait_id=molecular_trait_id,
        )
    except EQTLCatalogueAPIError as e:
        raise Tier2NotAvailable(
            f"eQTL Catalogue cannot resolve dataset {eqtl_dataset_id}: {e!s}"
        ) from e
    if exposure.n_variants == 0:
        raise Tier2NotAvailable(
            f"eQTL Catalogue returned zero variants for "
            f"{eqtl_dataset_id} at {chromosome}:{start_bp}-{end_bp}"
        )

    # 2. Outcome region (GWAS Catalog harmonised).
    try:
        outcome: GWASRegionResult = gwas_client.fetch_region(
            accession=gwas_accession,
            chromosome=chromosome.lstrip("chr"),
            start_bp=start_bp,
            end_bp=end_bp,
        )
    except GWASCatalogFetchError as e:
        raise Tier2NotAvailable(
            f"GWAS Catalog cannot resolve accession {gwas_accession}: {e!s}"
        ) from e
    if outcome.n_variants == 0:
        raise Tier2NotAvailable(
            f"GWAS Catalog returned zero variants for "
            f"{gwas_accession} at {chromosome}:{start_bp}-{end_bp}"
        )

    # 3. LD r² (optional; gracefully degrade when plink2 not installed).
    r2_by_variant: dict[str, float] = {lead_variant_id: 1.0}
    plink2_version = ""
    panel_id = "none"
    panel_version = ""
    if ld_client is not None:
        partner_ids = [v.variant_id for v in exposure.variants if v.variant_id != lead_variant_id]
        try:
            ld = ld_client.r2_with_lead(
                lead=lead_variant_id,
                partners=partner_ids,
                chromosome=chromosome,
                window_bp=window_bp,
            )
        except LDComputeError as e:
            notes.append(f"LD computation failed: {e!s}; rendering without LD coloring")
            extra_caveats.append("LD r² unavailable (compute failed); points show as grey")
        else:
            for pair in ld.pairs:
                r2_by_variant[pair.partner_variant_id] = pair.r2
            plink2_version = ld.plink2_version
            panel_id = ld.panel_id
            panel_version = ld.panel_version
    else:
        notes.append("ld_client is None; rendering without LD coloring")
        extra_caveats.append("LD r² unavailable (no plink2 client provided); points show as grey")

    # 4. Harmonise + join.
    pairs = harmonise_regions_for_locuscompare(
        exposure_variants=exposure.variants,
        outcome_variants=outcome.variants,
        r2_by_variant=r2_by_variant,
        lead_variant_id=lead_variant_id,
    )
    if not pairs:
        raise Tier2NotAvailable(
            f"no joinable variants between exposure {eqtl_dataset_id} "
            f"and outcome {gwas_accession} at "
            f"{chromosome}:{start_bp}-{end_bp}"
        )

    n_palindromic = sum(1 for p in pairs if p.palindromic_excluded)

    # 5. Render.
    fetched_at = _now_utc()
    pip_label = (
        f" (intersected; PIP_L x PIP_R = {intersected_pip_product:.4f})"
        if intersected_pip_product is not None else ""
    )
    # Convert the raw exposure / outcome region rows to wald_ratio LocusVariant
    # so the renderer's per-side manhattan tracks can show ALL variants in the
    # source's region (LocusZoom-style "bottom of plot fills the window"),
    # not just the joined intersection.
    exposure_track = [_eqtl_to_locus_variant(v) for v in exposure.variants]
    outcome_track = [_gwas_to_locus_variant(v) for v in outcome.variants]

    exposure_short_label, outcome_short_label = _build_short_labels_from_spec(
        spec, exposure.release,
    )

    # Gene track. Three sources, in priority order:
    # 1. spec.prefetched_gene_track — caller-supplied list (e.g. from the
    #    locuscompare CLI's Ensembl REST on-demand fetcher).
    # 2. local GTF path on spec.gencode_gtf_path (legacy / power-user).
    # 3. fall back to no gene track + a caveat (graceful degradation).
    gene_track: list[GeneTrackEntry] = []
    if spec.prefetched_gene_track is not None:
        gene_track = list(spec.prefetched_gene_track)
    elif gencode_gtf_path is not None:
        try:
            genes_result = fetch_region_genes(
                chromosome=chromosome.lstrip("chr"),
                start_bp=start_bp, end_bp=end_bp,
                gtf_path=gencode_gtf_path,
                biotypes=gene_biotypes,
            )
            gene_track = [
                GeneTrackEntry(
                    gene_symbol=g.gene_symbol,
                    start=g.start, end=g.end, strand=g.strand,
                    exons=[(e.start, e.end) for e in g.exons],
                    biotype=g.biotype,
                )
                for g in genes_result.genes
            ]
        except GTFFetchError as e:
            notes.append(f"gene track unavailable: {e!s}")
            extra_caveats.append("gene track unavailable (GTF not installed)")
    else:
        notes.append("no gene track source supplied")
        extra_caveats.append("gene track unavailable (no source supplied)")

    inp = RegionalLocusCompareInput(
        pairs=pairs,
        lead_variant_id=lead_variant_id,
        chromosome=chromosome,
        window_bp=window_bp,
        ld_panel_label=(
            f"{panel_id} ({super_pop.value}); plink2 {plink2_version}"
            if ld_client is not None and panel_id != "none"
            else "no LD reference (plot rendered without LD coloring)"
        ),
        window_label=f"+/-{window_bp // 1000} kb of lead {lead_variant_id}{pip_label}",
        exposure_label=(
            f"eQTL Catalogue {exposure.release.dataset_release or 'v7+'}; "
            f"study {eqtl_dataset_id}{spec.exposure_id_extra}"
        ),
        outcome_label=(
            f"GWAS Catalog harmonised; study {gwas_accession}{spec.outcome_id_extra}"
        ),
        provenance_label=f"{spec.provenance_prefix}Rendered: {fetched_at}",
        caveats=extra_caveats + spec.notes,
        exposure_track_variants=exposure_track,
        outcome_track_variants=outcome_track,
        r2_by_variant=dict(r2_by_variant),
        exposure_short_label=exposure_short_label,
        outcome_short_label=outcome_short_label,
        gene_track=gene_track,
    )
    render_full_locuscompare(inp, out_path)

    # 6. Manifest block.
    block = build_regional_locuscompare_block(
        ot_release=spec.release_tag,
        exposure_source="eqtl_catalogue",
        exposure_source_release=exposure.release.dataset_release or "v7+",
        exposure_study_id=eqtl_dataset_id,
        exposure_harmonisation_version="",  # eQTL Catalogue does not surface this
        outcome_source="gwas_catalog_harmonised",
        outcome_source_release=outcome.release.fetched_at_utc.split("T")[0],
        outcome_study_id=gwas_accession,
        outcome_harmonisation_version="",  # GWAS Catalog harmoniser version not surfaced via tabix
        ld_panel=panel_id,
        ld_panel_super_pop=super_pop.value,
        ld_panel_version=panel_version,
        plink2_version=plink2_version,
        window_bp=window_bp,
        lead_variant_id=lead_variant_id,
        n_pairs=len(pairs),
        n_palindromic_excluded=n_palindromic,
        scatter_downsampled=len(pairs) > 5000,
        scatter_downsample_target=5000,
        ancestry_caveats=extra_caveats + spec.notes,
        plot_artifact=str(out_path.name),
        fetched_at=fetched_at,
    )

    return Tier2Result(
        plot_path=out_path,
        manifest_block=block,
        n_pairs=len(pairs),
        n_palindromic_excluded=n_palindromic,
        notes=notes,
    )


def _build_short_labels_from_spec(
    spec: LocusCompareSpec,
    exposure_release,
) -> tuple[str, str]:
    """Construct front-and-center one-line panel titles for the manhattans.

    Generic (spec-based). Mirrors the legacy `_build_short_labels` but reads
    label inputs from a `LocusCompareSpec` instead of a `StudyIdMapping`.
    """
    # Outcome side.
    if spec.outcome_trait_label:
        outcome_short = (
            f"Outcome (GWAS): {spec.outcome_trait_label} "
            f"({spec.gwas_accession})"
        )
    else:
        outcome_short = f"Outcome (GWAS): {spec.gwas_accession}"

    # Exposure side.
    bits: list[str] = []
    if spec.exposure_gene_symbol:
        bits.append(spec.exposure_gene_symbol)
    if exposure_release.study_label:
        bits.append(exposure_release.study_label)
    if exposure_release.sample_group:
        bits.append(exposure_release.sample_group)
    elif exposure_release.tissue_label and exposure_release.condition_label:
        bits.append(f"{exposure_release.tissue_label} ({exposure_release.condition_label})")
    if exposure_release.quant_method:
        bits.append(exposure_release.quant_method)
    descriptor = " | ".join(bits) if bits else spec.eqtl_dataset_id
    exposure_short = (
        f"Exposure (eQTL): {descriptor} "
        f"({spec.eqtl_dataset_id})"
    )
    return exposure_short, outcome_short


def load_study_id_mappings(yaml_path: Path) -> dict[tuple[str, str], StudyIdMapping]:
    """Read the manual lookup table (YAML).

    Schema:
      mappings:
        - ot_left_study_id: quach_2016_ge_monocyte_iav_ensg00000115808
          eqtl_catalogue_dataset_id: QTD000110
          ot_right_study_id: FINNGEN_R12_I9_HEARTFAIL
          gwas_catalog_accession: GCST90475990
          ancestry_left: EUR
          ancestry_right: EUR (Finnish)
          notes:
            - "Quach 2016 IAV-stimulated monocyte; verified live 2026-05-04"

    Returns a dict keyed by (ot_left_study_id, ot_right_study_id) for fast
    per-row lookup.
    """
    yaml_path = Path(yaml_path)
    if not yaml_path.is_file():
        return {}
    data = yaml.safe_load(yaml_path.read_text()) or {}
    out: dict[tuple[str, str], StudyIdMapping] = {}
    for entry in data.get("mappings", []):
        m = StudyIdMapping(
            ot_left_study_id=entry["ot_left_study_id"],
            eqtl_catalogue_dataset_id=entry["eqtl_catalogue_dataset_id"],
            ot_right_study_id=entry["ot_right_study_id"],
            gwas_catalog_accession=entry["gwas_catalog_accession"],
            ancestry_left=entry.get("ancestry_left", "EUR"),
            ancestry_right=entry.get("ancestry_right", "EUR"),
            outcome_trait_label=entry.get("outcome_trait_label", ""),
            exposure_gene_symbol=entry.get("exposure_gene_symbol", ""),
            notes=list(entry.get("notes", [])),
        )
        out[(m.ot_left_study_id, m.ot_right_study_id)] = m
    return out


def _eqtl_to_locus_variant(v) -> LocusVariant:
    """Map skills.execution.eqtl_catalogue RegionVariant to wald_ratio
    LocusVariant. The renderer's per-side manhattan reads the LocusVariant
    fields it shares with the wald_ratio harmoniser.
    """
    return LocusVariant(
        variant_id=v.variant_id,
        chromosome=v.chromosome,
        position=v.position,
        ref=v.ref,
        alt=v.alt,
        pip=None,
        beta=v.beta,
        se=v.se,
        p_value=v.p_value,
        is95=None,
        is99=None,
        r2_lead=None,
    )


def _gwas_to_locus_variant(v) -> LocusVariant:
    """Same as `_eqtl_to_locus_variant` for GWAS Catalog rows."""
    return LocusVariant(
        variant_id=v.variant_id,
        chromosome=v.chromosome,
        position=v.position,
        ref=v.ref,
        alt=v.alt,
        pip=None,
        beta=v.beta,
        se=v.se,
        p_value=v.p_value,
        is95=None,
        is99=None,
        r2_lead=None,
    )


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _extract_ensg_from_ot_study_id(ot_study_id: str) -> str | None:
    """OT QTL studyIds end with `_ensg<digits>` (lowercase). Return the
    uppercased ENSG id (the format eQTL Catalogue's REST API expects) or
    None if the studyId does not match.
    """
    import re
    m = re.search(r"(ensg\d+)$", ot_study_id, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


__all__ = [
    "LocusCompareSpec",
    "StudyIdMapping",
    "Tier2NotAvailable",
    "Tier2Result",
    "load_study_id_mappings",
    "render_locuscompare_for_lead",
    "render_tier2_for_lead",
]
