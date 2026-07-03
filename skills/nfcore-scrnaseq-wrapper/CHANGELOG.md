# nfcore-scrnaseq-wrapper — Changelog

All notable changes to the `nfcore-scrnaseq-wrapper` ClawBio skill are documented
here. The format roughly follows [Keep a Changelog](https://keepachangelog.com)
and the wrapper version is tracked in `SKILL.md` YAML frontmatter.

## [Unreleased] — 0.1.0

### Fixed

- **`--demo` no longer reports `Samples: 0`.** The reported sample count trusted a
  local samplesheet count of `0` instead of falling back to the samples detected
  in the outputs. Under `--demo` the upstream `-profile test` supplies samples
  remotely (so the local count is 0) while the run still produces real samples;
  the count now uses a positive local count when present and otherwise falls back
  to `samples_detected`.
- **`result.json` carries the shared `ok`/`status` contract.** A successful run's
  envelope now includes `status: "ok"` and `ok: true`, and a failed run includes
  `status: "error"` alongside the existing `ok: false` — a minimal discriminator
  shared with nfcore-sarek/rnaseq (via the opt-in `status`/`ok` parameters on the
  shared `write_result_json` helper).

- **`clawbio run scrnaseq-pipeline` now forwards `--nextflow-config`.** The launcher
  (`clawbio/cli.py`) forwarded only `-c`/`--config`, silently dropping configs
  supplied via `--nextflow-config`. All three spellings are now a single repeatable
  option, normalised and forwarded as `--nextflow-config` (the wrapper accepts them
  as aliases).
- **Output directory inside the ClawBio source tree is now rejected** at preflight
  with the dedicated `OUTPUT_DIR_INSIDE_REPO` code, matching nfcore-rnaseq and
  nfcore-sarek. Previously this wrapper alone permitted writing multi-gigabyte
  pipeline outputs into the repository checkout.
- **`--demo` under `NXF_OFFLINE` now fails fast with a clear message.** Demo mode
  runs nf-core's upstream `-profile test`, whose FASTQs and references are remote
  GitHub URLs; on an offline/sandboxed host the nf-schema plugin previously aborted
  with a cryptic `does not exist`. Preflight now detects `NXF_OFFLINE` + demo and
  raises `DEMO_REQUIRES_NETWORK` with an actionable fix. Docs (SKILL.md, AGENTS.md)
  clarify that `--demo` downloads only nf-core public test data — no user/genetic
  data is uploaded — so it is compatible with the local-first guarantee.
- **nf-core-native snake_case flag spellings are now accepted via the launcher.**
  nf-core parameters are snake_case while the wrapper exposes them as hyphenated
  flags. A user copying an upstream nf-core command previously had the token
  silently dropped by the launcher's INT-001 allowlist filter, which matches exact
  (hyphenated) tokens. `clawbio run scrnaseq-pipeline` now canonicalises `_`↔`-`
  when matching the allowlist and forwards the wrapper's canonical hyphen spelling
  (which its parser already accepts). Scoped to the three nf-core pipeline skills;
  other skills keep exact-match filtering.
- **Environment post-failure hints on `EXECUTION_FAILED`.** When a run fails, the
  executor scans the captured Nextflow logs and appends an actionable hint on a
  known environment signature — diagnosed from the actual error text, so no
  resource thresholds are invented. `Process requirement exceeds available memory`
  yields a hint to cap resources via a `-c` config using `process.resourceLimits`;
  `Network is unreachable` / a Java connection exception (common on IPv6-only /
  NAT64 hosts, where the JVM prefers IPv4) yields a hint to verify outbound
  DNS/HTTPS and set `NXF_OPTS='-Djava.net.preferIPv6Addresses=true'`. Shared
  verbatim across the three wrappers.

### Changed

- **Remote input/reference URIs are gated behind `--allow-remote-inputs`
  (local-first by default).** Samplesheet FASTQs and reference/index paths must
  be local unless the flag is passed; otherwise preflight rejects remote URIs
  (`s3://`, `gs://`, `https://`, `ftp://`, …) with `REMOTE_INPUT_NOT_ALLOWED`, so
  the "keeps processing local / prevents accidental cloud access" guarantee is
  enforced by the code. With `--allow-remote-inputs` (a flag shared by all three
  nf-core wrappers), remote values pass through verbatim — only the basename is
  validated, existence is deferred to Nextflow staging — and preflight emits a
  runtime warning naming every path fetched over the network. Local paths are
  always existence-checked (`MISSING_FASTQ` / `MISSING_REFERENCE`). Resolved FASTQ
  values are `Path | str` so remote URIs are written unchanged to the normalized
  samplesheet and provenance (no `Path().as_posix()` `//`-collapse).
- **SKILL.md frontmatter aligned to the canonical `SKILL-TEMPLATE` schema** so the
  machine catalog (`skills/catalog.json`) and the generator never drift: the
  `trigger_keywords` list now lives under `metadata.openclaw` (was a bare
  `metadata.trigger_keywords` key) and a `metadata.demo_data` entry was added.
  Matches `nfcore-sarek-wrapper` and `nfcore-rnaseq-wrapper`.
- **Validation philosophy unified with the sibling wrappers.** The launcher-side
  `os.access(R_OK)` FASTQ readability pre-check was removed: the wrapper never
  reads FASTQ data — Nextflow does, often inside a root container under the
  default Docker profile — so a launcher-side readability probe can false-block
  valid runs. Existence and regular-file type are still validated; readability is
  deferred to Nextflow's staging in the true execution context. A path that
  exists but is not a regular file now raises `MISSING_FASTQ` (was
  `FASTQ_NOT_READABLE`). Mirrors `nfcore-rnaseq-wrapper`'s documented policy.

### Changed

- **Logging consistency**: added a dedicated `[provenance]` stage line so all
  three wrappers emit the identical stage-prefix set.
- **Config-flag parity**: `--nextflow-config` is now accepted as an alias of
  `-c`/`--config`, so the same custom-config flag names work across all three
  wrappers.

### Added

- **Order-independent test suite.** `tests/conftest.py` now claims this skill's
  bare modules (`errors`, `schemas`, …) via a canonical-object cache — at
  collection and before each test — so a single `pytest` session that also
  collects the sibling wrapper suites (e.g. `make test`) no longer hits
  cross-skill module shadowing. Shared verbatim across all three wrappers.
- **Converged CLI presentation with the sibling wrappers.** Added a startup
  banner (`--no-banner`), `-v/--verbose`, and sarek-style staged progress logs
  (`[preflight]`/`[execute]`/`[outputs]`/`[report]`/`[done]`) plus a
  human-readable boxed error on stdout — while keeping the machine-readable JSON
  error on stderr (a robust fallback that survives a failed `result.json` write)
  and `result.json` on disk as the machine contract. `--verbose`/`--no-banner`
  are registered in the `clawbio.py` runner allowlist.
- **Robust `main()` entrypoint.** `main()` now accepts an explicit `argv` list
  (unit-testable without mutating `sys.argv`) and returns exit code `130` on
  `KeyboardInterrupt` (the SIGINT convention) instead of dumping a traceback.
  Matches `nfcore-sarek-wrapper` and `nfcore-rnaseq-wrapper`.

### Removed

- Dead `ErrorCode.FASTQ_NOT_READABLE` constant (no longer referenced after the
  readability-precheck removal) and the now-unused `import os` in
  `samplesheet_builder.py`.
