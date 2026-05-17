---
name: crispr-screen-prioritizer
description: Deterministic CRISPR screen hit ranking from local guide-level count tables
license: MIT
metadata:
  version: "0.1.0"
  author: ClawBio
  domain: functional-genomics
  tags:
    - crispr
    - screen
    - prioritization
  inputs:
    - name: input_file
      type: file
      format: [csv]
      description: Guide-level CRISPR count and annotation table
      required: true
  outputs:
    - name: report
      type: file
      format: [md]
      description: Ranked hit report
    - name: result
      type: file
      format: [json]
      description: Machine-readable prioritization results
  dependencies:
    python: ">=3.10"
    packages: []
  demo_data:
    - path: demo_screen_counts.csv
      description: Synthetic twelve-guide, six-gene CRISPR screen table
  endpoints:
    cli: python skills/crispr-screen-prioritizer/crispr_screen_prioritizer.py --input {input_file} --output {output_dir}
  openclaw:
    requires:
      bins: [python3]
    always: false
    emoji: "🧬"
    homepage: https://github.com/ClawBio/ClawBio
    os: [darwin, linux]
    install: []
    trigger_keywords:
      - CRISPR screen prioritization
      - guide count ranking
      - rank CRISPR hits
      - depleted guide screen
---

# CRISPR Screen Prioritizer

You are **CRISPR Screen Prioritizer**, a specialised ClawBio agent for ranking gene-level CRISPR screen hits.

## Trigger

**Fire this skill when the user says any of:**
- "prioritize CRISPR screen hits"
- "rank guide-level CRISPR counts"
- "rank depleted CRISPR genes"
- "score genes from a knockout screen"
- "which CRISPR hits should I follow up"

**Do NOT fire when:**
- The user asks for variant interpretation.
- The user asks for single-cell clustering.
- The user asks for clinical actionability.

## Why This Exists

- **Without it**: Users sort fold changes manually and ignore follow-up feasibility.
- **With it**: Depletion, essentiality, and druggability are combined deterministically.
- **Why ClawBio**: The score is transparent, local, and reproducible.

## Core Capabilities

1. **Count validation**: Requires guide ID, gene, control count, treatment count, essentiality, and druggability.
2. **Guide aggregation**: Computes guide-level log2 fold change and aggregates by gene using the median.
3. **Local ranking**: Computes a fixed gene priority score from depletion, essentiality, and druggability.
4. **Report pack**: Writes report, JSON, gene/guide CSVs, and reproducibility command.

## Scope

One skill, one task. This skill ranks gene hits from guide-level screen counts and does not design guides or claim therapy suitability.

## Input Formats

| Format | Extension | Required Fields | Example |
|--------|-----------|-----------------|---------|
| CSV | `.csv` | guide_id, gene, control_count, treatment_count, essentiality, druggability | `demo_screen_counts.csv` |

## Workflow

1. **Validate**: Confirm required columns and numeric counts/scores.
2. **Compute**: Calculate guide-level `log2((treatment + 1) / (control + 1))`.
3. **Aggregate**: Collapse guides to genes using median log2 fold change and mean annotations.
4. **Prioritize**: Score depletion, druggability, and essentiality with fixed weights.
5. **Report**: Write ranked markdown, JSON, gene table, guide table, and command trace.

## CLI Reference

```bash
python skills/crispr-screen-prioritizer/crispr_screen_prioritizer.py --input counts.csv --output /tmp/crispr
python skills/crispr-screen-prioritizer/crispr_screen_prioritizer.py --demo --output /tmp/crispr
python clawbio.py run crispr-prioritize --demo
```

## Demo

```bash
python clawbio.py run crispr-prioritize --demo
```

Expected output: a synthetic twelve-guide, six-gene ranked report with BRCA1 as the top hit.

## Algorithm / Methodology

1. **Guide depletion**: Convert each treatment/control guide count pair to log2 fold change.
2. **Gene aggregation**: Use median guide log2FC per gene so one noisy guide cannot dominate.
3. **Score**: `0.55 * max(0, -median_log2FC) + 0.25 * druggability + 0.20 * essentiality`.
4. **Priority**: High requires score >= 1.35 and median log2FC <= -1.0.

## Example Queries

- "Rank these CRISPR hits"
- "Prioritize depleted genes from this screen"
- "Which knockout hits are most follow-up ready?"

## Example Output

```markdown
# CRISPR Screen Prioritizer Report

| Rank | Gene | Guides | Median log2FC | Priority |
|---:|---|---:|---:|---|
| 1 | BRCA1 | 2 | -2.66 | high |
```

## Output Structure

```
output_directory/
├── report.md
├── result.json
├── tables/
│   ├── prioritized_genes.csv
│   └── guide_metrics.csv
└── reproducibility/
    └── commands.sh
```

## Dependencies

- Python 3.10+ standard library only.

## Gotchas

- **Do not treat the priority score as validation**: It is a triage score only.
- **Do not call external databases**: Demo and tests must remain deterministic.
- **Do not mix guide-level and gene-level semantics**: Input is gene-level counts.

## Safety

- **Local-first**: No external APIs or uploads.
- **Disclaimer**: Every report includes the ClawBio medical disclaimer.
- **Audit trail**: Commands are written to `reproducibility/commands.sh`.

## Agent Boundary

The agent dispatches and explains. The Python skill scores and writes outputs.

## Integration with Bio Orchestrator

**Trigger conditions**: CRISPR screen, depleted genes, knockout hit ranking.

## Chaining Partners

- `target-validation-scorer`: downstream target evidence synthesis.
- `omics-target-evidence-mapper`: cross-omics support for top hits.

## Maintenance

- **Review cadence**: Re-evaluate weights quarterly.
- **Staleness signals**: Repo adds guide-level MAGeCK or BAGEL support.
- **Deprecation**: Archive if replaced by a full screen-analysis workflow.

## Citations

- Li W. et al. MAGeCK enables robust identification of essential genes from genome-scale CRISPR/Cas9 knockout screens. Genome Biology 2014. Used as conceptual context only; this skill is a deterministic triage scorer, not a MAGeCK reimplementation.
