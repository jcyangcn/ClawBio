# Packaging & distribution

How ClawBio ships as `pip install clawbio` and `conda install -c bioconda clawbio`.

## What changed to make the package installable

Before this work, the wheel shipped only the thin `clawbio/` bridge package: no
engine, no skills, no console command, so `pip install clawbio` produced an
import error and ran nothing. The fix:

- The CLI engine moved from the root `clawbio.py` into the package at
  `clawbio/cli.py`, so it ships inside the wheel. Root `clawbio.py` is now a thin
  shim that keeps `python clawbio.py ...` and the `spec_from_file_location` test
  hooks working.
- A `clawbio` console command is wired via `[project.scripts]`
  (`clawbio = "clawbio.cli:main"`).
- `clawbio/cli.py` resolves its content root for both layouts: the repo root in a
  source checkout, the package directory in an installed wheel. Output and patient
  profiles are routed to the current working directory once installed, never into
  read-only package data.
- The build backend is `hatchling`. `hatch_build.py` force-includes a curated
  subset of `skills/` and `examples/` into the wheel under `clawbio/` **without
  moving anything on disk**, so the Claude Code plugin marketplace and every
  public `skills/...` URL stay intact.

### Wheel payload policy (`hatch_build.py`)

- Every skill's logic (`.md` / `.py` / `.yaml` / `.sh`) ships, so agents can read
  and call all 84 skills.
- Small data files (< 256 KB) ship; heavy demo data (gzipped genomes,
  expected-output images, big catalogs) does not.
- The three headline skills ship their full demo payload so the quick-start works
  offline immediately: `pharmgx-reporter`, `drug-photo`, `gwas-lookup`.

Result: a ~3.2 MB wheel (vs the broken 100 KB one), all 84 skills present,
`clawbio run pharmgx --demo` working straight after install.

## Build locally

```bash
uv build                      # builds dist/clawbio-<v>.tar.gz and the wheel
shasum -a 256 dist/clawbio-*.tar.gz   # sha256 the bioconda recipe pins
```

Verify in a clean environment:

```bash
uv venv /tmp/cb && uv pip install --python /tmp/cb/bin/python dist/clawbio-*.whl
/tmp/cb/bin/clawbio run pharmgx --demo
```

## Publish to PyPI (maintainer action: needs a PyPI token)

PyPI release names and versions are permanent and cannot be reused, so publish
deliberately.

1. Bump the version in `clawbio/__init__.py` (single source of truth; the wheel
   reads it via `[tool.hatch.version]`). Keep `CITATION.cff` / `CHANGELOG.md` in
   step.
2. Clean build: `rm -rf dist && uv build`.
3. Smoke-test the wheel in a clean venv (above).
4. Upload to TestPyPI first, install from there, then upload to PyPI:
   ```bash
   uvx twine upload --repository testpypi dist/*
   uvx twine upload dist/*
   ```
5. Tag the release: `git tag v0.5.1 && git push --tags`.

## Publish to bioconda (maintainer action: needs a bioconda-recipes fork)

bioconda recipes live in the `bioconda/bioconda-recipes` repo, not here. The
recipe in `packaging/bioconda/meta.yaml` is the source to copy in.

1. **Pre-flight: every run dependency is available on conda channels** (verified
   2026-06-05 via the anaconda.org API, which bioconda's channel stack covers):
   - `pydeseq2` -> bioconda
   - `rocrate` -> conda-forge
   - `conda-lock` -> conda-forge (note: it is a *runtime* dependency here, which is
     unusual; consider moving it to an optional group upstream)
   - everything else (biopython, numpy, pandas, scikit-learn, matplotlib-base,
     google-auth, google-cloud-bigquery, openai, opentelemetry-sdk, pyyaml) is on
     conda-forge.

   Re-verify before submitting:
   ```bash
   conda search -c conda-forge -c bioconda "pydeseq2"
   conda search -c conda-forge -c bioconda "rocrate"
   ```
2. Fork `bioconda/bioconda-recipes`, create `recipes/clawbio/meta.yaml` from this
   recipe.
3. Update `sha256` to match the sdist actually on PyPI:
   `shasum -a 256` the downloaded `clawbio-<v>.tar.gz` from PyPI.
4. Lint and build locally with `bioconda-utils` (or rely on the PR CI):
   ```bash
   bioconda-utils lint recipes config.yml --packages clawbio
   bioconda-utils build recipes config.yml --packages clawbio
   ```
5. Open a PR. The `@BiocondaBot please add label` flow and CI handle the rest;
   merging publishes to the `bioconda` channel automatically.

## Notes / open items

- The sdist is ~12 MB because it carries the full `skills/` tree (the wheel build
  hook reads it to assemble the curated wheel). If a leaner sdist is wanted later,
  add an sdist-target filter mirroring the wheel hook.
- `README.md`'s "No `pip install clawbio` package yet" line should be removed once
  the first PyPI release is live.
