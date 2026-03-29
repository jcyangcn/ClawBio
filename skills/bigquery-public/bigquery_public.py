#!/usr/bin/env python3
"""
bigquery_public.py — BigQuery Public Data bridge for ClawBio
============================================================
Run read-only SQL queries against BigQuery public datasets with a local-first
workflow. Demo mode uses a bundled offline fixture so tests and first-run
experience do not require cloud authentication.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from clawbio.common.report import (
    DISCLAIMER,
    generate_report_footer,
    generate_report_header,
    write_result_json,
)

SKILL_DIR = Path(__file__).resolve().parent
DEMO_DIR = SKILL_DIR / "demo"
DEMO_QUERY_PATH = DEMO_DIR / "demo_query.sql"
DEMO_RESULT_PATH = DEMO_DIR / "demo_result.json"

SKILL_NAME = "bigquery-public"
SKILL_VERSION = "0.1.0"
DEFAULT_LOCATION = "US"
DEFAULT_MAX_ROWS = 100
DEFAULT_MAX_BYTES_BILLED = 1_000_000_000


class BigQuerySetupError(RuntimeError):
    """Raised when BigQuery access is unavailable or misconfigured."""


class QueryValidationError(ValueError):
    """Raised when the SQL is unsafe or unsupported."""


@dataclass
class QueryParameter:
    name: str
    type_name: str
    value: Any
    original: str

    def to_cli_spec(self) -> str:
        return f"{self.name}:{self.type_name}:{self.original}"


@dataclass
class QueryExecutionResult:
    backend: str
    project_id: str | None
    location: str
    query: str
    dry_run: bool
    rows: list[dict[str, Any]]
    columns: list[str]
    estimated_bytes_processed: int | None
    total_bytes_processed: int | None
    row_count: int
    job_id: str | None
    raw_metadata: dict[str, Any]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ClawBio BigQuery Public — read-only SQL bridge for public datasets",
    )
    parser.add_argument("--input", help="Path to a SQL file")
    parser.add_argument("--query", help="Inline SQL query string")
    parser.add_argument("--output", required=True, help="Directory to write outputs")
    parser.add_argument("--demo", action="store_true", help="Run offline demo using bundled fixture data")
    parser.add_argument("--dry-run", action="store_true", help="Estimate bytes only; do not execute the query")
    parser.add_argument("--location", default=DEFAULT_LOCATION, help=f"BigQuery location (default: {DEFAULT_LOCATION})")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS, help=f"Maximum rows to return (default: {DEFAULT_MAX_ROWS})")
    parser.add_argument(
        "--max-bytes-billed",
        type=int,
        default=DEFAULT_MAX_BYTES_BILLED,
        help=f"Maximum billed bytes safeguard (default: {DEFAULT_MAX_BYTES_BILLED})",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="Scalar query parameter in name=type:value format (repeatable)",
    )
    return parser


def _get_gcloud_project() -> str | None:
    if not shutil.which("gcloud"):
        return None
    proc = subprocess.run(
        ["gcloud", "config", "get-value", "project"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    if not value or value == "(unset)":
        return None
    return value


def _auth_setup_message(reasons: list[str], project_id: str | None) -> str:
    lines = [
        "BigQuery authentication is not available for this run.",
        "",
        "Backends tried:",
    ]
    lines.extend(f"- {reason}" for reason in reasons)
    lines.extend(
        [
            "",
            "Suggested setup:",
            "1. gcloud auth login",
            "2. gcloud auth application-default login",
            f"3. gcloud config set project {project_id or 'YOUR_PROJECT_ID'}",
            "4. Re-run the command, or set GOOGLE_APPLICATION_CREDENTIALS for service-account based access.",
        ]
    )
    return "\n".join(lines)


def _mask_sql_literals(text: str) -> str:
    patterns = [
        r"'(?:''|[^'])*'",
        r'"(?:\\"|[^"])*"',
        r"`(?:``|[^`])*`",
    ]
    masked = text
    for pattern in patterns:
        masked = re.sub(pattern, lambda m: " " * len(m.group(0)), masked, flags=re.DOTALL)
    return masked


def _strip_sql_comments(text: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"--.*?$", " ", no_block, flags=re.MULTILINE)


def validate_read_only_sql(query: str) -> str:
    cleaned = query.strip()
    if not cleaned:
        raise QueryValidationError("Query is empty.")

    masked = _mask_sql_literals(_strip_sql_comments(cleaned))
    masked_stripped = masked.strip()
    leading = masked_stripped.upper()
    if not (leading.startswith("SELECT") or leading.startswith("WITH")):
        raise QueryValidationError("Only read-only SELECT/WITH queries are supported.")

    if ";" in masked_stripped[:-1]:
        raise QueryValidationError("Multiple SQL statements are not supported.")
    if masked_stripped.endswith(";"):
        cleaned = cleaned.rstrip()
        cleaned = cleaned[:-1].rstrip()

    forbidden = re.compile(
        r"\b(INSERT|UPDATE|DELETE|CREATE|MERGE|EXPORT\s+DATA|DROP|ALTER|TRUNCATE|CALL|DECLARE|SET)\b",
        flags=re.IGNORECASE,
    )
    match = forbidden.search(masked)
    if match:
        raise QueryValidationError(f"Unsupported SQL keyword detected: {match.group(0).strip()}")

    return cleaned


def parse_scalar_param(spec: str) -> QueryParameter:
    if "=" not in spec or ":" not in spec.split("=", 1)[1]:
        raise ValueError(f"Invalid --param value: {spec!r}. Expected name=type:value")

    name, typed_value = spec.split("=", 1)
    type_name, raw_value = typed_value.split(":", 1)
    name = name.strip()
    type_name = type_name.strip().upper()
    raw_value = raw_value.strip()

    if not name:
        raise ValueError(f"Invalid --param value: {spec!r}. Parameter name is empty.")

    if type_name in {"STRING", "DATE", "DATETIME", "TIMESTAMP"}:
        value: Any = raw_value
    elif type_name in {"INT64", "INTEGER"}:
        value = int(raw_value)
        type_name = "INT64"
    elif type_name in {"FLOAT64", "FLOAT", "NUMERIC"}:
        value = float(raw_value)
        type_name = "FLOAT64" if type_name != "NUMERIC" else "NUMERIC"
    elif type_name in {"BOOL", "BOOLEAN"}:
        lowered = raw_value.lower()
        if lowered not in {"true", "false"}:
            raise ValueError(f"Invalid boolean parameter value: {raw_value!r}")
        value = lowered == "true"
        type_name = "BOOL"
    else:
        raise ValueError(
            f"Unsupported parameter type {type_name!r}. "
            "Supported types: STRING, INT64, FLOAT64, BOOL, DATE, DATETIME, TIMESTAMP, NUMERIC."
        )

    return QueryParameter(name=name, type_name=type_name, value=value, original=raw_value)


def parse_scalar_params(specs: list[str]) -> list[QueryParameter]:
    return [parse_scalar_param(spec) for spec in specs]


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_value(val) for key, val in value.items()}
    return str(value)


def _infer_columns(rows: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns


def _write_results_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not columns:
            return
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    col: json.dumps(_json_safe_value(row.get(col)), ensure_ascii=False)
                    if isinstance(row.get(col), (list, dict))
                    else _json_safe_value(row.get(col))
                    for col in columns
                }
            )


def _write_reproducibility_bundle(
    output_dir: Path,
    query: str,
    result: QueryExecutionResult,
) -> None:
    repro_dir = output_dir / "reproducibility"
    repro_dir.mkdir(parents=True, exist_ok=True)

    command_text = (
        "#!/usr/bin/env bash\n"
        f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"# Skill: {SKILL_NAME}\n\n"
        + " ".join(shlex.quote(arg) for arg in sys.argv)
        + "\n"
    )
    (repro_dir / "commands.sh").write_text(command_text, encoding="utf-8")
    (repro_dir / "query.sql").write_text(query.rstrip() + "\n", encoding="utf-8")
    (repro_dir / "job_metadata.json").write_text(
        json.dumps(result.raw_metadata, indent=2, default=str),
        encoding="utf-8",
    )
    (repro_dir / "environment.yml").write_text(
        "\n".join(
            [
                "name: clawbio-bigquery-public",
                "channels:",
                "  - conda-forge",
                "  - defaults",
                "dependencies:",
                "  - python>=3.10",
                "  - pip",
                "  - pip:",
                "    - google-auth",
                "    - google-cloud-bigquery",
                f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _render_markdown_table(rows: list[dict[str, Any]], columns: list[str], limit: int = 10) -> str:
    if not rows or not columns:
        return "_No rows returned._"

    visible = rows[:limit]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in visible:
        values = []
        for col in columns:
            cell = row.get(col, "")
            if isinstance(cell, (dict, list)):
                text = json.dumps(cell, ensure_ascii=False)
            else:
                text = str(cell)
            values.append(text.replace("\n", " ").replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _format_int(value: int | None) -> str:
    return f"{value:,}" if value is not None else "n/a"


def build_report(
    query: str,
    result: QueryExecutionResult,
    query_source: str,
    parameters: list[QueryParameter],
    max_rows: int,
    max_bytes_billed: int | None,
) -> str:
    metadata = {
        "Execution mode": "Dry run" if result.dry_run else "Query",
        "Backend": result.backend,
        "Project": result.project_id or "n/a",
        "Location": result.location,
        "Query source": query_source,
        "Rows returned": str(result.row_count),
        "Estimated bytes processed": _format_int(result.estimated_bytes_processed),
        "Actual bytes processed": _format_int(result.total_bytes_processed),
        "Max rows": str(max_rows),
        "Max bytes billed": _format_int(max_bytes_billed),
    }

    lines = [generate_report_header("BigQuery Public Query Report", SKILL_NAME, extra_metadata=metadata)]
    lines.extend(
        [
            "## Summary",
            "",
            f"- Execution backend: `{result.backend}`",
            f"- Location: `{result.location}`",
            f"- Rows returned: `{result.row_count}`",
            f"- Estimated bytes processed: `{_format_int(result.estimated_bytes_processed)}`",
            f"- Actual bytes processed: `{_format_int(result.total_bytes_processed)}`",
            "",
            "## Query",
            "",
            "```sql",
            query,
            "```",
            "",
        ]
    )

    if parameters:
        lines.append("## Parameters")
        lines.append("")
        for param in parameters:
            lines.append(f"- `{param.name}` ({param.type_name}) = `{param.original}`")
        lines.append("")

    lines.extend(
        [
            "## Results Preview",
            "",
            _render_markdown_table(result.rows, result.columns),
            "",
        ]
    )

    if result.job_id:
        lines.extend(["## Job Metadata", "", f"- Job ID: `{result.job_id}`", ""])

    lines.append(generate_report_footer())
    return "\n".join(lines).strip() + "\n"


def _ensure_output_dir_ready(output_dir: Path) -> None:
    if output_dir.exists():
        if any(output_dir.iterdir()):
            raise ValueError(
                f"Output directory already exists and is not empty: {output_dir}. "
                "Choose a new directory to avoid overwriting previous results."
            )
        return
    output_dir.mkdir(parents=True, exist_ok=True)


def _read_query_from_args(args: argparse.Namespace) -> tuple[str, str]:
    if args.demo:
        return DEMO_QUERY_PATH.read_text(encoding="utf-8"), "demo-query"

    if args.query:
        if args.input:
            print("WARNING: --query provided; ignoring --input SQL file.", file=sys.stderr)
        return args.query, "inline-query"

    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            raise FileNotFoundError(f"SQL file not found: {input_path}")
        return input_path.read_text(encoding="utf-8"), str(input_path)

    raise ValueError("Provide --query, --input <sql_file>, or --demo.")


def _load_demo_result(query: str, location: str, max_rows: int, dry_run: bool) -> QueryExecutionResult:
    payload = json.loads(DEMO_RESULT_PATH.read_text(encoding="utf-8"))
    rows = payload["rows"][:max_rows] if not dry_run else []
    columns = payload.get("columns") or _infer_columns(rows)
    bytes_processed = payload.get("total_bytes_processed")
    raw_metadata = {
        "backend": "demo-fixture",
        "project_id": payload.get("project_id"),
        "location": location,
        "job_id": payload.get("job_id"),
        "estimated_bytes_processed": bytes_processed,
        "total_bytes_processed": None if dry_run else bytes_processed,
        "demo_source": str(DEMO_RESULT_PATH),
    }
    return QueryExecutionResult(
        backend="demo-fixture",
        project_id=payload.get("project_id"),
        location=location,
        query=query,
        dry_run=dry_run,
        rows=rows,
        columns=columns,
        estimated_bytes_processed=bytes_processed,
        total_bytes_processed=None if dry_run else bytes_processed,
        row_count=len(rows),
        job_id=payload.get("job_id"),
        raw_metadata=raw_metadata,
    )


def _extract_named_value(payload: Any, target_key: str) -> Any:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() == target_key.lower():
                return value
            nested = _extract_named_value(value, target_key)
            if nested is not None:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = _extract_named_value(item, target_key)
            if nested is not None:
                return nested
    return None


def _try_parse_json(text: str) -> Any:
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _normalize_bq_cli_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [_json_safe_value(row) for row in payload if isinstance(row, dict)]

    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        schema_fields = payload.get("schema", {}).get("fields", [])
        field_names = [field.get("name", f"col_{idx}") for idx, field in enumerate(schema_fields)]
        rows: list[dict[str, Any]] = []
        for row in payload["rows"]:
            cells = row.get("f", [])
            row_data = {}
            for idx, cell in enumerate(cells):
                if idx >= len(field_names):
                    continue
                row_data[field_names[idx]] = _json_safe_value(cell.get("v"))
            rows.append(row_data)
        return rows

    return []


def _build_python_query_parameters(parameters: list[QueryParameter], bigquery_module: Any) -> list[Any]:
    built = []
    for param in parameters:
        built.append(bigquery_module.ScalarQueryParameter(param.name, param.type_name, param.value))
    return built


def _execute_with_python_client_once(
    query: str,
    location: str,
    max_rows: int,
    max_bytes_billed: int | None,
    parameters: list[QueryParameter],
    dry_run: bool,
    project_id: str | None,
) -> QueryExecutionResult:
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError
        from google.cloud import bigquery
    except ImportError as exc:
        raise BigQuerySetupError(f"Python BigQuery client unavailable: {exc}") from exc

    try:
        credentials, default_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except DefaultCredentialsError as exc:
        raise BigQuerySetupError(f"ADC unavailable: {exc}") from exc

    active_project = project_id or default_project or _get_gcloud_project()
    if not active_project:
        raise BigQuerySetupError("No Google Cloud project configured for the Python client.")

    client = bigquery.Client(project=active_project, credentials=credentials)
    job_config = bigquery.QueryJobConfig(
        dry_run=dry_run,
        use_legacy_sql=False,
        maximum_bytes_billed=max_bytes_billed,
        query_parameters=_build_python_query_parameters(parameters, bigquery),
    )

    try:
        query_job = client.query(query, location=location, job_config=job_config)
    except Exception as exc:
        raise BigQuerySetupError(f"Python BigQuery query failed: {exc}") from exc

    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    if not dry_run:
        try:
            iterator = query_job.result(max_results=max_rows)
        except Exception as exc:
            raise BigQuerySetupError(f"Python BigQuery result fetch failed: {exc}") from exc
        columns = [field.name for field in getattr(iterator, "schema", [])]
        for row in iterator:
            rows.append({key: _json_safe_value(value) for key, value in dict(row).items()})

    total_bytes = getattr(query_job, "total_bytes_processed", None)
    raw_metadata = {
        "backend": "python-adc",
        "project_id": active_project,
        "location": location,
        "job_id": getattr(query_job, "job_id", None),
        "state": getattr(query_job, "state", None),
        "total_bytes_processed": total_bytes,
        "cache_hit": getattr(query_job, "cache_hit", None),
    }
    return QueryExecutionResult(
        backend="python-adc",
        project_id=active_project,
        location=location,
        query=query,
        dry_run=dry_run,
        rows=rows,
        columns=columns or _infer_columns(rows),
        estimated_bytes_processed=total_bytes if dry_run else None,
        total_bytes_processed=None if dry_run else total_bytes,
        row_count=len(rows),
        job_id=getattr(query_job, "job_id", None),
        raw_metadata=raw_metadata,
    )


def execute_with_python_client(
    query: str,
    location: str,
    max_rows: int,
    max_bytes_billed: int | None,
    parameters: list[QueryParameter],
    dry_run: bool,
    project_id: str | None = None,
) -> QueryExecutionResult:
    if dry_run:
        return _execute_with_python_client_once(
            query=query,
            location=location,
            max_rows=max_rows,
            max_bytes_billed=max_bytes_billed,
            parameters=parameters,
            dry_run=True,
            project_id=project_id,
        )

    estimate = _execute_with_python_client_once(
        query=query,
        location=location,
        max_rows=max_rows,
        max_bytes_billed=max_bytes_billed,
        parameters=parameters,
        dry_run=True,
        project_id=project_id,
    )
    actual = _execute_with_python_client_once(
        query=query,
        location=location,
        max_rows=max_rows,
        max_bytes_billed=max_bytes_billed,
        parameters=parameters,
        dry_run=False,
        project_id=estimate.project_id,
    )
    actual.estimated_bytes_processed = estimate.estimated_bytes_processed or estimate.total_bytes_processed
    return actual


def _execute_with_bq_cli_once(
    query: str,
    location: str,
    max_rows: int,
    max_bytes_billed: int | None,
    parameters: list[QueryParameter],
    dry_run: bool,
    project_id: str | None,
) -> QueryExecutionResult:
    if not shutil.which("bq"):
        raise BigQuerySetupError("bq CLI is not installed.")

    active_project = project_id or _get_gcloud_project()
    if not active_project:
        raise BigQuerySetupError("No Google Cloud project configured for the bq CLI.")

    cmd = [
        "bq",
        f"--project_id={active_project}",
        f"--location={location}",
        "query",
        "--use_legacy_sql=false",
        "--format=prettyjson" if dry_run else "--format=json",
        f"--max_rows={max_rows}",
    ]
    if dry_run:
        cmd.append("--dry_run")
    if max_bytes_billed is not None:
        cmd.append(f"--maximum_bytes_billed={max_bytes_billed}")
    for param in parameters:
        cmd.append(f"--parameter={param.to_cli_spec()}")
    cmd.append(query)

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "Unknown bq CLI error."
        raise BigQuerySetupError(f"bq CLI query failed: {detail}")

    parsed = _try_parse_json(proc.stdout)
    rows = [] if dry_run else _normalize_bq_cli_rows(parsed)
    columns = _infer_columns(rows)
    bytes_processed = _extract_named_value(parsed, "totalBytesProcessed")
    try:
        bytes_processed = int(bytes_processed) if bytes_processed is not None else None
    except (TypeError, ValueError):
        bytes_processed = None
    job_id = _extract_named_value(parsed, "jobId")
    raw_metadata = {
        "backend": "bq-cli",
        "project_id": active_project,
        "location": location,
        "job_id": job_id,
        "total_bytes_processed": bytes_processed,
        "raw_response": parsed if parsed is not None else proc.stdout.strip(),
    }
    return QueryExecutionResult(
        backend="bq-cli",
        project_id=active_project,
        location=location,
        query=query,
        dry_run=dry_run,
        rows=rows,
        columns=columns,
        estimated_bytes_processed=bytes_processed if dry_run else None,
        total_bytes_processed=None if dry_run else bytes_processed,
        row_count=len(rows),
        job_id=str(job_id) if job_id is not None else None,
        raw_metadata=raw_metadata,
    )


def execute_with_bq_cli(
    query: str,
    location: str,
    max_rows: int,
    max_bytes_billed: int | None,
    parameters: list[QueryParameter],
    dry_run: bool,
    project_id: str | None = None,
) -> QueryExecutionResult:
    if dry_run:
        return _execute_with_bq_cli_once(
            query=query,
            location=location,
            max_rows=max_rows,
            max_bytes_billed=max_bytes_billed,
            parameters=parameters,
            dry_run=True,
            project_id=project_id,
        )

    estimate = _execute_with_bq_cli_once(
        query=query,
        location=location,
        max_rows=max_rows,
        max_bytes_billed=max_bytes_billed,
        parameters=parameters,
        dry_run=True,
        project_id=project_id,
    )
    actual = _execute_with_bq_cli_once(
        query=query,
        location=location,
        max_rows=max_rows,
        max_bytes_billed=max_bytes_billed,
        parameters=parameters,
        dry_run=False,
        project_id=estimate.project_id,
    )
    actual.estimated_bytes_processed = estimate.estimated_bytes_processed or estimate.total_bytes_processed
    return actual


def execute_query(
    query: str,
    location: str,
    max_rows: int,
    max_bytes_billed: int | None,
    parameters: list[QueryParameter],
    dry_run: bool,
) -> QueryExecutionResult:
    failures: list[str] = []
    try:
        return execute_with_python_client(
            query=query,
            location=location,
            max_rows=max_rows,
            max_bytes_billed=max_bytes_billed,
            parameters=parameters,
            dry_run=dry_run,
        )
    except BigQuerySetupError as exc:
        failures.append(f"Python ADC: {exc}")

    try:
        return execute_with_bq_cli(
            query=query,
            location=location,
            max_rows=max_rows,
            max_bytes_billed=max_bytes_billed,
            parameters=parameters,
            dry_run=dry_run,
        )
    except BigQuerySetupError as exc:
        failures.append(f"bq CLI: {exc}")

    raise BigQuerySetupError(_auth_setup_message(failures, _get_gcloud_project()))


def run_query(
    query: str,
    output_dir: Path,
    *,
    query_source: str,
    parameters: list[QueryParameter],
    location: str,
    max_rows: int,
    max_bytes_billed: int | None,
    dry_run: bool,
    demo: bool,
) -> QueryExecutionResult:
    validated_query = validate_read_only_sql(query)
    _ensure_output_dir_ready(output_dir)

    if demo:
        result = _load_demo_result(
            query=validated_query,
            location=location,
            max_rows=max_rows,
            dry_run=dry_run,
        )
    else:
        result = execute_query(
            query=validated_query,
            location=location,
            max_rows=max_rows,
            max_bytes_billed=max_bytes_billed,
            parameters=parameters,
            dry_run=dry_run,
        )

    report_text = build_report(
        query=validated_query,
        result=result,
        query_source=query_source,
        parameters=parameters,
        max_rows=max_rows,
        max_bytes_billed=max_bytes_billed,
    )
    (output_dir / "report.md").write_text(report_text, encoding="utf-8")
    _write_results_csv(output_dir / "tables" / "results.csv", result.rows, result.columns)
    _write_reproducibility_bundle(output_dir, validated_query, result)

    summary = {
        "mode": "demo" if demo else "query",
        "dry_run": dry_run,
        "backend": result.backend,
        "project_id": result.project_id,
        "location": result.location,
        "row_count": result.row_count,
        "max_rows": max_rows,
        "estimated_bytes_processed": result.estimated_bytes_processed,
        "total_bytes_processed": result.total_bytes_processed,
        "query_source": query_source,
    }
    data = {
        "query": validated_query,
        "columns": result.columns,
        "rows": result.rows,
        "parameters": [
            {"name": param.name, "type": param.type_name, "value": param.original}
            for param in parameters
        ],
        "job_metadata": result.raw_metadata,
        "disclaimer": DISCLAIMER,
    }
    write_result_json(
        output_dir=output_dir,
        skill=SKILL_NAME,
        version=SKILL_VERSION,
        summary=summary,
        data=data,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.max_rows <= 0:
        parser.error("--max-rows must be greater than 0")
    if args.max_bytes_billed is not None and args.max_bytes_billed <= 0:
        parser.error("--max-bytes-billed must be greater than 0")

    try:
        query_text, query_source = _read_query_from_args(args)
        params = parse_scalar_params(args.param)
        output_dir = Path(args.output)
        result = run_query(
            query=query_text,
            output_dir=output_dir,
            query_source=query_source,
            parameters=params,
            location=args.location,
            max_rows=args.max_rows,
            max_bytes_billed=args.max_bytes_billed,
            dry_run=args.dry_run,
            demo=args.demo,
        )
    except (BigQuerySetupError, QueryValidationError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Report written to {output_dir / 'report.md'}")
    print(f"Rows returned: {result.row_count}")
    print(f"Backend: {result.backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
