"""Guardrails for the pip/conda packaging contract.

These lock in the invariants that make ``pip install clawbio`` work without
regressing the source-checkout developer workflow:

  * the engine resolves its content root for both layouts (dev checkout vs
    installed wheel),
  * writable output never lands inside read-only package data once installed,
  * the console entry point target exists and is callable,
  * the packaged version matches the project metadata source.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import clawbio
from clawbio import cli


def test_version_is_single_sourced():
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.is_file():
        pytest.skip("source-tree only: pyproject.toml not present in installed layout")
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    # Version is dynamic, sourced from clawbio/__init__.py.
    assert "version" in data["project"]["dynamic"]
    assert data["tool"]["hatch"]["version"]["path"] == "clawbio/__init__.py"
    assert clawbio.__version__  # importable and non-empty


def test_console_entry_point_target_is_callable():
    # pyproject wires `clawbio = "clawbio.cli:main"`.
    assert callable(cli.main)


def test_skills_dir_resolves_to_existing_directory():
    # In either layout, SKILLS_DIR must point at a real directory holding skills.
    assert cli.SKILLS_DIR.is_dir()
    assert (cli.SKILLS_DIR / "pharmgx-reporter").is_dir()


def test_content_root_matches_install_flag():
    # _INSTALLED is True only when skills are bundled inside the package dir.
    if cli._INSTALLED:
        assert cli.CLAWBIO_DIR == cli._PKG_DIR
    else:
        assert cli.CLAWBIO_DIR == cli._PKG_DIR.parent


def test_writable_dirs_never_inside_package_when_installed():
    # Output and profiles must not be written under read-only package data.
    if cli._INSTALLED:
        assert cli._PKG_DIR not in cli.DEFAULT_OUTPUT_ROOT.parents
        assert cli._PKG_DIR not in cli.PROFILES_DIR.parents


def test_public_api_surface():
    for name in ("run_skill", "list_skills", "upload_profile", "__version__"):
        assert name in clawbio.__all__
