import importlib.util
from pathlib import Path


def _load_generate_catalog_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "generate_catalog.py"
    spec = importlib.util.spec_from_file_location("generate_catalog", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_load_skills_registry_reads_package_cli_registry():
    generate_catalog = _load_generate_catalog_module()

    registered_aliases = generate_catalog.load_skills_registry()

    assert "fastreer" in registered_aliases
    assert "analyze-fasta" in registered_aliases
    assert "pharmgx" in registered_aliases


def test_build_catalog_populates_cli_aliases_from_package_registry():
    generate_catalog = _load_generate_catalog_module()

    catalog = {entry["name"]: entry for entry in generate_catalog.build_catalog()}

    assert catalog["fastreer"]["cli_alias"] == "fastreer"
    assert catalog["fastreer"]["demo_command"] == "python clawbio.py run fastreer --demo"
    assert catalog["analyze-fasta"]["cli_alias"] == "analyze-fasta"
    assert catalog["analyze-fasta"]["demo_command"] == "python clawbio.py run analyze-fasta --demo"


def test_build_catalog_adds_objective_maturity_tiers():
    generate_catalog = _load_generate_catalog_module()

    catalog = {entry["name"]: entry for entry in generate_catalog.build_catalog()}

    assert catalog["pharmgx-reporter"]["maturity_tier"] == "ci-validated"
    assert catalog["pharmgx-reporter"]["maturity_evidence"] == {
        "has_skill_md": True,
        "has_script": True,
        "has_tests": True,
        "has_demo": True,
        "cli_registered": True,
        "ci_tested": True,
        "benchmark_validated": False,
    }

    assert catalog["fastreer"]["maturity_tier"] == "cli-registered"
    assert catalog["fastreer"]["maturity_evidence"]["cli_registered"] is True
    assert catalog["fastreer"]["maturity_evidence"]["ci_tested"] is False

    assert catalog["claw-semantic-sim"]["maturity_tier"] == "spec-only"
    assert catalog["claw-semantic-sim"]["maturity_evidence"]["has_script"] is False
