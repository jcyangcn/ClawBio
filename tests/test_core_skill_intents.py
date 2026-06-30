from __future__ import annotations

from pathlib import Path

from clawbio.cli import SKILLS
from clawbio.skill_intents import load_skill_intent_descriptors, plan_skill_intent


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _plan(text: str, requested_skill: str | None = None, requested_mode: str | None = None):
    return plan_skill_intent(
        user_text=text,
        requested_skill=requested_skill,
        requested_mode=requested_mode,
        attachments=[],
        skill_registry=SKILLS,
        project_root=PROJECT_ROOT,
    )


def test_core_descriptors_are_discoverable():
    loaded = load_skill_intent_descriptors(SKILLS, PROJECT_ROOT)
    descriptor_paths = [descriptor["_descriptor_path"] for descriptor in loaded]
    descriptors = {descriptor["skill"]: descriptor for descriptor in loaded}

    assert len(descriptor_paths) == len(set(descriptor_paths))
    assert {"gwas", "clinpgx", "pharmgx", "prs", "profile"} <= set(descriptors)


def test_gwas_descriptor_extracts_rsid_argument():
    plan = _plan("look up rs3798220 in GWAS databases")

    assert plan.status == "planned"
    assert plan.skill == "gwas"
    assert plan.intent_id == "variant_lookup"
    assert plan.executions[0].skill == "gwas"
    assert plan.executions[0].argv[-2:] == ["--rsid", "rs3798220"]


def test_clinpgx_descriptor_extracts_gene_argument():
    plan = _plan("look up CYP2D6 on ClinPGx")

    assert plan.status == "planned"
    assert plan.skill == "clinpgx"
    assert plan.intent_id == "gene_lookup"
    assert plan.executions[0].skill == "clinpgx"
    assert plan.executions[0].argv[-2:] == ["--gene", "CYP2D6"]


def test_core_demo_descriptors_require_explicit_demo_text():
    weak = _plan("run pharmacogenomics", requested_skill="pharmgx", requested_mode="demo")

    assert weak.status == "needs_input"
    assert weak.skill == "pharmgx"
    assert weak.intent_id == "legacy_fallback"

    explicit = _plan("run the pharmgx demo", requested_skill="pharmgx", requested_mode="demo")

    assert explicit.status == "planned"
    assert explicit.skill == "pharmgx"
    assert explicit.intent_id == "demo_report"
    assert explicit.executions[0].argv[-1] == "--demo"


def test_profile_and_prs_demo_descriptors_route_explicit_demos():
    profile = _plan("run the profile report demo", requested_skill="profile", requested_mode="demo")
    prs = _plan("run the PRS demo", requested_skill="prs", requested_mode="demo")

    assert profile.status == "planned"
    assert profile.skill == "profile"
    assert profile.executions[0].argv[-1] == "--demo"
    assert prs.status == "planned"
    assert prs.skill == "prs"
    assert prs.executions[0].argv[-1] == "--demo"
