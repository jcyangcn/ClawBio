# Examples

Five examples covering the main entry vectors for `locuscompare`, plus four
harmonization recipes for non-bundled data sources.

## When to use which

| Example | Entry vector | Network required | Use when... |
|---|---|---|---|
| `01_synthetic_demo/` | `--demo` flag | No | Smoke-testing the install; CI; offline use |
| `02_eqtl_catalogue_x_gwas_catalog/` | bundled fetchers | Yes | You have an eQTL Cat dataset_id + GWAS Cat accession |
| `03_open_targets_followup/` | OT coloc-row resolution | Yes | You found an interesting row in an Open Targets coloc query |
| `04_finemapping_chain/` | output of `fine-mapping` | No (after fine-mapping) | You want visual confirmation of a fine-mapping coloc claim |
| `05_gwas_lookup_followup/` | output of `gwas-lookup` | Yes | You found an interesting variant via `gwas-lookup` |

## Recipes for non-bundled sources

`recipes/` ships harmonization scripts that convert third-party sumstats into
the canonical TSV schema documented in `../INPUT_SCHEMA.md`. After harmonizing,
point a `sumstats_path` at the produced TSV in your config — locuscompare
treats it identically to a bundled-fetcher result.

| Recipe | Source | License tier | Auth |
|---|---|---|---|
| `finngen_direct/` | FinnGen direct (latest release) | Open-access | None |
| `pan_ukbb_direct/` | Pan-UKBB FTP | Open-access | None |
| `ukb_ppp_pqtl/` | UKB-PPP plasma pQTL | Yellow (registration-gated) | UKB approval |
| `gtex_v10_direct/` | GTEx v10 cis-eQTL bgz | Open-access | None |

Each recipe is a small bash + awk + bgzip + tabix pipeline (~20 lines) plus a
README documenting the source URL, the column-rename map, and any caveats.
