# 🦖 ClawBio Architecture

## Overview

ClawBio is a collection of modular AI agent skills for bioinformatics, designed around three principles: local-first execution, reproducible analysis, and composable workflows.

## System Design

ClawBio is easiest to read as a set of layers rather than one monolithic
agent. Each layer has a narrow responsibility:

| Layer | Main Files / Components | Responsibility |
|-------|-------------------------|----------------|
| Interfaces | CLI, Python API, RoboTerri, Discord, OpenClaw gateway, Claude Code | Accept a user request, collect files, and either name a skill directly or ask for routing. |
| Skill self-description | `skills/<name>/SKILL.md`, optional `skills/<name>/INTENTS.json`, `skills/catalog.json` | Describe what the skill does, what inputs it accepts, how it is invoked, and when it should be considered. |
| Routing and planning | `skills/bio-orchestrator/`, `clawbio/skill_intents.py`, `docs/skill-intents.md` | Detect file types, headers, keywords, and structured intent descriptors; select a suitable skill or plan a small chain. |
| Runner and safety gate | `clawbio.py` / `clawbio.run_skill()` | Validate the selected skill, enforce allowed flags, prepare output directories, and launch the skill script. |
| Specialist skill execution | `skills/<name>/<script>.py` plus skill-local helpers | Run the domain method and produce skill-owned outputs. |
| Output contract | `report.md`, `result.json`, `tables/`, `figures/`, `reproducibility/` | Return human-readable reports, machine-readable results, preferred artifacts, suggested follow-ups, and replay metadata where supported. |

## Routing Logic

The Bio Orchestrator routes requests based on:

1. **File extension**: `.vcf` -> equity-scorer/vcf-annotator, `.fastq` -> seq-wrangler, etc.
2. **Keyword matching**: "diversity" -> equity-scorer, "structure" -> struct-predictor, etc.
3. **User intent**: Explicit skill names override automatic routing.
4. **Chaining**: Multi-step requests trigger sequential skill invocation with output piping.

## Skill Independence

Every skill works standalone. The Bio Orchestrator adds:
- Automatic routing (user does not need to know skill names)
- Multi-skill chaining (pipe output of one skill to the next)
- Unified reporting (combine results from multiple skills)
- Access to skill-defined reproducibility outputs where a routed skill implements them

A user can invoke any skill directly without the orchestrator.

## Skill Self-Description

Skills describe themselves before they execute. `SKILL.md` is the primary
human- and agent-readable contract: it records the domain method, expected
inputs, outputs, safety boundaries, demo commands, and gotchas. Python scripts
are implementations of that contract, not replacements for it.

Some skills also publish optional intent descriptors in `INTENTS.json` or
`skill_intents.json`; see [docs/skill-intents.md](skill-intents.md) for the
descriptor schema. These files are data-only routing metadata for chat adapters
and planners: aliases, trigger terms, slot extraction rules, safe execution
plans, and confirmation gates. They do not grant new shell powers or new CLI
flags; `clawbio.py` still enforces the registered allow-list.

Together, `SKILL.md`, optional intent descriptors, and `skills/catalog.json`
let agents discover what a skill can do without scraping prose from previous
chat sessions.

## Data Flow

```
Input File(s)
    │
    ▼
Validation (file type, format, size checks)
    │
    ▼
Processing (skill-specific computation)
    │
    ▼
Results (tables, metrics, intermediate files)
    │
    ▼
Visualisation (matplotlib/seaborn figures)
    │
    ▼
Structured Output (result.json + preferred artifacts)
    │
    ▼
Report Assembly (markdown + embedded figures)
    │
    ▼
Optional Reproducibility Export (helper-backed commands, environment, checksums)
```

The same run may also expose UI-facing fields from `result.json`, including
`chat_summary_lines`, `preferred_artifacts`, `workflow_state`, and
`suggested_actions`. These fields let chat or GUI frontends render compact
summaries and offer deterministic next steps without inventing follow-up
commands.

## Structured Next Steps

Where supported, a skill can describe valid follow-up actions in its
`result.json` output:

- `workflow_state`: the lifecycle, label, and stable state identity for the
  completed run.
- `preferred_artifacts`: files the UI should surface first.
- `suggested_actions`: deterministic next-step requests, such as "show top
  results" or "summarise by category".
- `chat_summary_lines`: short skill-authored text suitable for chat adapters.

This keeps next-step menus tied to the skill's own state rather than to an
agent's guess. Not every skill emits these fields yet; skills without them still
return the standard report and output bundle.

## Privacy Model

ClawBio enforces a strict local-first privacy model:

- **No network calls** for data processing. All computation happens locally.
- **Optional network** only for: literature search (PubMed API), structure database queries (PDB/UniProt), and package installation.
- **Explicit consent** required before any data leaves the machine.
- **File access scoping**: Skills operate within the current working directory by default. Access to parent directories requires user confirmation.

## Reproducibility Contract

ClawBio's validated reproducibility contract is not universal across every skill. For skills that use the shared reproducibility helpers, the output directory typically includes:

1. **`reproducibility/commands.sh`**: A replay command for the skill run without needing the original agent session.
2. **`reproducibility/environment.yml`**: A suggested Conda environment snapshot for the run.
3. **`reproducibility/checksums.sha256`**: SHA-256 hashes for selected output files.
4. **Optional extras**: Some skills may emit additional provenance files such as `runtime-lock.json` or other lock metadata.

Important boundaries:

- Reproducibility behavior can vary by skill.
- Replays may still require external tools or the original input files to be present locally.
- `analysis_log.md` is not a guaranteed output for every skill.
- Portable replay scripts reduce path friction, but they are not a blanket promise that every run will reproduce unchanged on every machine.

See `docs/reproducibility.md` for the user workflow and a concrete `multiqc-reporter` example.

## Skill Packaging

Each skill is a directory containing:

```
skill-name/
├── SKILL.md          # Required: YAML frontmatter + markdown instructions
├── skill_name.py     # Optional: Python implementation
├── utils.py          # Optional: shared utilities
├── tests/            # Optional: test cases
│   └── test_skill.py
└── examples/         # Optional: example inputs/outputs
    ├── input.vcf
    └── expected_output.md
```

The SKILL.md is the primary artifact. The Python files are supporting code that the agent invokes via shell commands. This separation means:
- Skills can be reviewed as markdown (human-readable)
- Python code can be tested independently
- Skills work with any compatible agent platform, not just OpenClaw

## Integration with OpenBio

The existing [OpenBio skill](https://github.com/openclaw/skills) provides API access to:
- PDB (protein structures)
- UniProt (protein sequences and annotations)
- ChEMBL (bioactivity data)
- Pathway databases

ClawBio skills can call OpenBio for database lookups while keeping all computation local. For example, Struct Predictor might use OpenBio to fetch a reference structure from PDB, then run local AlphaFold for comparison.

## Extensibility

New skills follow the template at `templates/SKILL-TEMPLATE.md`. The Bio Orchestrator routing table is designed to be extended: add a new entry mapping file types or keywords to your skill, and the orchestrator routes to it automatically.

Community submissions go through ClawHub or direct PR to this repository. 🦖
