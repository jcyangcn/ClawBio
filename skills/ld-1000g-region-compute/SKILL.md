---
name: ld-1000g-region-compute
description: |
  Compute pairwise r² between a lead variant and every variant in a window
  using the 1000 Genomes Phase 3 GRCh38 reference panel, ancestry-stratified.
  Use when an agent needs LD coloring for a regional plot or LD pruning
  around a candidate causal variant. Two modes: pre-baked PLINK2 panel (fast,
  power-user) or on-demand region fetch from EBI 1000G FTP (no multi-GB
  cold-start; default for new installs).
license: MIT
metadata:
  skill-author: Aviv Madar
  version: 0.1.0
  domain: bioinformatics
  tags:
    - ld
    - 1000-genomes
    - reference-panel
    - plink2
    - ancestry-stratified
    - on-demand
  inputs:
    - name: lead
      type: string
      description: Lead variant id in chr_pos_ref_alt format (e.g. 1_109274968_G_T).
      required: true
    - name: partners
      type: list
      description: Iterable of partner variant ids in the same format.
      required: true
    - name: chromosome
      type: string
      description: Chromosome (with or without `chr` prefix).
      required: true
    - name: window_bp
      type: integer
      description: Total window width around the lead.
      required: true
    - name: super_pop
      type: string
      description: 1000G Phase 3 super-population code (EUR / AFR / AMR / EAS / SAS).
      required: false
  outputs:
    - name: pairs
      type: list
      description: Per-partner LDPair (partner_variant_id, r2, optional dprime).
    - name: panel_meta
      type: object
      description: Panel id, version, plink2 version, n_partners_returned, fetched_at_utc.
  dependencies:
    - python>=3.10
    - pysam>=0.22
    - pandas>=2.0
    - requests>=2.28
  demo_data:
    - examples/input.json
  endpoints:
    - https://ftp.1000genomes.ebi.ac.uk/    # phased VCFs + panel TSV (on-demand mode)
  openclaw:
    requires:
      bins:
        - python3
        - plink2
        - tabix
      env: []
      config: []
    always: false
    emoji: "🧬"
    homepage: https://github.com/ClawBio/ClawBio
    os: [darwin, linux]
    install: |
      pip install pysam pandas requests
      # plus a system plink2 binary (brew install plink2 on macOS, apt-get install plink2 on Linux)
    trigger_keywords:
      - ld around lead
      - ld region 1000g
      - r-squared 1000g phase 3
      - ancestry-stratified ld
      - locuszoom ld coloring
      - 1000 genomes ld panel
---

# 🧬 LD 1000G Region Compute

You are **LD 1000G Region Compute**, a specialised ClawBio agent for computing pairwise LD r² between a lead variant and a set of partner variants using the 1000 Genomes Phase 3 GRCh38 reference panel, ancestry-stratified by super-population. Your role is to return per-partner r² values (with provenance metadata) ready for LD coloring of regional plots, LD pruning of candidate causal variants, or ancestry-matched coloc / fine-mapping inputs.

## Overview

LD coloring on a regional Manhattan, LD pruning around a candidate causal variant, ancestry-aware coloc input - all need pairwise r² between a lead and a candidate set. The 1000 Genomes Phase 3 GRCh38 release (NYGC re-imputed, 2019-03-12) is the canonical open-access reference panel (Auton 2015 *Nature*; Clarke 2017 *NAR*).

This skill provides r² compute via `plink2 --r2-unphased` with two access modes:

1. **Pre-baked PLINK2 panel** (`Plink2LDClient`): point at a local `<prefix>.{pgen,pvar,psam}` triplet that the user has already installed (typically a 2-3 GB super-pop subset per chromosome). Fastest; no network. Power-user mode.
2. **On-demand region fetch** (`OnDemand1000GLDClient`): tabix-fetch the region VCF from EBI 1000G FTP (~5-50 MB per request), super-pop-filter via the canonical Phase 3 panel TSV, run `plink2 --vcf` locally. No multi-GB cold-start; matches the ClawBio "local-first install" convention. Cache stored at `~/.clawbio/locuscompare_cache/1000g/`.

Both modes implement the same `r2_with_lead(lead, partners, chromosome, window_bp)` interface; consumers swap clients without code changes.

## Trigger

**Fire when** the user (or upstream agent step) wants:

- Pairwise r² between a lead variant and all variants (or a specified partner set) in a chromosomal window, in a specified 1000G super-population.
- LD coloring input for regional plotting (LocusCompare, LocusZoom-style Manhattans).
- LD-pruning input for Mendelian randomisation instrument selection.
- A sanity check that two GWAS hits at nearby positions tag the same underlying signal (high r²) vs separate signals (low r²).
- Ancestry-matched LD reference for coloc / fine-mapping inputs.

**Do NOT fire when** the user wants:

- **r² between two specific variants only** - a 2-variant lookup is overkill via this skill; query plink2 directly with `--ld <var1> <var2>` for that case.
- **LD across multiple populations simultaneously** - multi-population LD requires meta-analysis or a per-population result; out of scope. Call this skill once per super-population if needed.
- **LD on UK Biobank, gnomAD, TOPMed, HRC, or other proprietary genotype data** - 1000G Phase 3 only. Other panels require different licensing and ingest paths.
- **Pre-computed full-genome LD matrices** - this is on-demand region compute. Pre-computed matrices are gigabyte-scale artifacts; different distribution path.
- **Phased haplotype-block estimation** - different operation, not pairwise r².
- **Trans-population LD comparisons** - use a dedicated tool (LDLink, LDpair).

## Scope

**One skill, one task.** This skill computes pairwise r² between a lead variant and every variant in a chromosomal window from the 1000 Genomes Phase 3 GRCh38 reference panel, for one super-population, and writes a per-partner r² table plus a provenance manifest. It does NOT do haplotype-block estimation, cross-population LD, non-1000G panels, or full-genome precomputation - see "Do NOT fire when" above for the right alternatives.

## Workflow

When an agent asks for r² between a lead and partners in a region:

1. **Resolve `lead` + `partners` + `chromosome` + `window_bp` + `super_pop`**: lead in `chr_pos_ref_alt` GRCh38 form; partners as a list (or `null` to compute against all variants in the window); super-population from `{EUR, AFR, AMR, EAS, SAS}` (default EUR; choose to match the upstream cohort's ancestry - see Gotcha #1).
2. **Region VCF fetch** (on-demand mode): the skill performs a tabix-on-FTP byte-range request against `https://ftp.1000genomes.ebi.ac.uk/` for the requested chromosome × window. Cache hit at `~/.clawbio/locuscompare_cache/1000g/<chr>_<start>_<end>.vcf.gz` skips the fetch.
3. **Super-pop filter**: subset the VCF to the chosen super-population's samples via the canonical Phase 3 panel TSV (`integrated_call_samples_v3.20130502.ALL.panel`); `plink2 --keep` with FID=0 convention (Gotcha #4).
4. **r² compute**: `plink2 --r2-unphased lower-tri` against the lead variant. Variant ids are rewritten to `chr_pos_ref_alt` form via `plink2 --set-all-var-ids '@:#:$r:$a'` (Gotcha #3).
5. **Write outputs** to `--output <dir>/`: a flat `pairs.tsv` (partner_variant_id, r2, optional dprime), a `manifest.yaml` with provenance (panel id, panel version, super_pop, plink2 version, n_partners_requested, n_partners_returned, fetched_at_utc, cache hit/miss), and a `report.md` human-readable summary.

## CLI Reference

```bash
# Standard usage with a config file
python skills/ld-1000g-region-compute/ld_1000g_region_compute.py \
    --input <config.json> --output <output_dir>

# Bundled demo (SORT1 locus, EUR super-pop, 5 partner variants)
python skills/ld-1000g-region-compute/ld_1000g_region_compute.py \
    --demo --output /tmp/sort1_ld_demo

# Via ClawBio runner
python clawbio.py run ld-region --input <config.json>
python clawbio.py run ld-region --demo
```

Config schema (JSON or YAML):

```json
{
  "lead": "1_109274968_G_T",
  "partners": [
    "1_109270398_G_A",
    "1_109272630_A_G",
    "1_109274570_A_G",
    "1_109274623_C_T",
    "1_109274857_G_C"
  ],
  "chromosome": "1",
  "window_bp": 1000000,
  "super_pop": "EUR"
}
```

Setting `partners: null` (or omitting the key in some implementations) computes r² against every variant in the window; the response can be large for wide windows.

## Example Output

Running `--demo` (SORT1 locus, EUR, 5 partner variants):

```
info: using bundled demo sort1_locus_eur.json
ld-1000g-region-compute: 5 partners -> /tmp/sort1_ld_demo/pairs.tsv
  panel: 1000g_phase3_v5b_grch38_basic (EUR)
  plink2: PLINK v2.0.0-a.7.1 M1 (4 May 2026)
  cache: hit (~/.clawbio/locuscompare_cache/1000g/chr1_108774968_109774968.vcf.gz)
```

`<output_dir>/manifest.yaml`:

```yaml
skill: ld-1000g-region-compute
version: 0.1.0
lead: 1_109274968_G_T
chromosome: '1'
window_bp: 1000000
super_pop: EUR
panel:
  panel_id: 1000g_phase3_v5b_grch38_basic
  panel_version: 5b_remote_2019_03_12
  super_pop: EUR
  super_pop_label: European (EUR; n=503; 1000G Phase 3)
  plink2_version: PLINK v2.0.0-a.7.1 M1 (4 May 2026)
n_partners_requested: 5
n_partners_returned: 5
cache_hit: true
fetched_at_utc: '2026-05-09T11:44:21Z'
outputs:
  pairs_tsv: pairs.tsv
notes: []
```

`<output_dir>/pairs.tsv`:

```
partner_variant_id     r2
1_109270398_G_A        0.892
1_109272630_A_G        0.765
1_109274570_A_G        0.991
1_109274623_C_T        0.998
1_109274857_G_C        0.412
```

`<output_dir>/report.md`:

```markdown
# ld-1000g-region-compute report

- **Lead:** `1_109274968_G_T` (rs646776)
- **Panel:** 1000G Phase 3 GRCh38 v5b (EUR; n=503 samples)
- **plink2:** PLINK v2.0.0-a.7.1 M1
- **Window:** chr1, ±500 kb
- **Partners returned:** 5 of 5 requested
- **Output TSV:** pairs.tsv
```

## Gotchas

1. **r² requires ancestry-matched reference panel.** Using EUR LD against an East-Asian GWAS produces wrong LD blocks and misleading visualisations. The skill takes `super_pop` as a required input (or defaults to EUR with a manifest caveat) and emits the choice in every manifest. Match `super_pop` to the upstream cohort's ancestry. For Finnish-EUR (FinnGen) on a 1000G EUR panel, expect ~0.05 r² average divergence on common variants per Locke 2019; surface as a caveat in the rendered output. See `references/ancestry_matching.md`.

2. **Lead variant absent from 1000G.** Rare or array-only variants may not be in the 1000G panel; in that case every partner returns r²=0 because the lead has no neighbours in the reference. The skill notes `LD r² unavailable for lead` in the manifest. Workaround: pick a different (more common) lead in the locus that IS in 1000G via `--lead <variant_id>`, or accept grey points in the regional plot.

3. **Variant-id format collision in 1000G VCFs.** The 1000G GRCh38 VCFs use rsids in the ID column, NOT `chr:pos:ref:alt`. The on-demand client passes `plink2 --set-all-var-ids '@:#:$r:$a'` to rewrite IDs into the canonical form before LD compute. Tri-allelic loci that have been split into multiple lines may produce duplicate IDs; the client passes `--new-id-max-allele-len 100 missing` to mitigate, but very unusual loci may still complain (deduplicate the source VCF or `bcftools norm -m -any`).

4. **plink2 `--keep` FID convention.** plink2 sets FID=0 for all samples loaded from a VCF. The on-demand client's `--keep` file uses `0\t<sample>` rows (NOT `<sample>\t<sample>` - that variant is silently ignored, and the super-pop filter will return zero samples).

5. **Rare variants (MAF < 0.01) have unstable r².** With ~500 EUR samples and MAF=0.005, only ~5 individuals carry the rare allele; r² estimates have huge sampling variance. The skill filters MAF < 0.01 by default and emits the count in `rare_variant_drops`. Do NOT manually re-include rare variants by lowering this threshold; for rare-variant fine-mapping, use a higher-density reference (TOPMed, HRC) which is out of scope.

6. **1000G Phase 3 is stable; cache is durable.** The 2019-03-12 NYGC re-imputed release has not been refreshed; r² values computed today vs five years from now are identical. Cache invalidation is panel-version-keyed; the skill does NOT re-fetch when the cache is warm.

7. **Admixed populations do not fit cleanly into a single 1000G super-population.** Hispanic / Latino, African American, and other admixed cohorts have ancestry-specific LD that 1000G's five super-pops only partially capture. The skill emits a caveat in the manifest when `super_pop = AMR` and the upstream study is admixed; surface it in the user-facing reply. See `references/ancestry_matching.md`.

## Safety

**Not for clinical decisions.** This skill returns LD r² estimates from a public reference panel. The output is a research-grade visualisation aid; do not use the output for clinical decision-making.

**LD computed on a reference panel does not match LD in the target study population exactly.** The 1000G Phase 3 super-populations are approximations. For trans-ancestry studies, populations not represented in 1000G, or admixed cohorts, the r² values are useful for visualisation only, not for hard inferential decisions (e.g., LD-pruning instruments for Mendelian randomisation should use the actual GWAS reference panel when available).

## Agent Boundary

The skill computes pairwise r² between a lead variant and partner variants in a chromosomal window, using a 1000 Genomes Phase 3 GRCh38 super-population reference panel. The agent should:

- **Use r² output for visualisation** (LocusCompare, LocusZoom-style regional plots) and for instrument-set LD-pruning in Mendelian randomisation.
- **Surface the chosen super-population in the user-facing reply.** Per the user-friendly enum-expansion rule (`CLAUDE.md`), expand the field: `LD = 1000G Phase 3 EUR (n=503 samples)`, never just `EUR`.
- **NOT claim "in LD" without an explicit r² threshold.** The standard publication thresholds are r² > 0.6 (high LD), r² > 0.2 (any LD); the agent must cite the threshold when making a claim.
- **NOT use 1000G-derived LD for ancestry-mismatched studies without flagging the mismatch.** When the GWAS / eQTL ancestry does not match the chosen super-pop, the agent must surface this as a caveat in the user-facing reply.
- **NOT compute LD on rare variants (MAF < 0.01).** The skill drops them; the agent must NOT manually re-include them by lowering the threshold.
- **Cite the panel version and plink2 version** in any output. The manifest carries both; the agent quotes them as part of the methods statement.

## Citations

- 1000 Genomes Project Consortium (2015). *A global reference for human genetic variation.* Nature 526, 68-74. doi:10.1038/nature15393
- Clarke et al. (2017). *The international Genome Sample Resource (IGSR): A worldwide collection of genome variation incorporating the 1000 Genomes Project data.* Nucleic Acids Res 45, D854-D859. doi:10.1093/nar/gkw829
- Chang et al. (2015). *Second-generation PLINK: rising to the challenge of larger and richer datasets.* GigaScience 4. doi:10.1186/s13742-015-0047-8
- Locke et al. (2019). *Exome sequencing of Finnish isolates enhances rare-variant association power.* Nature 572, 323-328. doi:10.1038/s41586-019-1457-z (Finnish-EUR vs 1000G EUR LD divergence at common variants.)
