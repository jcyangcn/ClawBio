#!/usr/bin/env python3
"""
bigquery_public.py — BigQuery Public bridge for ClawBio
=======================================================
Run read-only SQL queries and lightweight discovery commands against BigQuery
public datasets with a local-first workflow. Demo mode uses a bundled offline
fixture so tests and first-run experience do not require cloud authentication.
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

from clawbio.common.report import (  # noqa: E402
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
SKILL_VERSION = "0.2.0"
DEFAULT_LOCATION = "US"
DEFAULT_MAX_ROWS = 100
DEFAULT_MAX_BYTES_BILLED = 1_000_000_000

DISCOVERY_LIST_DATASETS = "list-datasets"
DISCOVERY_LIST_TABLES = "list-tables"
DISCOVERY_DESCRIBE = "describe"


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


@dataclass
class DiscoveryRequest:
    action: str
    target: str


@dataclass
class RunPlan:
    mode: str
    query_source: str
    source_query: str | None
    effective_query: str | None
    discovery: DiscoveryRequest | None
    warnings: list[str]
    paper_reference: str | None
    notes: list[str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ClawBio BigQuery Public — SQL-first bridge for public datasets",
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
    parser.add_argument("--list-datasets", help="List datasets for a project (project)")
    parser.add_argument("--list-tables", help="List tables for a dataset (project.dataset)")
    parser.add_argument("--describe", help="Describe top-level schema for a table (project.dataset.table)")
    parser.add_argument("--preview", type=int, help="Wrap the SQL query in a preview LIMIT")
    parser.add_argument("--count-only", action="store_true", help="Return only the row count for the SQL query")
    parser.add_argument("--paper", help="Paper reference, DOI, URL, title, or local PDF path")
    parser.add_argument("--note", action="append", default=[], help="Repeatable provenance note")
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


def _analysis_sql(query: str) -> str:
    return re.sub(r"\s+", " ", _mask_sql_literals(_strip_sql_comments(query))).strip().upper()


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


def parse_project_dataset(spec: str) -> tuple[str, str]:
    parts = spec.strip().split(".")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Expected project.dataset, got: {spec!r}")
    return parts[0], parts[1]


def parse_project_dataset_table(spec: str) -> tuple[str, str, str]:
    parts = spec.strip().split(".")
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"Expected project.dataset.table, got: {spec!r}")
    return parts[0], parts[1], parts[2]


def _wrap_preview_query(query: str, preview_rows: int) -> str:
    return f"SELECT * FROM (\n{query}\n) AS clawbio_preview LIMIT {preview_rows}"


def _wrap_count_query(query: str) -> str:
    return f"SELECT COUNT(*) AS row_count FROM (\n{query}\n) AS clawbio_count"


def _has_select_star(query: str) -> bool:
    return bool(re.search(r"\bSELECT\s+(?:ALL\s+|DISTINCT\s+)?\*", _analysis_sql(query), flags=re.IGNORECASE))


def _has_limit_clause(query: str) -> bool:
    return bool(re.search(r"\bLIMIT\b", _analysis_sql(query), flags=re.IGNORECASE))


def collect_query_warnings(
    source_query: str,
    *,
    dry_run: bool,
    preview_rows: int | None,
    count_only: bool,
) -> list[str]:
    warnings: list[str] = []
    if _has_select_star(source_query):
        warnings.append("Query uses SELECT *; consider selecting only the columns you need.")
    if not dry_run and preview_rows is None and not count_only and not _has_limit_clause(source_query):
        warnings.append("Query does not include LIMIT; be sure the result size is intentional.")
    return warnings


def _resolve_run_plan(args: argparse.Namespace) -> RunPlan:
    discovery_options = [
        (DISCOVERY_LIST_DATASETS, args.list_datasets),
        (DISCOVERY_LIST_TABLES, args.list_tables),
        (DISCOVERY_DESCRIBE, args.describe),
    ]
    selected_discovery = [(action, target) for action, target in discovery_options if target]

    if len(selected_discovery) > 1:
        raise ValueError("Choose only one of --list-datasets, --list-tables, or --describe.")
    if args.preview is not None and args.preview <= 0:
        raise ValueError("--preview must be greater than 0.")
    if args.preview is not None and args.count_only:
        raise ValueError("--preview and --count-only cannot be used together.")

    if selected_discovery:
        if args.demo or args.query or args.input:
            raise ValueError("Discovery options are mutually exclusive with --query, --input, and --demo.")
        if args.dry_run:
            raise ValueError("--dry-run is only supported with --query or --input.")
        if args.preview is not None or args.count_only:
            raise ValueError("--preview and --count-only are only supported with --query or --input.")
        action, target = selected_discovery[0]
        if action == DISCOVERY_LIST_TABLES:
            parse_project_dataset(target)
        elif action == DISCOVERY_DESCRIBE:
            parse_project_dataset_table(target)
        return RunPlan(
            mode="discovery",
            query_source=f"discovery:{action}",
            source_query=None,
            effective_query=None,
            discovery=DiscoveryRequest(action=action, target=target),
            warnings=[],
            paper_reference=args.paper,
            notes=args.note or [],
        )

    query_text, query_source = _read_query_from_args(args)
    source_query = validate_read_only_sql(query_text)
    effective_query = source_query

    if args.demo and (args.preview is not None or args.count_only):
        raise ValueError("--preview and --count-only are not supported with --demo.")
    if args.count_only:
        effective_query = validate_read_only_sql(_wrap_count_query(source_query))
    elif args.preview is not None:
        effective_query = validate_read_only_sql(_wrap_preview_query(source_query, args.preview))

    warnings = [] if args.demo else collect_query_warnings(
        source_query,
        dry_run=args.dry_run,
        preview_rows=args.preview,
        count_only=args.count_only,
    )

    return RunPlan(
        mode="demo" if args.demo else "query",
        query_source=query_source,
        source_query=source_query,
        effective_query=effective_query,
        discovery=None,
        warnings=warnings,
        paper_reference=args.paper,
        notes=args.note or [],
    )


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


def _build_provenance_payload(plan: RunPlan, result: QueryExecutionResult) -> dict[str, Any]:
    payload = {
        "paper_reference": plan.paper_reference,
        "notes": plan.notes,
        "query_source": plan.query_source,
        "source_query": plan.source_query,
        "effective_query": plan.effective_query,
        "backend": result.backend,
        "project_id": result.project_id,
        "location": result.location,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    if plan.discovery:
        payload["discovery_action"] = plan.discovery.action
        payload["discovery_target"] = plan.discovery.target
    if plan.warnings:
        payload["warnings"] = plan.warnings
    return payload


def _default_query_sql(plan: RunPlan) -> str:
    if plan.effective_query:
        return plan.effective_query.rstrip() + "\n"
    if plan.discovery:
        return (
            "-- No SQL query executed.\n"
            f"-- Discovery action: {plan.discovery.action}\n"
            f"-- Discovery target: {plan.discovery.target}\n"
        )
    return "-- No SQL query executed.\n"


def _write_reproducibility_bundle(
    output_dir: Path,
    plan: RunPlan,
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
    (repro_dir / "query.sql").write_text(_default_query_sql(plan), encoding="utf-8")
    (repro_dir / "job_metadata.json").write_text(
        json.dumps(result.raw_metadata, indent=2, default=str),
        encoding="utf-8",
    )
    (repro_dir / "provenance.json").write_text(
        json.dumps(_build_provenance_payload(plan, result), indent=2, default=str),
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
    plan: RunPlan,
    result: QueryExecutionResult,
    parameters: list[QueryParameter],
    max_rows: int,
    max_bytes_billed: int | None,
) -> str:
    execution_mode = "Discovery" if plan.mode == "discovery" else ("Dry run" if result.dry_run else "Query")
    metadata = {
        "Execution mode": execution_mode,
        "Mode": plan.mode,
        "Backend": result.backend,
        "Project": result.project_id or "n/a",
        "Location": result.location,
        "Query source": plan.query_source,
        "Rows returned": str(result.row_count),
        "Estimated bytes processed": _format_int(result.estimated_bytes_processed),
        "Actual bytes processed": _format_int(result.total_bytes_processed),
        "Max rows": str(max_rows),
        "Max bytes billed": _format_int(max_bytes_billed),
    }
    if plan.discovery:
        metadata["Discovery action"] = plan.discovery.action
        metadata["Discovery target"] = plan.discovery.target

    lines = [generate_report_header("BigQuery Public Query Report", SKILL_NAME, extra_metadata=metadata)]
    lines.extend(
        [
            "## Summary",
            "",
            f"- Run mode: `{plan.mode}`",
            f"- Execution backend: `{result.backend}`",
            f"- Location: `{result.location}`",
            f"- Rows returned: `{result.row_count}`",
            f"- Estimated bytes processed: `{_format_int(result.estimated_bytes_processed)}`",
            f"- Actual bytes processed: `{_format_int(result.total_bytes_processed)}`",
            "",
        ]
    )

    if plan.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in plan.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    if plan.paper_reference or plan.notes:
        lines.append("## Provenance")
        lines.append("")
        if plan.paper_reference:
            lines.append(f"- Paper reference: `{plan.paper_reference}`")
        for note in plan.notes:
            lines.append(f"- Note: {note}")
        lines.append("")

    if plan.discovery:
        lines.extend(
            [
                "## Discovery Request",
                "",
                f"- Action: `{plan.discovery.action}`",
                f"- Target: `{plan.discovery.target}`",
                "",
            ]
        )
    elif plan.effective_query:
        if plan.source_query == plan.effective_query:
            lines.extend(
                [
                    "## Query",
                    "",
                    "```sql",
                    plan.effective_query,
                    "```",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "## Source Query",
                    "",
                    "```sql",
                    plan.source_query or "",
                    "```",
                    "",
                    "## Effective Query",
                    "",
                    "```sql",
                    plan.effective_query,
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

    raise ValueError("Provide --query, --input <sql_file>, --demo, or a discovery option.")


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
    return [
        bigquery_module.ScalarQueryParameter(param.name, param.type_name, param.value)
        for param in parameters
    ]


def _load_python_bigquery_client(project_id: str | None = None) -> tuple[Any, Any, str]:
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
    return client, bigquery, active_project


def _execute_with_python_client_once(
    query: str,
    location: str,
    max_rows: int,
    max_bytes_billed: int | None,
    parameters: list[QueryParameter],
    dry_run: bool,
    project_id: str | None,
) -> QueryExecutionResult:
    client, bigquery, active_project = _load_python_bigquery_client(project_id)

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


def _result_from_discovery_rows(
    *,
    backend: str,
    active_project: str,
    location: str,
    rows: list[dict[str, Any]],
    columns: list[str],
    raw_metadata: dict[str, Any],
) -> QueryExecutionResult:
    return QueryExecutionResult(
        backend=backend,
        project_id=active_project,
        location=location,
        query="",
        dry_run=False,
        rows=rows,
        columns=columns,
        estimated_bytes_processed=None,
        total_bytes_processed=None,
        row_count=len(rows),
        job_id=raw_metadata.get("job_id"),
        raw_metadata=raw_metadata,
    )


def execute_discovery_with_python_client(
    request: DiscoveryRequest,
    *,
    max_rows: int,
    location: str,
) -> QueryExecutionResult:
    client, bigquery, active_project = _load_python_bigquery_client()
    try:
        if request.action == DISCOVERY_LIST_DATASETS:
            target_project = request.target
            rows = []
            for dataset in client.list_datasets(project=target_project, max_results=max_rows):
                rows.append(
                    {
                        "project_id": target_project,
                        "dataset_id": dataset.dataset_id,
                        "location": getattr(dataset, "location", "") or "",
                    }
                )
            result_location = rows[0]["location"] if rows and len({row["location"] for row in rows if row["location"]}) == 1 else location
            raw_metadata = {
                "backend": "python-adc",
                "project_id": active_project,
                "location": result_location,
                "discovery_action": request.action,
                "discovery_target": request.target,
                "target_project": target_project,
            }
            return _result_from_discovery_rows(
                backend="python-adc",
                active_project=active_project,
                location=result_location,
                rows=rows,
                columns=["project_id", "dataset_id", "location"],
                raw_metadata=raw_metadata,
            )

        if request.action == DISCOVERY_LIST_TABLES:
            target_project, dataset_id = parse_project_dataset(request.target)
            dataset_ref = bigquery.DatasetReference(target_project, dataset_id)
            dataset = client.get_dataset(dataset_ref)
            rows = []
            for table in client.list_tables(dataset_ref, max_results=max_rows):
                rows.append(
                    {
                        "project_id": target_project,
                        "dataset_id": dataset_id,
                        "table_id": table.table_id,
                        "table_type": getattr(table, "table_type", "") or "",
                    }
                )
            raw_metadata = {
                "backend": "python-adc",
                "project_id": active_project,
                "location": getattr(dataset, "location", None) or location,
                "discovery_action": request.action,
                "discovery_target": request.target,
                "target_project": target_project,
                "target_dataset": dataset_id,
            }
            return _result_from_discovery_rows(
                backend="python-adc",
                active_project=active_project,
                location=raw_metadata["location"],
                rows=rows,
                columns=["project_id", "dataset_id", "table_id", "table_type"],
                raw_metadata=raw_metadata,
            )

        if request.action == DISCOVERY_DESCRIBE:
            target_project, dataset_id, table_id = parse_project_dataset_table(request.target)
            table_ref = bigquery.TableReference(bigquery.DatasetReference(target_project, dataset_id), table_id)
            table = client.get_table(table_ref)
            rows = [
                {
                    "field_name": field.name,
                    "field_type": field.field_type,
                    "mode": field.mode,
                    "description": field.description or "",
                }
                for field in table.schema
            ]
            raw_metadata = {
                "backend": "python-adc",
                "project_id": active_project,
                "location": getattr(table, "location", None) or location,
                "discovery_action": request.action,
                "discovery_target": request.target,
                "target_project": target_project,
                "target_dataset": dataset_id,
                "target_table": table_id,
                "table_type": getattr(table, "table_type", None),
            }
            return _result_from_discovery_rows(
                backend="python-adc",
                active_project=active_project,
                location=raw_metadata["location"],
                rows=rows,
                columns=["field_name", "field_type", "mode", "description"],
                raw_metadata=raw_metadata,
            )
    except Exception as exc:
        raise BigQuerySetupError(f"Python BigQuery discovery failed: {exc}") from exc

    raise ValueError(f"Unsupported discovery action: {request.action}")


def _run_bq_cli_json(cmd: list[str]) -> Any:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "Unknown bq CLI error."
        raise BigQuerySetupError(f"bq CLI discovery failed: {detail}")
    parsed = _try_parse_json(proc.stdout)
    return parsed if parsed is not None else proc.stdout.strip()


def execute_discovery_with_bq_cli(
    request: DiscoveryRequest,
    *,
    max_rows: int,
    location: str,
) -> QueryExecutionResult:
    if not shutil.which("bq"):
        raise BigQuerySetupError("bq CLI is not installed.")

    active_project = _get_gcloud_project()
    if not active_project:
        raise BigQuerySetupError("No Google Cloud project configured for the bq CLI.")

    if request.action == DISCOVERY_LIST_DATASETS:
        payload = _run_bq_cli_json(
            [
                "bq",
                f"--project_id={active_project}",
                "ls",
                "--datasets",
                "--format=prettyjson",
                request.target,
            ]
        )
        items = payload if isinstance(payload, list) else []
        rows = [
            {
                "project_id": item.get("datasetReference", {}).get("projectId", request.target),
                "dataset_id": item.get("datasetReference", {}).get("datasetId", ""),
                "location": item.get("location", "") or "",
            }
            for item in items[:max_rows]
        ]
        unique_locations = {row["location"] for row in rows if row["location"]}
        result_location = rows[0]["location"] if len(unique_locations) == 1 and rows else location
        raw_metadata = {
            "backend": "bq-cli",
            "project_id": active_project,
            "location": result_location,
            "discovery_action": request.action,
            "discovery_target": request.target,
            "raw_response": payload,
        }
        return _result_from_discovery_rows(
            backend="bq-cli",
            active_project=active_project,
            location=result_location,
            rows=rows,
            columns=["project_id", "dataset_id", "location"],
            raw_metadata=raw_metadata,
        )

    if request.action == DISCOVERY_LIST_TABLES:
        target_project, dataset_id = parse_project_dataset(request.target)
        dataset_meta = _run_bq_cli_json(
            [
                "bq",
                f"--project_id={active_project}",
                "show",
                "--format=prettyjson",
                f"{target_project}:{dataset_id}",
            ]
        )
        payload = _run_bq_cli_json(
            [
                "bq",
                f"--project_id={active_project}",
                "ls",
                "--format=prettyjson",
                f"{target_project}:{dataset_id}",
            ]
        )
        items = payload if isinstance(payload, list) else []
        rows = [
            {
                "project_id": item.get("tableReference", {}).get("projectId", target_project),
                "dataset_id": item.get("tableReference", {}).get("datasetId", dataset_id),
                "table_id": item.get("tableReference", {}).get("tableId", ""),
                "table_type": item.get("type", "") or "",
            }
            for item in items[:max_rows]
        ]
        raw_metadata = {
            "backend": "bq-cli",
            "project_id": active_project,
            "location": dataset_meta.get("location", location) if isinstance(dataset_meta, dict) else location,
            "discovery_action": request.action,
            "discovery_target": request.target,
            "raw_response": payload,
        }
        return _result_from_discovery_rows(
            backend="bq-cli",
            active_project=active_project,
            location=raw_metadata["location"],
            rows=rows,
            columns=["project_id", "dataset_id", "table_id", "table_type"],
            raw_metadata=raw_metadata,
        )

    if request.action == DISCOVERY_DESCRIBE:
        target_project, dataset_id, table_id = parse_project_dataset_table(request.target)
        payload = _run_bq_cli_json(
            [
                "bq",
                f"--project_id={active_project}",
                "show",
                "--format=prettyjson",
                f"{target_project}:{dataset_id}.{table_id}",
            ]
        )
        schema_fields = payload.get("schema", {}).get("fields", []) if isinstance(payload, dict) else []
        rows = [
            {
                "field_name": field.get("name", ""),
                "field_type": field.get("type", ""),
                "mode": field.get("mode", ""),
                "description": field.get("description", "") or "",
            }
            for field in schema_fields
        ]
        raw_metadata = {
            "backend": "bq-cli",
            "project_id": active_project,
            "location": payload.get("location", location) if isinstance(payload, dict) else location,
            "discovery_action": request.action,
            "discovery_target": request.target,
            "raw_response": payload,
        }
        return _result_from_discovery_rows(
            backend="bq-cli",
            active_project=active_project,
            location=raw_metadata["location"],
            rows=rows,
            columns=["field_name", "field_type", "mode", "description"],
            raw_metadata=raw_metadata,
        )

    raise ValueError(f"Unsupported discovery action: {request.action}")


def execute_discovery(
    request: DiscoveryRequest,
    *,
    max_rows: int,
    location: str,
) -> QueryExecutionResult:
    failures: list[str] = []
    try:
        return execute_discovery_with_python_client(request, max_rows=max_rows, location=location)
    except BigQuerySetupError as exc:
        failures.append(f"Python ADC: {exc}")

    try:
        return execute_discovery_with_bq_cli(request, max_rows=max_rows, location=location)
    except BigQuerySetupError as exc:
        failures.append(f"bq CLI: {exc}")

    raise BigQuerySetupError(_auth_setup_message(failures, _get_gcloud_project()))


def run_plan(
    plan: RunPlan,
    output_dir: Path,
    *,
    parameters: list[QueryParameter],
    location: str,
    max_rows: int,
    max_bytes_billed: int | None,
    dry_run: bool,
) -> QueryExecutionResult:
    _ensure_output_dir_ready(output_dir)

    if plan.mode == "demo":
        result = _load_demo_result(
            query=plan.effective_query or "",
            location=location,
            max_rows=max_rows,
            dry_run=dry_run,
        )
    elif plan.mode == "discovery":
        assert plan.discovery is not None
        result = execute_discovery(plan.discovery, max_rows=max_rows, location=location)
    else:
        assert plan.effective_query is not None
        result = execute_query(
            query=plan.effective_query,
            location=location,
            max_rows=max_rows,
            max_bytes_billed=max_bytes_billed,
            parameters=parameters,
            dry_run=dry_run,
        )

    report_text = build_report(
        plan=plan,
        result=result,
        parameters=parameters,
        max_rows=max_rows,
        max_bytes_billed=max_bytes_billed,
    )
    (output_dir / "report.md").write_text(report_text, encoding="utf-8")
    _write_results_csv(output_dir / "tables" / "results.csv", result.rows, result.columns)
    _write_reproducibility_bundle(output_dir, plan, result)

    summary = {
        "mode": plan.mode,
        "dry_run": result.dry_run,
        "backend": result.backend,
        "project_id": result.project_id,
        "location": result.location,
        "row_count": result.row_count,
        "max_rows": max_rows,
        "estimated_bytes_processed": result.estimated_bytes_processed,
        "total_bytes_processed": result.total_bytes_processed,
        "query_source": plan.query_source,
    }
    if plan.discovery:
        summary["discovery_action"] = plan.discovery.action
        summary["discovery_target"] = plan.discovery.target

    data = {
        "query": plan.effective_query or "",
        "source_query": plan.source_query,
        "effective_query": plan.effective_query,
        "columns": result.columns,
        "rows": result.rows,
        "parameters": [
            {"name": param.name, "type": param.type_name, "value": param.original}
            for param in parameters
        ],
        "paper_reference": plan.paper_reference,
        "notes": plan.notes,
        "warnings": plan.warnings,
        "job_metadata": result.raw_metadata,
        "disclaimer": DISCLAIMER,
    }
    if plan.discovery:
        data["discovery"] = {
            "action": plan.discovery.action,
            "target": plan.discovery.target,
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
        plan = _resolve_run_plan(args)
        params = parse_scalar_params(args.param) if plan.mode == "query" else []
        output_dir = Path(args.output)
        result = run_plan(
            plan=plan,
            output_dir=output_dir,
            parameters=params,
            location=args.location,
            max_rows=args.max_rows,
            max_bytes_billed=args.max_bytes_billed,
            dry_run=args.dry_run,
        )
    except (BigQuerySetupError, QueryValidationError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for warning in plan.warnings:
        print(f"WARNING: {warning}")
    print(f"Report written to {output_dir / 'report.md'}")
    print(f"Rows returned: {result.row_count}")
    print(f"Backend: {result.backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
