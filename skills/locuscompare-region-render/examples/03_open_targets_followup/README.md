# Example 03 — Open Targets follow-up

You found an interesting row in Open Targets' `target.colocalisation` query
and you want to inspect the regional pattern visually before committing to the
target. This example walks through translating an OT coloc row into a
locuscompare config.

## The OT row

Verbatim row from `ot_row.json` in this directory (Open Targets release 26.03):

```
left_studyId:   gtex_ge_minor_salivary_gland_ensg00000134243   # SORT1 cis-eQTL, GTEx minor salivary gland
right_studyId:  GCST90269602                                    # cholesterol in medium VLDL GWAS
h4:             0.998
lead_variant:   1_109274968_G_T
```

This is the canonical Musunuru 2010 1p13.3 locus.

## Resolution recipe

OT studyIds are not directly consumable by tabix-on-FTP fetchers. You need to
translate them into source-native ids:

### Left side (eQTL): `gtex_ge_minor_salivary_gland_ensg00000134243` → `(QTD000276, ENSG00000134243)`

The OT eQTL studyId convention is `<project>_<quant>_<tissue>_<ensg>` where
`<ensg>` is the lowercase ENSG without the version suffix. Two pieces:

1. The **molecular_trait_id** is the ENSG (uppercase): `ENSG00000134243`. Just
   regex it out: `re.search(r"(ensg\d+)$", studyId, re.I).group(1).upper()`.
2. The **dataset_id** is what the eQTL Catalogue uses for "GTEx v8 minor salivary
   gland gene-expression QTL". Look it up via the eQTL Cat REST API:

   ```bash
   curl -s 'https://www.ebi.ac.uk/eqtl/api/v3/datasets?study_label=GTEx&tissue_label=minor%20salivary%20gland&quant_method=ge' \
     | jq -r '.datasets[] | "\(.dataset_id)\t\(.study_label)\t\(.tissue_label)\t\(.quant_method)"'
   # QTD000276    GTEx    minor salivary gland    ge
   ```

   Or use the eQTL Cat dataset metadata table at
   <https://www.ebi.ac.uk/eqtl/Studies/>.

### Right side (GWAS): `GCST90269602` → `GCST90269602`

GWAS Catalog accessions are already in OT's right_studyId field for GWAS Cat–
sourced studies. Drop in directly. (For non-GWAS-Cat right_studyIds — e.g.
`FINNGEN_R12_*` — see the FinnGen recipe in `examples/recipes/finngen_direct/`.)

## Resulting config

See `config.yaml` in this directory. Once you have `(dataset_id,
molecular_trait_id, gwas_accession, lead_variant_id, chromosome, position_bp,
window_bp)`, the rest of the config is identical to example 02.

## Run

```bash
python skills/locuscompare-region-render/cli.py \
    --input examples/03_open_targets_followup/config.yaml \
    --output runs/sort1_ot_followup/
```

Same network footprint as example 02; the OT-followup just adds the
provenance fields to the manifest.

## Why the OT info goes in `provenance:`, not in core config

The OT-ness disappears once resolved — the actual analysis is identical to
any other eQTL Cat × GWAS Cat run. The `provenance:` block keeps the
OT-discovery context recorded in the output manifest for reproducibility,
but doesn't affect the rendering. This is the architectural decoupling
documented in the project's `docs/locuscompare_decoupling_plan.md`.

## Bulk resolution for many OT rows

If you're processing many OT coloc rows (a target-validation workflow that
batches dozens of genes), the per-row resolution above scales poorly. Instead,
maintain an orchestrator-side mappings YAML keyed by `(ot_left_studyId,
ot_right_studyId) → (dataset_id, molecular_trait_id, accession)` and emit
one locuscompare config per row.
