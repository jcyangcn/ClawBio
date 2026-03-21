"""
05-simulate.py — Genomebook Population Simulator

Purpose: Run N generations of agent evolution with matchmaking, recombination,
         mutation, clinical evaluation, and population tracking.
Input:  Generation-0 genomes from DATA/GENOMES/
Output: Per-generation snapshots in DATA/GENERATIONS/, summary statistics in OUTPUT/
"""

import json
import random
import csv
import time
import gc
from pathlib import Path
from collections import Counter, defaultdict

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib import import_module

genomematch = import_module("02-genomematch")
recombinator = import_module("04-recombinator")

random.seed(2026)

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "DATA"
GENOMES_DIR = DATA / "GENOMES"
GENERATIONS_DIR = DATA / "GENERATIONS"
OUTPUT_DIR = BASE / "OUTPUT"

GENERATIONS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SIM_CONFIG = {
    "num_generations": 100,
    "offspring_per_pair": 2,
    "population_cap": 30,
    "retirement_age": 3,
    "death_threshold": 0.15,
    "conversation_generations": [0, 5, 10, 25, 50, 100],
    "min_population": 4,
}


def load_generation_0():
    genomes = {}
    for gf in sorted(GENOMES_DIR.glob("*-g0.genome.json")):
        g = json.load(open(gf))
        g["birth_generation"] = 0
        genomes[g["id"]] = g
    return genomes


def save_generation_snapshot(generation, population, stats):
    gen_dir = GENERATIONS_DIR / f"gen-{generation:04d}"
    gen_dir.mkdir(parents=True, exist_ok=True)

    agents_summary = []
    for gid, g in population.items():
        agents_summary.append({
            "id": g["id"],
            "name": g.get("name", gid),
            "sex": g["sex"],
            "generation": g["generation"],
            "parents": g.get("parents", [None, None]),
            "health_score": g.get("health_score", 1.0),
            "trait_scores": g.get("trait_scores", {}),
            "clinical_history": g.get("clinical_history", []),
            "mutation_count": len(g.get("mutations", [])),
            "ancestry": g.get("ancestry", ""),
        })

    with open(gen_dir / "agents.json", "w") as f:
        json.dump(agents_summary, f)
    with open(gen_dir / "stats.json", "w") as f:
        json.dump(stats, f)


def population_stats(population, generation):
    agents = list(population.values())
    males = sum(1 for a in agents if a["sex"] == "Male")
    females = len(agents) - males

    trait_sums = defaultdict(float)
    trait_counts = defaultdict(int)
    for a in agents:
        for t, s in a.get("trait_scores", {}).items():
            trait_sums[t] += s
            trait_counts[t] += 1
    trait_means = {t: round(trait_sums[t] / trait_counts[t], 4) for t in trait_sums}

    disease_counts = Counter()
    for a in agents:
        for cond in a.get("clinical_history", []):
            disease_counts[cond["name"]] += 1
    disease_prev = {d: round(c / len(agents), 4) for d, c in disease_counts.most_common(10)}

    health_scores = [a.get("health_score", 1.0) for a in agents]
    avg_h = round(sum(health_scores) / len(health_scores), 4) if health_scores else 0

    return {
        "generation": generation,
        "population_size": len(agents),
        "males": males,
        "females": females,
        "avg_health": avg_h,
        "min_health": round(min(health_scores), 4) if health_scores else 0,
        "max_health": round(max(health_scores), 4) if health_scores else 0,
        "total_mutations": sum(len(a.get("mutations", [])) for a in agents),
        "disease_prevalence": disease_prev,
        "trait_means": trait_means,
    }


def cull_population(population, generation, config):
    to_remove = []
    for gid, g in list(population.items()):
        age = generation - g.get("birth_generation", 0)
        health = g.get("health_score", 1.0)
        if health < config["death_threshold"]:
            to_remove.append(gid)
        elif age >= config["retirement_age"] and generation > 5:
            to_remove.append(gid)

    for gid in to_remove:
        population.pop(gid, None)

    if len(population) > config["population_cap"]:
        by_health = sorted(population.keys(), key=lambda k: population[k].get("health_score", 1.0))
        excess = len(population) - config["population_cap"]
        for gid in by_health[:excess]:
            population.pop(gid, None)

    return population


def trim_agent_memory(agent):
    """Reduce in-memory footprint of an agent — keep only what breeding needs."""
    muts = agent.get("mutations", [])
    if isinstance(muts, list) and len(muts) > 5:
        agent["mutations"] = muts[:3]


def simulate():
    trait_reg, disease_reg = recombinator.load_registries()
    population = load_generation_0()
    print(f"Generation 0: {len(population)} agents loaded")

    csv_path = OUTPUT_DIR / "generation_summary.csv"
    csv_file = open(csv_path, "w", newline="")
    csv_fields = ["generation", "population", "males", "females", "avg_health", "diseases_active", "mutations"]
    writer = csv.DictWriter(csv_file, fieldnames=csv_fields)
    writer.writeheader()

    trait_drift = []
    disease_drift = []

    t0 = time.time()
    final_gen = 0

    for gen in range(SIM_CONFIG["num_generations"] + 1):
        final_gen = gen
        stats = population_stats(population, gen)

        if gen in SIM_CONFIG["conversation_generations"]:
            save_generation_snapshot(gen, population, stats)

        m = stats["males"]
        f = stats["females"]
        h = stats["avg_health"]
        d = len(stats["disease_prevalence"])
        mut = stats["total_mutations"]
        print(f"  Gen {gen:4d} | Pop: {stats['population_size']:3d} ({m}M/{f}F) | "
              f"Health: {h:.3f} | Diseases: {d} | Mutations: {mut}")

        writer.writerow({
            "generation": gen,
            "population": stats["population_size"],
            "males": m,
            "females": f,
            "avg_health": h,
            "diseases_active": d,
            "mutations": mut,
        })

        trait_drift.append({"generation": gen, **stats["trait_means"]})
        disease_drift.append({"generation": gen, **stats["disease_prevalence"]})

        if gen == SIM_CONFIG["num_generations"]:
            break

        # Check viable population
        male_ids = [gid for gid, g in population.items() if g["sex"] == "Male"]
        female_ids = [gid for gid, g in population.items() if g["sex"] == "Female"]

        if len(male_ids) < 1 or len(female_ids) < 1:
            print(f"  EXTINCTION at generation {gen}")
            break

        # Match and breed
        pairings = genomematch.match_generation(population, disease_reg)
        selected = genomematch.select_mating_pairs(
            pairings, max_pairs=min(len(male_ids), len(female_ids))
        )

        new_offspring = {}
        for pair in selected:
            father = population[pair["male"]]
            mother = population[pair["female"]]
            children = recombinator.breed_pair(
                father, mother,
                generation=gen + 1,
                num_offspring=SIM_CONFIG["offspring_per_pair"],
                trait_reg=trait_reg,
                disease_reg=disease_reg,
            )
            for child in children:
                child["birth_generation"] = gen + 1
                new_offspring[child["id"]] = child

        population.update(new_offspring)
        population = cull_population(population, gen + 1, SIM_CONFIG)

        # Memory management
        for g in population.values():
            trim_agent_memory(g)
        if gen % 5 == 0:
            gc.collect()

    csv_file.close()
    elapsed = time.time() - t0

    with open(OUTPUT_DIR / "trait_drift.json", "w") as f:
        json.dump(trait_drift, f)
    with open(OUTPUT_DIR / "disease_drift.json", "w") as f:
        json.dump(disease_drift, f)

    print(f"\n{'='*60}")
    print(f"SIMULATION COMPLETE")
    print(f"{'='*60}")
    print(f"Generations:  {final_gen}")
    print(f"Final pop:    {stats['population_size']}")
    print(f"Time elapsed: {elapsed:.2f}s")
    print(f"Output:       {csv_path}")
    print(f"Drift data:   {OUTPUT_DIR}/")
    print(f"Snapshots:    {GENERATIONS_DIR}/")


if __name__ == "__main__":
    simulate()
