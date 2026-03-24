"""
01-soul2dna.py — Soul2DNA Compiler for Genomebook

Purpose: Parse SOUL.md files → assign alleles at each locus based on trait scores → write .genome.json
Input:  DATA/SOULS/*.soul.md, DATA/trait_registry.json
Output: DATA/GENOMES/*.genome.json

Allele assignment logic:
  - For each trait, the SOUL.md score (0.0–1.0) determines allele distribution across loci.
  - Higher scores → more ALT alleles. Dominance model affects the mapping:
    - additive:  score < 0.33 → ref/ref, 0.33–0.66 → ref/alt, > 0.66 → alt/alt
    - dominant:  score < 0.50 → ref/ref, >= 0.50 → ref/alt or alt/alt (weighted by score)
    - recessive: score < 0.75 → ref/ref or ref/alt, >= 0.75 → alt/alt
  - Multi-locus traits distribute score across loci weighted by effect_size.

Sex determination:
  - Parsed from SOUL.md Identity block (Male/Female).
  - Stored in genome as sex_chromosomes: "XY" or "XX".
"""

import json
import re
import random
from pathlib import Path

random.seed(42)  # Reproducible generation-0 genomes

# Paths
BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "DATA"
SOULS_DIR = DATA / "SOULS"
GENOMES_DIR = DATA / "GENOMES"
TRAIT_REGISTRY = DATA / "trait_registry.json"

GENOMES_DIR.mkdir(parents=True, exist_ok=True)


def load_trait_registry():
    with open(TRAIT_REGISTRY, "r") as f:
        return json.load(f)


def parse_soul(filepath):
    """Extract trait scores and metadata from a SOUL.md file."""
    text = filepath.read_text()

    # Extract sex
    sex = None
    sex_match = re.search(r"\*\*Sex:\*\*\s*(Male|Female)", text, re.IGNORECASE)
    if sex_match:
        sex = sex_match.group(1).capitalize()
    else:
        raise ValueError(f"No sex found in {filepath.name}")

    # Extract name
    name = None
    name_match = re.search(r"\*\*Name:\*\*\s*(.+)", text)
    if name_match:
        name = name_match.group(1).strip()

    # Extract ancestry
    ancestry = None
    anc_match = re.search(r"\*\*Ancestry:\*\*\s*(.+)", text)
    if anc_match:
        ancestry = anc_match.group(1).strip()

    # Extract domain
    domain = None
    dom_match = re.search(r"\*\*Domain:\*\*\s*(.+)", text)
    if dom_match:
        domain = dom_match.group(1).strip()

    # Extract era
    era = None
    era_match = re.search(r"\*\*Era:\*\*\s*(.+)", text)
    if era_match:
        era = era_match.group(1).strip()

    # Extract summary
    summary = None
    sum_match = re.search(r"## Summary\n(.+)", text, re.DOTALL)
    if sum_match:
        summary = sum_match.group(1).strip()

    # Extract trait scores (format: "trait_name: 0.XX")
    traits = {}
    for match in re.finditer(r"^(\w+):\s*([\d.]+)\s*$", text, re.MULTILINE):
        trait_name = match.group(1)
        score = float(match.group(2))
        traits[trait_name] = score

    return {
        "name": name,
        "sex": sex,
        "ancestry": ancestry,
        "domain": domain,
        "era": era,
        "summary": summary,
        "traits": traits,
    }


def score_to_genotype(score, locus, trait_score_contribution):
    """Convert a trait score into a diploid genotype at a single locus.

    Args:
        score: Overall trait score (0.0–1.0)
        locus: Locus definition from trait registry
        trait_score_contribution: This locus's proportional contribution to the trait

    Returns:
        list of two alleles, e.g. ["A", "G"]
    """
    ref = locus["ref"]
    alt = locus["alt"]
    dominance = locus["dominance"]

    # Effective score for this locus — weighted by its effect contribution
    # Add small random noise (±0.05) for genetic variation
    effective = score + random.uniform(-0.05, 0.05)
    effective = max(0.0, min(1.0, effective))

    if dominance == "additive":
        if effective < 0.33:
            return [ref, ref]
        elif effective < 0.66:
            # Some randomness in het assignment
            return [ref, alt] if random.random() < 0.7 else [alt, ref]
        else:
            return [alt, alt]

    elif dominance == "dominant":
        # Dominant ALT: even one copy gives phenotype
        if effective < 0.40:
            return [ref, ref]
        elif effective < 0.75:
            return [ref, alt]
        else:
            return [alt, alt]

    elif dominance == "recessive":
        # Recessive ALT: need two copies for phenotype
        if effective < 0.50:
            return [ref, ref]
        elif effective < 0.80:
            return [ref, alt]
        else:
            return [alt, alt]

    # Fallback
    return [ref, ref]


def compile_genome(soul_data, registry):
    """Convert parsed SOUL.md data into a full genome."""
    genome = {
        "id": None,  # Set by caller
        "name": soul_data["name"],
        "sex": soul_data["sex"],
        "sex_chromosomes": "XY" if soul_data["sex"] == "Male" else "XX",
        "ancestry": soul_data["ancestry"],
        "domain": soul_data["domain"],
        "era": soul_data["era"],
        "summary": soul_data["summary"],
        "generation": 0,
        "parents": [None, None],
        "loci": {},
        "trait_scores": soul_data["traits"],
    }

    traits_def = registry["traits"]

    for trait_name, trait_def in traits_def.items():
        score = soul_data["traits"].get(trait_name, 0.5)  # Default 0.5 if missing
        loci = trait_def["loci"]

        # Calculate total effect for proportional weighting
        total_effect = sum(l["effect"] for l in loci)

        for locus in loci:
            locus_id = locus["id"]
            contribution = locus["effect"] / total_effect if total_effect > 0 else 1.0

            # If locus already assigned by a pleiotropic trait, skip
            if locus_id in genome["loci"]:
                continue

            genotype = score_to_genotype(score, locus, contribution)

            genome["loci"][locus_id] = {
                "chromosome": locus["chr"],
                "position": locus["pos"],
                "ref": locus["ref"],
                "alt": locus["alt"],
                "dominance": locus["dominance"],
                "effect_size": locus["effect"],
                "alleles": genotype,
            }

    return genome


def main():
    registry = load_trait_registry()
    soul_files = sorted(f for f in SOULS_DIR.glob("*.soul.md")
                        if not re.match(r"g\d+", f.stem))

    if not soul_files:
        print(f"ERROR: No .soul.md files found in {SOULS_DIR}")
        return

    males = []
    females = []

    for sf in soul_files:
        agent_id = sf.stem.replace(".soul", "")  # e.g. "einstein"
        soul_data = parse_soul(sf)
        genome = compile_genome(soul_data, registry)
        genome["id"] = f"{agent_id}-g0"

        # Write genome
        out_path = GENOMES_DIR / f"{agent_id}-g0.genome.json"
        with open(out_path, "w") as f:
            json.dump(genome, f, indent=2)

        if soul_data["sex"] == "Male":
            males.append(agent_id)
        else:
            females.append(agent_id)

        print(f"  {genome['id']:20s} | {soul_data['sex']:6s} | {len(genome['loci']):2d} loci | {soul_data['name']}")

    print(f"\nCompiled {len(soul_files)} genomes ({len(males)}M / {len(females)}F)")
    print(f"Males:   {', '.join(males)}")
    print(f"Females: {', '.join(females)}")
    print(f"Output:  {GENOMES_DIR}/")


if __name__ == "__main__":
    main()
