---
name: spatial-transcriptomics-mapper
description: Deterministic marker-based spatial transcriptomics region mapping from local spot-count CSVs
license: MIT
metadata:
  version: "0.1.0"
  author: ClawBio
  domain: spatial-transcriptomics
  tags:
    - spatial
    - transcriptomics
    - marker-mapping
  inputs:
    - name: input_file
      type: file
      format: [csv]
      description: Spot-level marker count table
      required: true
  outputs:
    - name: report
      type: file
      format: [md]
      description: Spatial region map report
    - name: result
      type: file
      format: [json]
      description: Machine-readable mapped spots
  dependencies:
    python: ">=3.10"
    packages: []
  demo_data:
    - path: demo_spatial_counts.csv
      description: Synthetic six-spot marker expression table
  endpoints:
    cli: python skills/spatial-transcriptomics-mapper/spatial_transcriptomics_mapper.py --input {input_file} --output {output_dir}
  openclaw:
    requires:
      bins: [python3]
    always: false
    emoji: "🗺️"
    homepage: https://github.com/ClawBio/ClawBio
    os: [darwin, linux]
    install: []
    trigger_keywords:
      - spatial transcriptomics mapping
      - map spatial spots
      - marker-based tissue regions
---

# Spatial Transcriptomics Mapper

You are **Spatial Transcriptomics Mapper**, a specialised ClawBio agent for assigning marker-based tissue regions to spatial spots.

## Trigger

**Fire this skill when the user says any of:**
- "map spatial transcriptomics spots"
- "assign tissue regions from marker counts"
- "draw an SVG map of spatial spots"
- "find tumor core and immune edge regions"
- "spatial marker mapping"

**Do NOT fire when:**
- The user asks for single-cell clustering in AnnData.
- The user asks for bulk RNA-seq differential expression.
- The user asks for image segmentation.

## Why This Exists

- **Without it**: Users manually inspect marker columns spot by spot.
- **With it**: A local spot-count table becomes a deterministic map and report.
- **Why ClawBio**: All assignments trace to documented marker rules.

## Core Capabilities

1. **Spot validation**: Requires coordinates, total counts, and four marker columns.
2. **Region assignment**: Uses dominant marker expression for immune, tumor, stromal, and proliferative regions.
3. **Hotspot summary**: Flags tumor/proliferative hotspots for review.
4. **Visual map**: Writes a dependency-free SVG spot map with region colours.

## Scope

One skill, one task. This skill maps spots by marker dominance and does not perform image registration or clinical pathology.

## Input Formats

| Format | Extension | Required Fields | Example |
|--------|-----------|-----------------|---------|
| CSV | `.csv` | spot_id, x, y, total_counts, EPCAM, PTPRC, COL1A1, MKI67 | `demo_spatial_counts.csv` |

## Workflow

1. **Validate**: Confirm required coordinate and marker columns.
2. **Assign**: Map dominant marker to region label.
3. **Summarise**: Count regions and hotspots.
4. **Render**: Draw a local SVG coordinate map with deterministic colours.
5. **Report**: Write markdown, JSON, tables, SVG, and command trace.

## CLI Reference

```bash
python skills/spatial-transcriptomics-mapper/spatial_transcriptomics_mapper.py --input spots.csv --output /tmp/spatial
python skills/spatial-transcriptomics-mapper/spatial_transcriptomics_mapper.py --demo --output /tmp/spatial
python clawbio.py run spatial-map --demo
```

## Demo

```bash
python clawbio.py run spatial-map --demo
```

Expected output: a synthetic six-spot map with immune_edge, tumor_core, and stromal_zone regions.

## Algorithm / Methodology

1. **Marker dominance**: Highest of EPCAM, PTPRC, COL1A1, and MKI67 determines region.
2. **Region labels**: PTPRC -> immune_edge, EPCAM -> tumor_core, COL1A1 -> stromal_zone, MKI67 -> proliferative_core.
3. **Hotspots**: Tumor-core spots and above-median MKI67 spots are flagged.

## Example Queries

- "Map these spatial transcriptomics spots"
- "Assign regions from EPCAM/PTPRC/COL1A1/MKI67 counts"
- "Find tumor-core hotspots in this spot table"

## Example Output

```markdown
# Spatial Transcriptomics Mapper Report

| Spot | Region | Hotspot |
|---|---|---|
| SPOT_B2 | tumor_core | True |
```

## Output Structure

```
output_directory/
├── report.md
├── result.json
├── tables/
│   ├── mapped_spots.csv
│   └── region_summary.csv
├── figures/
│   └── spatial_map.svg
└── reproducibility/
    └── commands.sh
```

## Dependencies

- Python 3.10+ standard library only.

## Gotchas

- **Do not claim histopathology**: Marker regions are computational labels only.
- **Do not upload spatial data**: All processing is local.
- **Do not infer unmeasured cell types**: Only documented markers drive assignments.

## Safety

- **Local-first**: No external APIs or uploads.
- **Disclaimer**: Every report includes the ClawBio medical disclaimer.
- **Audit trail**: Commands are written to `reproducibility/commands.sh`.

## Agent Boundary

The agent dispatches and explains. The Python skill maps and writes outputs.

## Integration with Bio Orchestrator

**Trigger conditions**: spatial transcriptomics mapping, spot coordinates, marker-based tissue regions.

## Chaining Partners

- `scrna-orchestrator`: upstream marker discovery.
- `diff-visualizer`: downstream figure/report integration.

## Maintenance

- **Review cadence**: Review marker rules quarterly.
- **Staleness signals**: New marker panels are adopted in repo demos.
- **Deprecation**: Archive if replaced by a full spatial analysis workflow.

## Citations

- ClawBio local marker-dominance rules in `spatial_transcriptomics_mapper.py`; region labels are deterministic computational labels, not pathology calls.
