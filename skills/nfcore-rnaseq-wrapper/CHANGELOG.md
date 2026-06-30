# nfcore-rnaseq-wrapper — Changelog

All notable changes to the `nfcore-rnaseq-wrapper` ClawBio skill are documented
here. The format roughly follows [Keep a Changelog](https://keepachangelog.com)
and the wrapper version is tracked in `SKILL.md` YAML frontmatter.

## [Unreleased] — 0.1.0

### Fixed

- **`clawbio run rnaseq-pipeline` now forwards `-c`/`--config` Nextflow config
  files.** The launcher (`clawbio/cli.py`) accepted `-c`/`--config` but forwarded
  only `--nextflow-config`, silently dropping configs supplied with the short or
  `--config` spelling. The three spellings are now a single repeatable option and
  every entry is forwarded as `--nextflow-config` (which the wrapper accepts as an
  alias), so config files reach Nextflow regardless of spelling.
- **`--timeout-hours` and `--allow-pipeline-version-override` are now forwardable.**
  Both are real wrapper flags (and were already exposed for the sibling pipelines)
  but were absent from the launcher allowlist, so the extra-args filter dropped
  them before they reached the wrapper. `--allow-pipeline-version-override` is also
  recorded as value-free so the filter cannot consume the following token.
- **Output directory inside the repository now raises the dedicated
  `OUTPUT_DIR_INSIDE_REPO` code** instead of the misleading
  `OUTPUT_DIR_NOT_WRITABLE` (which implied a permissions problem), matching
  nfcore-sarek and nfcore-scrnaseq.
- **macOS + Docker `/tmp` guard is now accurate and robust (demo-mode parity).**
  The preflight warning previously said output under `/tmp` "may be slow or
  unreliable due to VirtioFS behavior", which mis-describes the actual failure:
  Colima does not share `/tmp` into its VM, so a `/tmp` work-dir hard-fails with
  `.command.run: No such file or directory`. The warning now states the real
  cause and fix (move `--output` under HOME), uses a resolve-based
  `is_under_tmp` check (shared in `schemas.py`) instead of a brittle string
  prefix, and the executor appends the same actionable hint to `EXECUTION_FAILED`
  — matching nfcore-sarek and nfcore-scrnaseq.

### Added

- **`--allow-remote-inputs` opt-in (local-first by default).** Remote samplesheet
  inputs and reference paths (`s3://`, `gs://`, `https://`, `ftp://`, …) are now
  rejected at preflight (`REMOTE_INPUT_NOT_ALLOWED`) unless the flag is passed, in
  which case a runtime warning names every path fetched over the network. The
  object-store `--work-dir` is not gated. Shared verbatim with
  `nfcore-scrnaseq/sarek`.
- **Control-flag parity with the sibling wrappers.** Added `--work-dir` (Nextflow
  work directory override; accepts a local path or an object-store URI for cloud
  executors; was hardcoded to `<output>/upstream/work`) and `-c`/`--config` as
  aliases of `--nextflow-config`. The executor/command-builder now accept a
  `Path | str` work dir so remote URIs pass through verbatim.

### Changed

- **`--timeout-hours 0` now disables the wall-clock cap** (was rejected at
  preflight). The default still applies a 12h cap; `0` is an explicit opt-out for
  long HPC/cloud runs — parity with `nfcore-scrnaseq/sarek`. `_resolve_timeout_seconds`
  returns `int | None`, the executor accepts `None`, negative values are still
  rejected, and the macOS compatibility config falls back to the default ceiling
  when the cap is disabled. A `[provenance]` stage log was added so all three
  wrappers share the identical stage-prefix set.

### Added

- **Order-independent test suite (shared mechanism).** `tests/conftest.py` now
  also carries the canonical-object bare-module isolation block shared verbatim
  with the sibling wrappers (in addition to this skill's existing per-file
  guards), so the cross-skill isolation mechanism is identical across all three.
- **Converged CLI presentation with the sibling wrappers.** Added a startup
  banner (`--no-banner`), `-v/--verbose`, and sarek-style staged progress logs
  (`[preflight]`/`[execute]`/`[outputs]`/`[report]`/`[done]`) plus a
  human-readable boxed error on stdout — while keeping the machine-readable JSON
  error on stderr and `result.json` on disk. `--verbose`/`--no-banner` are
  registered in the `clawbio.py` runner allowlist and the replay-drift guards.
- **Robust `main()` entrypoint.** `main()` now returns exit code `130` on
  `KeyboardInterrupt` (the SIGINT convention) instead of letting the interrupt
  propagate as a traceback. Matches `nfcore-sarek-wrapper` and
  `nfcore-scrnaseq-wrapper`.

### Notes

- This wrapper is the reference implementation for the cross-wrapper
  **input-readability policy**: the launcher never pre-checks FASTQ/BAM
  readability (`os.access(R_OK)`) because Nextflow reads the data in the true
  execution context (often a root container under the default Docker profile),
  where a launcher-side probe would false-block valid runs. Existence is
  validated (`MISSING_FASTQ`); readability is deferred to Nextflow staging. The
  rationale is documented inline in `errors.py`.
