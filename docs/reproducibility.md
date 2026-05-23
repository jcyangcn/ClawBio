# Reproducibility Guide

ClawBio's reproducibility support is designed to let someone replay a completed skill run without needing the original agent session. The exact files can vary by skill, but the current shared pattern centers on a `reproducibility/` directory written inside the chosen output directory.

This guide uses [`multiqc-reporter`](../skills/multiqc-reporter/) as the concrete example because it already has a validated direct-script workflow. For this example, the exact recorded replay path is a direct Python invocation of `skills/multiqc-reporter/multiqc_reporter.py` rather than `python clawbio.py run`.

## What The Bundle Contains

Many skills write these files under `<output_dir>/reproducibility/`:

- `commands.sh`: a replay script for the same skill run
- `environment.yml`: a suggested Conda environment snapshot
- `checksums.sha256`: SHA-256 digests for selected output files

Some skills may also write extra provenance or lock files when supported, such as `runtime-lock.json` or other lock metadata. Do not assume every skill emits the exact same extras.

Keep these limits in mind:

- Reproducibility is not a blanket guarantee that any run will replay unchanged on every machine.
- External tools still need to be installed when a skill depends on them.
- External inputs still need to be available when the original run used user-provided files.
- `analysis_log.md` is not a universal output across all skills.

## Quick Example

`multiqc-reporter` requires `multiqc` on `PATH`, and the replay is direct script invocation:

```bash
python skills/multiqc-reporter/multiqc_reporter.py --demo --output /tmp/multiqc_demo
```

Use a fresh output directory. The skill writes:

- `/tmp/multiqc_demo/report.md`
- `/tmp/multiqc_demo/multiqc_report.html`
- `/tmp/multiqc_demo/multiqc_data/`
- `/tmp/multiqc_demo/reproducibility/`

For this demo run, `reproducibility/commands.sh` records the `--demo` invocation and `reproducibility/environment.yml` captures the suggested Python and `multiqc` environment for the run.

## Recreate The Environment

The simplest path is to inspect the generated environment first:

```bash
cat /tmp/multiqc_demo/reproducibility/environment.yml
```

For `multiqc-reporter`, the important requirement is still that `multiqc` is installed and available on `PATH`. A typical replay setup is:

```bash
conda env create -f /tmp/multiqc_demo/reproducibility/environment.yml
conda activate clawbio-multiqc-reporter
multiqc --version
```

If your workflow or environment manager also produces a lock artifact such as `runtime-lock.json`, treat that as a stricter runtime snapshot than the base `environment.yml`.

## Replay The Analysis

From the repository checkout, rerun the saved command:

```bash
bash /tmp/multiqc_demo/reproducibility/commands.sh
```

For scripts that use the shared reproducibility helpers, `commands.sh` is self-anchoring:

- `OUTPUT_DIR` is derived from the location of `commands.sh`
- `CLAWBIO_ROOT` defaults to the repository path recorded when the bundle was created
- you can override `CLAWBIO_ROOT` if you are replaying from a different checkout

Example with an explicit checkout path:

```bash
CLAWBIO_ROOT="$PWD" bash /tmp/multiqc_demo/reproducibility/commands.sh
```

For non-demo runs, make sure the original input directories still exist. The replay script can only rerun what the skill can still read.

For `multiqc-reporter`, rerunning into the same output directory is valid, but MultiQC may add suffixed artifacts such as `multiqc_report_1.html` or `multiqc_data_1/` instead of replacing the original HTML report and data directory in place. If you want the cleanest manual replay comparison, start from a fresh output directory.

## Verify Outputs

First, inspect the output tree:

```bash
find /tmp/multiqc_demo -maxdepth 2 -type f | sort
```

Then verify the recorded checksums:

```bash
cd /tmp/multiqc_demo
sha256sum -c reproducibility/checksums.sha256
```

`checksums.sha256` covers selected output files recorded by the skill. For `multiqc-reporter`, that includes `report.md`, `multiqc_report.html`, and files under `multiqc_data/` when present.

If a checksum does not match, check whether:

- the external tool version changed
- the input directories changed
- the output directory already contained files from an earlier run

If the replay added suffixed files such as `multiqc_report_1.html`, that does not automatically mean the recorded bundle failed. `checksums.sha256` only verifies the files that the skill originally recorded for that run.

## External Inputs

Not every replay is demo-mode.

When a skill was originally run with user-supplied files or directories:

- keep those inputs available
- keep the relative or absolute paths valid for the replay command
- expect the skill's external dependencies to be installed locally

This matters for `multiqc-reporter` because non-demo runs depend on the original QC directories, and MultiQC itself must be installed before replay.

## Troubleshooting

`multiqc: command not found`

- Install MultiQC first, for example with `pip install multiqc` or via the generated `environment.yml`.

`checksums.sha256` verifies some files but not others

- That is expected. Skills choose which outputs to checksum. Read the skill's `report.md` and `SKILL.md` to see what is considered part of the reproducibility contract.

The replay command points at the wrong repository path

- Override `CLAWBIO_ROOT` when running `commands.sh`.

The run succeeds but the report is empty

- `multiqc-reporter` treats "no recognized QC files found" as a valid MultiQC outcome. Check `multiqc_report.html` and `multiqc_data/multiqc_data.json`.

You expected `analysis_log.md`

- Some skills may emit extra logs, but it is not a universal ClawBio guarantee today.
