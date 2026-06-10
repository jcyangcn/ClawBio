#!/usr/bin/env python3
"""ClawBio CLI entry shim.

The implementation now lives in the installable :mod:`clawbio.cli` module so it
ships inside the ``pip install clawbio`` wheel. This thin wrapper keeps the
``python clawbio.py ...`` developer workflow working and keeps the public
``run_skill`` / ``list_skills`` / ``upload_profile`` callables importable from
the repository root (e.g. ``spec_from_file_location`` in the test suite).

Installed users get the same behaviour via the ``clawbio`` console command,
which is wired to :func:`clawbio.cli.main`.
"""

from clawbio.cli import list_skills, main, run_skill, upload_profile

__all__ = ["list_skills", "main", "run_skill", "upload_profile"]


if __name__ == "__main__":
    main()
