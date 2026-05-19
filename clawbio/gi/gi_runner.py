"""Shared CLI runner for the six gi-* skills.

Each skill's ``gi_<task>.py`` is a ~20-line config that calls
``run_skill(task=..., demo_path=..., async_mode=...)``. The runner handles
arg parsing, FASTA → predict → ``{data, meta}``, and writes
``report.md`` + ``result.json`` + ``reproducibility/`` per ClawBio
convention.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from clawbio.gi.gi_client import Client, GIError, read_fasta

DISCLAIMER = (
    "ClawBio is a research and educational tool. It is not a medical "
    "device and does not provide clinical diagnoses."
)


def _parse_args(task: str) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=f"ClawBio gi-{task}: {task} prediction via Genomic Intelligence API.")
    p.add_argument("--input", type=Path, dest="input_file", help="Input FASTA (single record)")
    p.add_argument("--output", type=Path, default=Path(f"/tmp/gi-{task}"), help="Output directory")
    p.add_argument("--demo", action="store_true", help="Run with the bundled example FASTA")
    p.add_argument("--model", type=str, default=None, help="Override default model name")
    p.add_argument("--description", type=str, default=None, help="Cell type / assay context (required by gi-expression; ignored by other tasks)")
    p.add_argument("--api-key", type=str, default=None, help="Override GI_API_KEY env (otherwise uses env or bundled hackathon key)")
    p.add_argument("--base-url", type=str, default=None, help="Override GI_BASE_URL (default: https://api.genomicintelligence.ai)")
    return p.parse_args()


def _resolve_input(args: argparse.Namespace, demo_path: Path, task: str) -> Path:
    if args.demo or args.input_file is None:
        if not demo_path.exists():
            print(f"Error: bundled demo fixture missing at {demo_path}", file=sys.stderr)
            sys.exit(1)
        return demo_path
    if not args.input_file.exists():
        print(f"Error: --input file not found: {args.input_file}", file=sys.stderr)
        sys.exit(1)
    return args.input_file


def _summarize(task: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Pick the most useful headline numbers per task from `data`."""
    data = body.get("data") or {}
    summary = data.get("summary") or {}
    out: Dict[str, Any] = {"task": task, "model": data.get("model")}
    if task == "promoter":
        out["promoter_windows"] = summary.get("promoter_windows")
        out["total_windows"] = summary.get("total_windows")
        out["regions"] = data.get("regions") or []
    elif task == "splice":
        out["sites_found"] = summary.get("sites_found")
        out["sites"] = data.get("sites") or []
    elif task == "enhancer":
        out["windows_processed"] = summary.get("windows_processed")
        out["max_activity"] = summary.get("max_activity")
    elif task == "chromatin":
        out["windows_processed"] = summary.get("windows_processed")
        out["total_annotations"] = summary.get("total_annotations")
    elif task == "expression":
        pred = data.get("prediction") or {}
        out["log_tpm"] = pred.get("expression_log_tpm")
        out["tpm"] = pred.get("expression_tpm")
    elif task == "annotation":
        out["transcripts_found"] = summary.get("total_transcripts", summary.get("transcripts_found"))
        out["transcripts"] = data.get("transcripts") or []
    out["raw_summary"] = summary
    return out


def _write_report(task: str, summary: Dict[str, Any], body: Dict[str, Any], output_dir: Path, input_path: Path, sequence_name: str, sequence_length: int, elapsed_ms: float) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(json.dumps({"summary": summary, "full_response": body}, indent=2))

    meta = body.get("meta") or {}
    model = summary.get("model") or "—"
    lines = [
        f"# gi-{task} report",
        "",
        f"- **Sequence**: `{sequence_name}` ({sequence_length:,} bp)",
        f"- **Input file**: `{input_path}`",
        f"- **Model**: `{model}`",
        f"- **Inference time**: {meta.get('inference_time_ms', elapsed_ms):.0f} ms",
        f"- **Request ID**: `{meta.get('request_id', '—')}`",
        f"- **Generated**: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## Headline result",
        "",
    ]
    if task == "promoter":
        lines.append(f"- Promoter windows: **{summary.get('promoter_windows', 0)}** / {summary.get('total_windows', 0)} total")
        regions = summary.get("regions") or []
        if regions:
            lines.append("")
            lines.append("| Window | Start | End | Probability |")
            lines.append("|---|---|---|---|")
            for r in regions[:20]:
                lines.append(f"| {r.get('window_index','-')} | {r.get('start','-')} | {r.get('end','-')} | {r.get('probability','-'):.3f} |" if isinstance(r.get('probability'), (int, float)) else f"| {r.get('window_index','-')} | {r.get('start','-')} | {r.get('end','-')} | {r.get('probability','-')} |")
    elif task == "splice":
        lines.append(f"- Splice sites found: **{summary.get('sites_found', 0)}**")
        sites = (summary.get("sites") or [])[:20]
        if sites:
            lines.append("")
            lines.append("| Position | Kind | Strand | Probability |")
            lines.append("|---|---|---|---|")
            for s in sites:
                lines.append(f"| {s.get('position','-')} | {s.get('kind','-')} | {s.get('strand','-')} | {s.get('probability','-')} |")
    elif task == "enhancer":
        lines.append(f"- Windows processed: **{summary.get('windows_processed', 0)}**")
        if summary.get("max_activity") is not None:
            lines.append(f"- Max predicted activity: **{summary['max_activity']}**")
    elif task == "chromatin":
        lines.append(f"- Windows processed: **{summary.get('windows_processed', 0)}**")
        lines.append(f"- Total annotations across all tracks: **{summary.get('total_annotations', 0)}**")
    elif task == "expression":
        log_tpm = summary.get("log_tpm")
        tpm = summary.get("tpm")
        if log_tpm is not None:
            lines.append(f"- Predicted expression: **{log_tpm:.4f} log(TPM+1)**" + (f" ≈ {tpm:.2f} TPM" if isinstance(tpm, (int, float)) else ""))
        else:
            lines.append("- See `result.json` for the full prediction payload.")
    elif task == "annotation":
        lines.append(f"- Transcripts found: **{summary.get('transcripts_found') or 0}**")
        tx = (summary.get("transcripts") or [])[:20]
        if tx:
            lines.append("")
            lines.append("| Transcript | Start | End | Strand |")
            lines.append("|---|---|---|---|")
            for t in tx:
                lines.append(f"| {t.get('transcript_id','-')} | {t.get('start','-')} | {t.get('end','-')} | {t.get('strand','-')} |")

    lines += [
        "",
        "## Reproducibility",
        "",
        f"- `reproducibility/command.sh` — exact invocation",
        f"- `result.json` — full `{{data, meta}}` response from the API",
        "",
        "## API",
        "",
        f"`POST /v1/tasks/{task}/predict` on `https://api.genomicintelligence.ai` — see <https://docs.genomicintelligence.ai>.",
        "",
        "---",
        "",
        f"_{DISCLAIMER}_",
        "",
    ]
    (output_dir / "report.md").write_text("\n".join(lines))

    repro = output_dir / "reproducibility"
    repro.mkdir(exist_ok=True)
    cmd = f"python skills/gi-{task}/gi_{task.replace('-', '_')}.py --input {input_path} --output {output_dir}\n"
    (repro / "command.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + cmd)
    (repro / "command.sh").chmod(0o755)
    (repro / "environment.json").write_text(json.dumps({
        "skill": f"gi-{task}",
        "skill_version": "0.1.0",
        "api_base_url": os.environ.get("GI_BASE_URL", "https://api.genomicintelligence.ai"),
        "model": summary.get("model"),
        "request_id": meta.get("request_id"),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2))


def run_skill(*, task: str, demo_path: Path, async_mode: bool = False, default_model: Optional[str] = None, default_options: Optional[Dict[str, Any]] = None) -> int:
    """Skill entry-point. Returns process exit code."""
    args = _parse_args(task)
    input_path = _resolve_input(args, demo_path, task)
    sequence_name, sequence = read_fasta(input_path)
    if not sequence:
        print(f"Error: parsed an empty sequence from {input_path}", file=sys.stderr)
        return 1

    client = Client(api_key=args.api_key, base_url=args.base_url)
    model = args.model or default_model
    options: Dict[str, Any] = dict(default_options or {})
    if args.description is not None:
        options["description"] = args.description

    print(f"[gi-{task}] sequence_name={sequence_name} length={len(sequence):,} bp model={model or 'default'} mode={'async' if async_mode else 'sync'}", file=sys.stderr)
    started = time.monotonic()
    try:
        if async_mode:
            job_id = client.submit_async(task, sequence=sequence, sequence_name=sequence_name, model=model, options=options or None)
            print(f"[gi-{task}] submitted job_id={job_id}", file=sys.stderr)
            def _progress(p: Dict[str, Any]) -> None:
                pct = p.get("percent")
                msg = p.get("message", "")
                if pct is not None:
                    print(f"  {pct:>3}% {msg}", file=sys.stderr)
            body = client.wait_for_job(job_id, on_progress=_progress)
        else:
            body = client.predict(task, sequence=sequence, sequence_name=sequence_name, model=model, options=options or None)
    except GIError as e:
        print(f"[gi-{task}] API error: {e}", file=sys.stderr)
        return 2
    elapsed_ms = (time.monotonic() - started) * 1000.0
    summary = _summarize(task, body)
    _write_report(task, summary, body, args.output, input_path, sequence_name, len(sequence), elapsed_ms)
    print(f"[gi-{task}] OK — wrote {args.output}/report.md ({elapsed_ms:.0f} ms wall)", file=sys.stderr)
    return 0
