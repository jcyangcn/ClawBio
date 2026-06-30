# Configuration & runtime options

polars-bio is configured via `pb.set_option(key, value)` / `pb.get_option(key)`, plus
logging and metadata helpers.

## Common options

| Option | Default | Purpose |
|--------|---------|---------|
| `datafusion.execution.target_partitions` | 1 | parallelism; set to `os.cpu_count()` for large data |
| `datafusion.bio.coordinate_system_zero_based` | False (1-based) | global coordinate fallback |
| `datafusion.bio.coordinate_system_check` | False | raise on missing coordinate metadata |

```python
import os, polars_bio as pb
pb.set_option("datafusion.execution.target_partitions", os.cpu_count())
```

## Coordinate systems

- Default is **1-based closed** (matches VCF/GFF/SAM). BED files are **0-based half-open**
  in the file; polars-bio converts on read (corrected in #413).
- I/O functions accept `use_zero_based=True/False` to set coordinate metadata; the
  ClawBio CLI exposes `--zero-based`.
- Mismatched systems between two inputs raise `CoordinateSystemMismatchError`; missing
  metadata (with `coordinate_system_check=True`) raises `MissingCoordinateSystemError`.

## Metadata utilities

`get_metadata`, `set_source_metadata`, `print_metadata_json`, `print_metadata_summary`
inspect/assign the coordinate + source metadata attached to DataFrames and propagated
through operations.

## Logging

`pb.set_loglevel(level)` controls verbosity (the readers emit per-record warnings on
malformed input — e.g. a 3-column BED produces "Error reading record" with 0 rows).

## Platform notes

- Built on a recent Apache DataFusion stack (with a matching pyarrow floor).
- Robust predicate & projection pushdown on indexed formats.
- Python **3.11–3.14** supported (`<3.15,>=3.11`).
