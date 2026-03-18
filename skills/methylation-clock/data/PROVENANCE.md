# Demo Fixture Provenance

File: `GSE139307_small.pkl`

## What It Contains

- A small, local demo subset of methylation data for GEO accession `GSE139307`
- Intended for smoke testing and documentation demos of `skills/methylation-clock/methylation_clock.py`
- Serialized as a pandas pickle compatible with the current PyAging workflow

## Source And Generation

- Upstream source dataset: `GSE139307` (downloaded through PyAging helper utilities)
- Generation workflow: `PyAging_tutorial.ipynb`
- Method summary: load the full `GSE139307.pkl`, then persist a reduced two-sample subset as `GSE139307_small.pkl` for lightweight tests

## Integrity

- SHA-256: `3d392339e82c4ccb9763e169f2790a3f093b026672adaf13749a3f176f4ede47`
- Size (bytes): `14094255`

## Safety Note

This file is a repository-controlled fixture and should only be loaded from this trusted repository path. Do not load untrusted pickle files from unknown sources.

ClawBio is a research and educational tool. It is not a medical device and does not provide clinical diagnoses. Consult a healthcare professional before making any medical decisions.
