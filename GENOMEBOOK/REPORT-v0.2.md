# Genomebook v0.2: Mendelian Skill Inheritance for LLM Agent Populations

## Summary

Genomebook v0.2 introduces heritable executable skills to agent evolution. Each agent's genome (26 traits encoded across 60 diploid loci) now gates access to 7 real bioinformatics skills from ClawBio. Breeding via Mendelian inheritance produces offspring with different skill portfolios. Fitness-weighted mating selection creates evolutionary pressure for agents that use their skills effectively.

A controlled experiment (5 replicates, 50 generations, 2 conditions) demonstrates that Mendelian skill inheritance produces significantly higher fitness than random skill assignment, with a large effect size (Cohen's d = 2.23).

## What Changed from v0.1

| Component | v0.1 | v0.2 |
|-----------|------|------|
| Traits | Decorative (flavour prompts) | Functional (gate executable skills) |
| Skills | None | 7 ClawBio skills with trait thresholds |
| Fitness | Health score from disease burden | 4-component: utilization, quality, novelty, chaining |
| Mating | Compatibility only | Compatibility x fitness weighting |
| Founders | Mixed historical figures (20) | Biomedical scientists only (20) |
| Selection | Purifying (disease avoidance) | Directional (skill competence) |

## Architecture

### Skill Genome

Each of 7 skills requires minimum scores on specific heritable traits:

| Skill | Gate Traits | Coverage (gen-0) |
|-------|-------------|------------------|
| pubmed-search | analytical >= 0.60, verbal >= 0.55 | 18/20 |
| gwas-variant-lookup | pattern_rec >= 0.80, analytical >= 0.80 | 17/20 |
| prs-calculator | math >= 0.70, pattern >= 0.80, analytical >= 0.80 | 9/20 |
| equity-assessment | empathy >= 0.65, conscientiousness >= 0.80 | 8/20 |
| clinpgx-lookup | analytical >= 0.80, conscientiousness >= 0.80 | 12/20 |
| hypothesis-generation | creativity >= 0.75, openness >= 0.75, risk >= 0.55 | 5/20 |
| literature-synthesis | verbal >= 0.70, analytical >= 0.80, creativity >= 0.65 | 11/20 |

Trait scores also configure skill parameters (e.g., persistence controls search depth, openness controls search breadth).

### Fitness Function

| Component | Weight | Measures |
|-----------|--------|----------|
| F1: Skill utilization | 0.30 | Used available skills relevant to task |
| F2: Synthesis quality | 0.30 | Skill executions returned valid data |
| F3: Novelty | 0.20 | Unique skill combination vs other agents |
| F4: Skill chaining | 0.20 | Combined 2+ data skills |

### Biomedical Founders (gen-0)

**Males (10):** Darwin, Cajal, Ibn Sina, Pasteur, Mendel, Fleming, Salk, Crick, Brenner, Sanger

**Females (10):** Anning, Curie, Franklin, Hodgkin, Nightingale, McClintock, Tu Youyou, Doudna, Nettie Stevens, Barre-Sinoussi

Skill portfolios range from 1/7 (Fleming, Anning) to 7/7 (Ibn Sina).

## Experimental Design

| Condition | Skills | Inheritance | Purpose |
|-----------|--------|-------------|---------|
| Mendelian | Trait-gated | Diploid, crossover, mutation | Treatment |
| Random | Randomly assigned each gen | None | Control |

- 5 replicates per condition (seeds: 42, 137, 256, 1001, 2026)
- 50 generations per replicate
- Population cap: 30 agents, retirement age: 3 generations
- 2 offspring per mating pair
- Dry-run mode (skill gates evaluated, no external API calls)

## Results

### Mendelian vs Random: Fitness Trajectory (averaged across 5 replicates)

| Gen | Mendelian | Random | Delta |
|-----|-----------|--------|-------|
| 0 | 0.840 | 0.778 | +0.062 |
| 5 | 0.845 | 0.757 | +0.088 |
| 10 | 0.838 | 0.791 | +0.047 |
| 15 | 0.844 | 0.789 | +0.055 |
| 20 | 0.836 | 0.794 | +0.042 |
| 25 | 0.834 | 0.812 | +0.022 |
| 30 | 0.840 | 0.781 | +0.059 |
| 35 | 0.844 | 0.766 | +0.078 |
| 40 | 0.834 | 0.795 | +0.040 |
| 45 | 0.846 | 0.787 | +0.059 |
| 49 | 0.835 | 0.788 | +0.047 |

### Summary Statistics (250 generation-measurements per condition)

| Metric | Mendelian | Random |
|--------|-----------|--------|
| Mean fitness | **0.840** | 0.786 |
| Std dev | 0.016 | 0.031 |
| Win rate | **96.4%** (241/250) | 3.6% (9/250) |
| Cohen's d | **2.23** (large) | |
| Mean skills at gen 49 | **4.48** | 2.52 |

### Per-Seed Final Fitness (gen 49)

| Seed | Mendelian | Random | Winner |
|------|-----------|--------|--------|
| 42 | 0.852 | 0.797 | Mendelian |
| 137 | 0.820 | 0.778 | Mendelian |
| 256 | 0.845 | 0.798 | Mendelian |
| 1001 | 0.846 | 0.780 | Mendelian |
| 2026 | 0.811 | 0.787 | Mendelian |

### Key Findings

1. **Mendelian inheritance produces consistently higher fitness** across all seeds and nearly all generations (96.4% win rate, Cohen's d = 2.23).

2. **Random agents lose skills over time** (4.35 to 2.52 mean skills by gen 49) because there is no inheritance mechanism to maintain skill-enabling trait combinations. Mendelian agents maintain ~4.5 skills through heritable trait preservation.

3. **Mendelian populations show lower fitness variance** (sd 0.016 vs 0.031), indicating more stable evolution. Inheritance provides a consistent floor.

4. **The advantage is sustained, not transient.** The fitness gap does not close over 50 generations, suggesting inheritance provides a structural advantage that random reassignment cannot replicate.

## Literature Position

No existing system combines all three: Mendelian diploid inheritance, executable skill portfolios, and measurable fitness. The closest related work:

- **EvoPrompt / PromptBreeder** (ICLR/ICML 2024): evolve prompt text, no skills, no biological inheritance
- **FunSearch / AlphaEvolve** (Nature 2023, DeepMind 2025): evolve code, not agent capabilities
- **ADAS** (ICLR 2025): evolve agent architectures via archive-based search, no Mendelian model
- **AI Scientist** (Sakana 2024/25): single agent, no evolution

## Files

### New Scripts
- `PYTHON/15-skill_executor.py` -- trait gate evaluation + ClawBio API dispatch
- `PYTHON/16-fitness_evaluator.py` -- F1-F4 fitness scoring
- `PYTHON/20-evolve_v2.py` -- v2 orchestrator with fitness-weighted mating

### Modified Scripts
- `PYTHON/01-soul2dna.py` -- filter to exclude offspring when recompiling gen-0
- `PYTHON/02-genomematch.py` -- optimized genome loading with filename prefix filtering

### New Data
- `DATA/skill_registry.json` -- 7 skill definitions with trait gates and config formulas
- `DATA/SOULS/{scientist}.soul.md` -- 12 new biomedical scientist profiles
- `OUTPUT/replicates_combined.csv` -- full experiment data (500 rows)

## Commands

```bash
# Show skill matrix for gen-0 founders
python PYTHON/15-skill_executor.py --matrix

# Inspect a single agent's skills
python PYTHON/15-skill_executor.py --agent darwin-g0

# Run single Mendelian evolution (dry-run, no cost)
python PYTHON/20-evolve_v2.py --dry-run --generations 50 --seed 42

# Run single Random control (dry-run, no cost)
python PYTHON/20-evolve_v2.py --random-skills --dry-run --generations 50 --seed 42

# Run full experiment (5 replicates x 2 conditions)
python /tmp/run_replicates_v2.py
```

## Next Steps (v0.3)

1. Run with real Haiku API calls for meaningful synthesis quality scores (~$1)
2. Add monolith control (single agent, all skills unlocked)
3. Add Moltbook sharing ablation (test swarm knowledge transfer)
4. Generate publication figures from replicates_combined.csv
5. Submit paper: "Mendelian Inheritance of Executable Skills Produces Emergent Specialization in LLM Agent Populations"
