# OpenClaw Bio

**Bioinformatics-native AI agent skills for local-first, privacy-focused genomics research.**

OpenClaw Bio is a curated collection of modular AI agent skills built for bioinformaticians, genomics researchers, and computational biologists. Each skill wraps proven bioinformatics tools (Biopython, SAMtools, Seurat, AlphaFold) into composable, agent-orchestrated workflows that run entirely on your machine.

No cloud uploads. No data exfiltration. Your genomes stay local.

## Why OpenClaw Bio?

General-purpose AI agents are powerful but blind to the specific needs of biological research:

- **Privacy**: Genomic data is sensitive. Cloud-first agents risk exposing patient samples, proprietary variants, and unpublished findings.
- **Reproducibility**: Biology demands audit trails. Every analysis step must be logged, versioned, and exportable as a reproducible pipeline.
- **Domain expertise**: A generic agent does not know that a VCF file needs ancestry-aware annotation, or that single-cell data requires doublet removal before clustering.

OpenClaw Bio fills this gap with skills that understand biology from the ground up.

## Skills

| Skill | Status | Description |
|-------|--------|-------------|
| [Bio Orchestrator](skills/bio-orchestrator/) | MVP | Meta-agent that routes bioinformatics requests to specialised sub-agents |
| [Equity Scorer](skills/equity-scorer/) | MVP | HEIM diversity metrics from VCF/ancestry data; heterozygosity, FST, PCA, equity reports |
| [VCF Annotator](skills/vcf-annotator/) | Planned | Variant annotation with VEP, ancestry context, and diversity metrics |
| [Lit Synthesizer](skills/lit-synthesizer/) | Planned | PubMed/bioRxiv search with LLM summarisation and citation graphs |
| [scRNA Orchestrator](skills/scrna-orchestrator/) | Planned | Seurat/Scanpy automation: QC, clustering, DE analysis, visualisation |
| [Struct Predictor](skills/struct-predictor/) | Planned | AlphaFold/Boltz/Chai wrappers for local structure prediction |
| [Seq Wrangler](skills/seq-wrangler/) | Planned | FastQC, alignment, BAM processing, QC reporting |
| [Repro Enforcer](skills/repro-enforcer/) | Planned | Export any analysis as Conda env + Singularity container + Nextflow pipeline |

## Quick Start

### Prerequisites

- [OpenClaw](https://github.com/openclaw/openclaw) installed and configured
- Python 3.11+
- Bioinformatics tools for your skill of choice (see individual SKILL.md files)

### Install a skill

```bash
# Install the Bio Orchestrator (routes to sub-skills automatically)
openclaw install skills/bio-orchestrator

# Install the Equity Scorer for diversity analysis
openclaw install skills/equity-scorer
```

### Use a skill

```bash
# Ask the orchestrator to analyse a VCF file
openclaw "Analyse the diversity metrics in my VCF file at data/samples.vcf"

# Run equity scoring directly
openclaw "Score the population diversity in data/ancestry.csv using HEIM metrics"
```

## Architecture

```
User Request
    |
    v
Bio Orchestrator (routing + file I/O + reporting)
    |
    +---> Equity Scorer (diversity metrics, HEIM index)
    +---> VCF Annotator (variant annotation, VEP)
    +---> Lit Synthesizer (literature search, summarisation)
    +---> scRNA Orchestrator (single-cell pipelines)
    +---> Struct Predictor (protein structure)
    +---> Seq Wrangler (sequence QC, alignment)
    +---> Repro Enforcer (reproducibility export)
    |
    v
Markdown Report + Audit Log + Reproducibility Bundle
```

Each skill is a standalone SKILL.md + supporting scripts. The Bio Orchestrator routes requests to the right skill based on input type and user intent, but every skill also works independently.

See [docs/architecture.md](docs/architecture.md) for the full design.

## Contributing

We want skills from the bioinformatics community. If you work with genomics, proteomics, metabolomics, imaging, or clinical data, you can contribute a skill.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the submission process and [templates/SKILL-TEMPLATE.md](templates/SKILL-TEMPLATE.md) for the skill skeleton.

**Skill ideas we would love to see:**
- GWAS Pipeline (PLINK/REGENIE automation)
- Metagenomics Classifier (Kraken2/MetaPhlAn wrapper)
- Pathway Enricher (GO/KEGG enrichment analysis)
- Clinical Variant Reporter (ACMG classification)
- Phylogenetics Builder (IQ-TREE/RAxML automation)

## Principles

1. **Local-first**: All processing on your machine. No mandatory cloud uploads.
2. **Modular**: Each skill does one thing well. Compose them via the orchestrator.
3. **Reproducible**: Every analysis generates an audit trail and exportable pipeline.
4. **Auditable**: Human-review checkpoints before destructive or irreversible actions.
5. **Secure**: Minimal permissions. Containerisation recommended. No hardcoded credentials.

## Roadmap

- **Week 1** (Feb 27): Bio Orchestrator + Equity Scorer on ClawHub
- **Week 2-3** (Mar 6-19): VCF Annotator, Lit Synthesizer, scRNA Orchestrator
- **Week 4-5** (Mar 20 - Apr 2): Struct Predictor, Seq Wrangler
- **Week 6** (Apr 3-9): Repro Enforcer, bioRxiv preprint, community launch

## License

MIT

## Citation

If you use OpenClaw Bio in your research, please cite:

```bibtex
@software{openclaw_bio_2026,
  author = {Corpas, Manuel},
  title = {OpenClaw Bio: An Open-Source Library of AI Agent Skills for Reproducible Bioinformatics},
  year = {2026},
  url = {https://github.com/manuelcorpas/openclaw-bio}
}
```

## Links

- [OpenClaw](https://github.com/openclaw/openclaw) - The agent platform
- [ClawHub](https://clawhub.ai) - Skill registry
- [HEIM Index](https://heim-index.org) - Health Equity Index for Minorities
- [Corpus Core](https://github.com/manuelcorpas/corpus-core) - RAG memory system for research
