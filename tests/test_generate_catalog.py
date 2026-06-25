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
