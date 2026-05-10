"""LD reference execution skill.

Computes r² between a lead variant and a list of partner variants using a
1000 Genomes Phase 3 GRCh38 PLINK2 reference panel, via plink2 subprocess.

License posture:

- 1000G Phase 3 GRCh38 PLINK2: open access without embargo, attribution
  required (Auton 2015 Nature, Clarke 2017 NAR). NOT formally CC0; treated as
  Green-with-attribution.
- plink2 binary: GPL-3 standalone. Subprocess invocation is FSF aggregation
  (https://www.gnu.org/licenses/gpl-faq.html#MereAggregation), not linkage.
  Codebase remains MIT. Bind to a fixed plink2 version path; do NOT bundle
  the binary in our wheels. Users install via apt-get / brew / container.

This module is intentionally a thin wrapper:
- inputs: panel path (PLINK2 .pgen+.pvar+.psam), super-pop, lead variant,
  partner variant ids (or window), plink2 binary path
- outputs: LDResult with per-partner r² values

Caching is the orchestrator's job. The wrapper does not write to a cache
itself.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

DEFAULT_PLINK2_BIN = os.environ.get("PLINK2_BIN", "plink2")

PLINK2_NOT_FOUND_HINT = (
    "plink2 binary not found. Install via `brew install plink2` (macOS) or "
    "`apt-get install plink2` (Linux), then either ensure it is on PATH or set "
    "PLINK2_BIN to its absolute path."
)


def _ot_to_panel_id(ot_id: str, sep: str) -> str:
    """Convert OT chr_pos_ref_alt (underscore) to whatever separator the panel
    uses in its variant ID column. The 1000G GRCh38 PLINK2 distribution at
    cog-genomics.org uses chr:pos:ref:alt (colon), so `sep=":"` for that panel.
    Heuristic: if the OT id is already in the target separator, return as-is.
    """
    if sep == "_":
        return ot_id
    if sep in ot_id:
        return ot_id
    # OT ids have exactly 4 underscore-separated tokens (chr, pos, ref, alt).
    return ot_id.replace("_", sep, 3)


def _panel_to_ot_id(panel_id: str) -> str:
    """Inverse: convert chr:pos:ref:alt (or any separator) back to OT chr_pos_ref_alt."""
    return panel_id.replace(":", "_", 3)


class SuperPop(str, Enum):
    """1000G Phase 3 super-populations.

    Per-study ancestry tag drives the choice. EUR is the explicit fallback
    for unspecified-ancestry studies.
    """
    EUR = "EUR"
    AFR = "AFR"
    AMR = "AMR"
    EAS = "EAS"
    SAS = "SAS"


@dataclass
class LDPair:
    """r² between the lead and one partner variant."""

    partner_variant_id: str  # chr_pos_ref_alt (matches OT join key)
    r2: float
    dprime: float | None = None  # plink2 also reports D'; optional


@dataclass
class LDResult:
    panel_id: str  # e.g. "1000g_phase3_v5b_grch38_basic"
    panel_version: str  # e.g. "5b"
    super_pop: SuperPop
    plink2_version: str
    chromosome: str
    lead_variant_id: str
    window_bp: int
    n_partners_requested: int
    n_partners_returned: int
    pairs: list[LDPair]
    fetched_at_utc: str
    notes: list[str] = field(default_factory=list)


class LDComputeError(Exception):
    """Raised when r² computation cannot proceed (plink2 missing, panel missing, etc.)."""


def _detect_plink2_version(plink2_bin: str) -> str:
    """Returns the plink2 --version string for the manifest."""
    if shutil.which(plink2_bin) is None:
        raise LDComputeError(f"{PLINK2_NOT_FOUND_HINT} (looked for: {plink2_bin})")
    try:
        out = subprocess.run(
            [plink2_bin, "--version"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        # plink2 prints version on stdout or stderr depending on build.
        line = (out.stdout or out.stderr).strip().splitlines()
        return line[0] if line else "unknown"
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise LDComputeError(f"could not run `{plink2_bin} --version`: {e!s}") from e


class Plink2LDClient:
    """Compute r² between a lead and partner variants via plink2 subprocess.

    Usage:
      client = Plink2LDClient(panel_path="/data/1000g_phase3_grch38_eur",
                              super_pop=SuperPop.EUR,
                              panel_version="5b",
                              panel_id="1000g_phase3_v5b_grch38_basic")
      r2 = client.r2_with_lead(lead="2_36910110_C_T",
                                partners=["2_36932656_A_G", ...])
    """

    def __init__(
        self,
        panel_path: str | Path,
        super_pop: SuperPop,
        panel_id: str,
        panel_version: str,
        plink2_bin: str = DEFAULT_PLINK2_BIN,
        panel_id_separator: str = ":",
    ) -> None:
        """`panel_id_separator` is the character separating chr/pos/ref/alt in
        the panel's variant ID column. The 1000G GRCh38 PLINK2 distribution
        from cog-genomics.org uses ":". Set to "_" if the panel already uses
        OT-style ids.
        """
        self.panel_path = Path(panel_path)
        self.super_pop = super_pop
        self.panel_id = panel_id
        self.panel_version = panel_version
        self.plink2_bin = plink2_bin
        self.panel_id_separator = panel_id_separator
        # Validate plink2 + panel up-front so callers fail fast.
        self.plink2_version = _detect_plink2_version(plink2_bin)
        self._validate_panel()

    def _validate_panel(self) -> None:
        # PLINK2 panel = three sibling files: .pgen / .pvar / .psam.
        suffixes = (".pgen", ".pvar", ".psam")
        for s in suffixes:
            if not self.panel_path.with_suffix(s).exists() and not Path(
                f"{self.panel_path}{s}"
            ).exists():
                # Some bundles have the suffix in the bare path already.
                if not str(self.panel_path).endswith(s):
                    raise LDComputeError(
                        f"PLINK2 panel missing {s} sibling for "
                        f"{self.panel_path}. Expected three files with "
                        f"suffixes .pgen / .pvar / .psam."
                    )

    def r2_with_lead(
        self,
        lead: str,
        partners: Iterable[str],
        chromosome: str | None = None,
        window_bp: int | None = None,
    ) -> LDResult:
        """Compute r² between `lead` and every partner in `partners`.

        Implementation strategy: write the union of {lead, partners} to a temp
        --extract list, then call `plink2 --r2-unphased --ld-snp <lead>
        --ld-window-r2 0`. Parse the plink2 output `.vcor2` file.
        """
        partner_list = list(partners)
        notes: list[str] = []
        pairs: list[LDPair] = []
        if not partner_list:
            return LDResult(
                panel_id=self.panel_id,
                panel_version=self.panel_version,
                super_pop=self.super_pop,
                plink2_version=self.plink2_version,
                chromosome=chromosome or "",
                lead_variant_id=lead,
                window_bp=window_bp or 0,
                n_partners_requested=0,
                n_partners_returned=0,
                pairs=[],
                fetched_at_utc=_now_utc(),
                notes=["no partners requested"],
            )

        sep = self.panel_id_separator
        lead_panel = _ot_to_panel_id(lead, sep)
        partner_panel = [_ot_to_panel_id(p, sep) for p in partner_list]

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            extract_path = td_path / "extract.txt"
            with extract_path.open("w") as f:
                f.write(lead_panel + "\n")
                for p in partner_panel:
                    f.write(p + "\n")
            out_prefix = td_path / "ld_out"
            cmd = [
                self.plink2_bin,
                "--pfile", str(self.panel_path),
                "--extract", str(extract_path),
                "--r2-unphased",
                "--ld-snp", lead_panel,
                "--ld-window-r2", "0",
                "--out", str(out_prefix),
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, check=False, timeout=120,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                raise LDComputeError(f"plink2 invocation failed: {e!s}") from e
            if proc.returncode != 0:
                raise LDComputeError(
                    f"plink2 exited with code {proc.returncode}. "
                    f"stderr (truncated): {(proc.stderr or '')[:1000]}"
                )

            # plink2 v2.00a7+ writes `.vcor`; older versions wrote `.vcor2`.
            vcor_path: Path | None = None
            for suffix in (".vcor", ".vcor2"):
                candidate = Path(f"{out_prefix}{suffix}")
                if candidate.exists():
                    vcor_path = candidate
                    break
            if vcor_path is None:
                raise LDComputeError(
                    f"plink2 produced no .vcor / .vcor2 at {out_prefix}; "
                    f"check stdout: {(proc.stdout or '')[:1000]}"
                )
            pairs = _parse_plink2_vcor2(vcor_path, lead_panel, notes)
            # Convert panel-format ids back to OT format on the way out.
            for p in pairs:
                p.partner_variant_id = _panel_to_ot_id(p.partner_variant_id)

        return LDResult(
            panel_id=self.panel_id,
            panel_version=self.panel_version,
            super_pop=self.super_pop,
            plink2_version=self.plink2_version,
            chromosome=chromosome or "",
            lead_variant_id=lead,
            window_bp=window_bp or 0,
            n_partners_requested=len(partner_list),
            n_partners_returned=len(pairs),
            pairs=pairs,
            fetched_at_utc=_now_utc(),
            notes=notes,
        )


def _parse_plink2_vcor2(path: Path, lead: str, notes: list[str]) -> list[LDPair]:
    """Parse plink2's `.vcor2` output (--r2-unphased format).

    Columns (plink2 v2.00+): #CHROM_A POS_A ID_A REF_A ALT_A CHROM_B POS_B ID_B
    REF_B ALT_B UNPHASED_R2 D' (or similar). Schema can drift across plink2
    builds; we read the header and locate columns by name.
    """
    pairs: list[LDPair] = []
    with path.open("r") as f:
        reader = csv.reader(f, delimiter="\t")
        header: list[str] | None = None
        for row in reader:
            if not row:
                continue
            if header is None:
                header = [c.lstrip("#") for c in row]
                continue
            r = dict(zip(header, row))
            id_a = r.get("ID_A") or ""
            id_b = r.get("ID_B") or ""
            partner = id_b if id_a == lead else (id_a if id_b == lead else None)
            if partner is None:
                continue
            r2 = _maybe_float(r.get("UNPHASED_R2") or r.get("R2"))
            dprime = _maybe_float(r.get("DP") or r.get("D'") or r.get("Dprime"))
            if r2 is None:
                notes.append(f"missing r² value for partner {partner}")
                continue
            pairs.append(LDPair(partner_variant_id=partner, r2=r2, dprime=dprime))
    return pairs


def _maybe_float(v: str | None) -> float | None:
    if v is None or v == "" or v == "NA":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if (f == f) else None


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = [
    "DEFAULT_PLINK2_BIN",
    "LDComputeError",
    "LDPair",
    "LDResult",
    "PLINK2_NOT_FOUND_HINT",
    "Plink2LDClient",
    "SuperPop",
]


# ---------------------------------------------------------------------------
# CLI entry point: --input <config> --output <dir> --demo.
# Default mode: on-demand region fetch (no pre-baked panel required).
# ---------------------------------------------------------------------------

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

# When run directly as a script, ensure the script's own directory is on
# sys.path so the sibling `ondemand_client` module resolves.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# Local cache for LD-compute outputs (region VCFs are cached separately by
# OnDemand1000GLDClient under ~/.clawbio/locuscompare_cache/1000g/).
DEFAULT_LD_RESULT_CACHE_DIR = Path(
    os.environ.get(
        "LD_1000G_RESULT_CACHE_DIR",
        Path.home() / ".clawbio" / "ld_1000g_region_compute_cache",
    )
).expanduser()


def main(argv: list[str] | None = None) -> int:
    """Standard skill CLI: --input <config> --output <dir> --demo.

    Config schema (JSON or YAML):
        lead: 1_109274968_G_T
        partners:
          - 1_109270398_G_A
          - 1_109274570_A_G
        chromosome: "1"
        window_bp: 1000000
        super_pop: EUR
        # optional pre-baked panel mode:
        plink2_panel_path: /path/to/chr1_eur

    Writes <output>/{ld_pairs.tsv, manifest.yaml, report.md}.
    """
    parser = argparse.ArgumentParser(
        prog="ld-1000g-region-compute",
        description="Compute pairwise r² between a lead and partner variants using 1000G Phase 3 GRCh38, ancestry-stratified.",
    )
    parser.add_argument("--input", type=Path, help="JSON or YAML config (see docstring).")
    parser.add_argument("--output", type=Path,
                        help="Output directory; created if missing. Required unless --list-demos.")
    parser.add_argument("--demo", nargs="?", const="__default__", default=None,
                        metavar="NAME",
                        help="Run a bundled demo. Bare --demo runs the default; "
                             "pass a name to choose a specific one. See --list-demos.")
    parser.add_argument("--list-demos", action="store_true",
                        help="List bundled demo configs in this skill's examples/ directory.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Bypass the LD-result cache; re-compute every time.")
    args = parser.parse_args(argv)

    if args.list_demos:
        _print_available_demos()
        return 0
    if args.demo is None and args.input is None:
        parser.error("either --input <config> or --demo [NAME] or --list-demos is required")
    if args.output is None:
        parser.error("--output is required")
    args.output.mkdir(parents=True, exist_ok=True)

    if args.demo is not None:
        cfg_path = _resolve_demo_path(args.demo)
        cfg = _load_config(cfg_path)
        print(f"info: using bundled demo {cfg_path.name}", file=sys.stderr)
    else:
        cfg = _load_config(args.input)

    super_pop_str = cfg.get("super_pop", "EUR")
    plink2_bin = cfg.get("plink2_bin", DEFAULT_PLINK2_BIN)
    lead = cfg["lead"]
    partners = [p for p in cfg["partners"] if p != lead]
    chromosome = str(cfg["chromosome"])
    window_bp = int(cfg["window_bp"])

    cache_dir = None if args.no_cache else DEFAULT_LD_RESULT_CACHE_DIR
    cache_path = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        import hashlib
        partners_hash = hashlib.sha1(("\n".join(sorted(partners))).encode()).hexdigest()[:12]
        cache_path = cache_dir / f"{lead}__{super_pop_str}__win{window_bp}__{partners_hash}.json"
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text())
            _write_ld_outputs(cached, args.output)
            print(f"ld-1000g-region-compute: {cached['n_partners_returned']} pairs (cache hit) -> {args.output / 'ld_pairs.tsv'}")
            return 0

    if cfg.get("plink2_panel_path"):
        client = Plink2LDClient(
            panel_path=cfg["plink2_panel_path"],
            super_pop=SuperPop[super_pop_str],
            panel_id=cfg.get("panel_id", "1000g_phase3_v5b_grch38_basic"),
            panel_version=cfg.get("panel_version", "5b"),
            plink2_bin=plink2_bin,
        )
    else:
        from ondemand_client import (
            OnDemand1000GLDClient,
        )
        client = OnDemand1000GLDClient(
            super_pop=super_pop_str,
            plink2_bin=plink2_bin,
        )

    result = client.r2_with_lead(
        lead=lead,
        partners=partners,
        chromosome=chromosome,
        window_bp=window_bp,
    )
    payload = {
        "lead": lead,
        "chromosome": chromosome,
        "window_bp": window_bp,
        "super_pop": getattr(result.super_pop, "value", str(result.super_pop)),
        "panel_id": result.panel_id,
        "panel_version": result.panel_version,
        "plink2_version": result.plink2_version,
        "n_partners_requested": result.n_partners_requested,
        "n_partners_returned": result.n_partners_returned,
        "fetched_at_utc": result.fetched_at_utc,
        "notes": list(result.notes),
        "pairs": [{"partner_variant_id": p.partner_variant_id,
                   "r2": p.r2,
                   "dprime": getattr(p, "dprime", None)} for p in result.pairs],
    }
    if cache_path is not None:
        cache_path.write_text(json.dumps(payload, default=str))
    _write_ld_outputs(payload, args.output)
    print(f"ld-1000g-region-compute: {result.n_partners_returned} pairs -> {args.output / 'ld_pairs.tsv'}")
    print(f"  panel: {result.panel_id} ({payload['super_pop']}) | plink2 {result.plink2_version}")
    return 0


def _load_config(path: Path) -> dict:
    text = path.read_text()
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml as _yaml
        return _yaml.safe_load(text) or {}
    if path.suffix.lower() == ".json":
        return json.loads(text)
    raise ValueError(f"unsupported config extension: {path.suffix}")


def _examples_dir() -> Path:
    return Path(__file__).resolve().parent / "examples"


def _list_demos() -> list[Path]:
    out: list[Path] = []
    for ext in ("*.json", "*.yaml", "*.yml"):
        out.extend(sorted(_examples_dir().glob(ext)))
    return [p for p in out if p.name not in {"expected_output.md", "README.md"}]


def _resolve_demo_path(name: str) -> Path:
    examples = _examples_dir()
    if name == "__default__":
        for cand in ("default.json", "default.yaml", "default.yml", "input.json"):
            p = examples / cand
            if p.is_file():
                return p
        files = _list_demos()
        if not files:
            raise FileNotFoundError(f"no bundled demo configs found in {examples}")
        return files[0]
    for ext in (".json", ".yaml", ".yml", ""):
        p = examples / (name if ext == "" else f"{name}{ext}")
        if p.is_file():
            return p
    available = ", ".join(p.stem for p in _list_demos())
    raise FileNotFoundError(
        f"no bundled demo named {name!r} in {examples}. Available: {available}"
    )


def _print_available_demos() -> None:
    paths = _list_demos()
    if not paths:
        print(f"no bundled demos in {_examples_dir()}")
        return
    try:
        default_path = _resolve_demo_path("__default__")
    except FileNotFoundError:
        default_path = None
    print(f"Bundled demos in {_examples_dir()}:")
    for p in paths:
        marker = " (default)" if default_path is not None and p == default_path else ""
        print(f"  {p.stem}{marker}    [{p.name}]")


def _write_ld_outputs(payload: dict, output: Path) -> None:
    tsv_path = output / "ld_pairs.tsv"
    cols = ["lead_variant_id", "partner_variant_id", "r2", "dprime", "panel_id", "super_pop"]
    with tsv_path.open("w") as f:
        f.write("# ld-1000g-region-compute v0.1.0\n")
        f.write(f"# panel: {payload['panel_id']}\n")
        f.write(f"# super_pop: {payload['super_pop']}\n")
        f.write(f"# plink2: {payload['plink2_version']}\n")
        f.write("\t".join(cols) + "\n")
        for p in payload["pairs"]:
            row = [
                payload["lead"],
                p["partner_variant_id"],
                f"{p['r2']:.6f}",
                "" if p.get("dprime") is None else f"{p['dprime']:.6f}",
                payload["panel_id"],
                str(payload["super_pop"]),
            ]
            f.write("\t".join(row) + "\n")

    manifest = {
        "skill": "ld-1000g-region-compute",
        "version": "0.1.0",
        "lead": payload["lead"],
        "chromosome": payload["chromosome"],
        "window_bp": payload["window_bp"],
        "super_pop": payload["super_pop"],
        "panel_id": payload["panel_id"],
        "panel_version": payload["panel_version"],
        "plink2_version": payload["plink2_version"],
        "n_partners_requested": payload["n_partners_requested"],
        "n_partners_returned": payload["n_partners_returned"],
        "fetched_at_utc": payload["fetched_at_utc"],
        "outputs": {"ld_pairs_tsv": "ld_pairs.tsv"},
        "notes": payload.get("notes") or [],
    }
    try:
        import yaml as _yaml
        (output / "manifest.yaml").write_text(_yaml.safe_dump(manifest, sort_keys=False))
    except ImportError:
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    report = [
        "# ld-1000g-region-compute report",
        "",
        f"- **Lead:** `{payload['lead']}`",
        f"- **Region:** chr{payload['chromosome']} ±{payload['window_bp']//2//1000} kb",
        f"- **Reference panel:** {payload['panel_id']} ({payload['super_pop']})",
        f"- **plink2:** {payload['plink2_version']}",
        f"- **Partners requested / returned:** {payload['n_partners_requested']} / {payload['n_partners_returned']}",
        f"- **Output TSV:** ld_pairs.tsv",
    ]
    (output / "report.md").write_text("\n".join(report) + "\n")


if __name__ == "__main__":
    sys.exit(main())
