"""Metadata provider interfaces for Illumina Connected Analytics enrichment."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - exercised in runtime envs without requests
    requests = None  # type: ignore[assignment]


DEFAULT_ICA_BASE_URL = "https://ica.illumina.com/ica/rest"
ICA_ACCEPT_HEADER = "application/vnd.illumina.v3+json"


@dataclass
class MetadataEnrichmentResult:
    """Structured result returned by metadata providers."""

    provider: str
    status: str
    warnings: list[str] = field(default_factory=list)
    project: dict[str, Any] = field(default_factory=dict)
    run: dict[str, Any] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "warnings": list(self.warnings),
            "project": dict(self.project),
            "run": dict(self.run),
            "samples": [dict(sample) for sample in self.samples],
        }


class BaseMetadataProvider:
    """Provider interface for optional metadata enrichment."""

    provider_name = "none"

    def enrich(
        self,
        *,
        bundle_dir: Path,
        project_id: str | None = None,
        run_id: str | None = None,
        allow_mock: bool = False,
    ) -> MetadataEnrichmentResult:
        raise NotImplementedError


class NullMetadataProvider(BaseMetadataProvider):
    """Default provider that performs no enrichment."""

    provider_name = "none"

    def enrich(
        self,
        *,
        bundle_dir: Path,
        project_id: str | None = None,
        run_id: str | None = None,
        allow_mock: bool = False,
    ) -> MetadataEnrichmentResult:
        return MetadataEnrichmentResult(provider="none", status="disabled")


class ICAMetadataProvider(BaseMetadataProvider):
    """Metadata-only ICA connector."""

    provider_name = "ica"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        session: Any = None,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("ILLUMINA_ICA_API_KEY", "")
        self.base_url = (base_url or os.environ.get("ILLUMINA_ICA_BASE_URL") or DEFAULT_ICA_BASE_URL).rstrip("/")
        self.session = session
        if self.session is None and requests is not None:
            self.session = requests.Session()
        if self.session is not None:
            self.session.headers.update(
                {
                    "Accept": ICA_ACCEPT_HEADER,
                    "X-API-Key": self.api_key,
                }
            )

    def _fetch_json(self, endpoint: str) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("requests is not installed; ICA metadata lookup is unavailable.")
        response = self.session.get(f"{self.base_url}{endpoint}", timeout=30)
        response.raise_for_status()
        return response.json()

    def _normalize_project(self, payload: dict[str, Any], fallback_id: str | None) -> dict[str, Any]:
        return {
            "id": payload.get("id", fallback_id or ""),
            "name": payload.get("name", ""),
            "status": payload.get("status", ""),
        }

    def _normalize_run(self, payload: dict[str, Any], fallback_id: str | None) -> dict[str, Any]:
        pipeline = payload.get("pipeline", {})
        pipeline_name = ""
        if isinstance(pipeline, dict):
            pipeline_name = pipeline.get("name", "") or pipeline.get("code", "")
        elif isinstance(pipeline, str):
            pipeline_name = pipeline

        return {
            "id": payload.get("id", fallback_id or ""),
            "name": payload.get("name", ""),
            "status": payload.get("status", ""),
            "pipeline": pipeline_name,
        }

    def _normalize_samples(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            return []
        normalized: list[dict[str, Any]] = []
        for sample in payload:
            if not isinstance(sample, dict):
                continue
            sample_id = sample.get("sample_id") or sample.get("sampleId") or sample.get("name") or ""
            if not sample_id:
                continue
            normalized.append(
                {
                    "sample_id": str(sample_id),
                    "ica_sample_id": str(sample.get("ica_sample_id") or sample.get("id") or ""),
                    "analysis_status": str(sample.get("analysis_status") or sample.get("status") or ""),
                    "cohort": str(sample.get("cohort") or ""),
                    "notes": str(sample.get("notes") or ""),
                }
            )
        return normalized

    def _load_mock_payload(self, bundle_dir: Path) -> dict[str, Any]:
        mock_path = bundle_dir / "mock_ica_metadata.json"
        if not mock_path.exists():
            raise FileNotFoundError(f"No mock ICA metadata found in bundle: {mock_path}")
        return json.loads(mock_path.read_text(encoding="utf-8"))

    def _result_from_payload(
        self,
        payload: dict[str, Any],
        *,
        status: str,
        warnings: list[str] | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
    ) -> MetadataEnrichmentResult:
        warnings = warnings or []
        return MetadataEnrichmentResult(
            provider="ica",
            status=status,
            warnings=warnings,
            project=self._normalize_project(payload.get("project", {}), project_id),
            run=self._normalize_run(payload.get("run", {}), run_id),
            samples=self._normalize_samples(payload.get("samples")),
        )

    def enrich(
        self,
        *,
        bundle_dir: Path,
        project_id: str | None = None,
        run_id: str | None = None,
        allow_mock: bool = False,
    ) -> MetadataEnrichmentResult:
        if not project_id or not run_id:
            return MetadataEnrichmentResult(
                provider="ica",
                status="skipped",
                warnings=["ICA metadata provider requested without both project and run IDs."],
            )

        if not self.api_key:
            if allow_mock:
                try:
                    payload = self._load_mock_payload(bundle_dir)
                    return self._result_from_payload(
                        payload,
                        status="mocked-demo",
                        warnings=["Using bundled mock ICA metadata because no API key was configured."],
                        project_id=project_id,
                        run_id=run_id,
                    )
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
            return MetadataEnrichmentResult(
                provider="ica",
                status="warning",
                warnings=["ILLUMINA_ICA_API_KEY is not set; continuing without ICA metadata enrichment."],
            )

        if self.session is None:
            return MetadataEnrichmentResult(
                provider="ica",
                status="warning",
                warnings=["The optional 'requests' dependency is not installed; continuing without ICA metadata enrichment."],
            )

        try:
            project_payload = self._fetch_json(f"/api/projects/{project_id}")
            run_payload = self._fetch_json(f"/api/projects/{project_id}/analyses/{run_id}")
        except Exception as exc:
            return MetadataEnrichmentResult(
                provider="ica",
                status="warning",
                warnings=[f"ICA metadata request failed: {exc}"],
            )

        combined = {
            "project": project_payload,
            "run": run_payload,
            "samples": run_payload.get("samples", []),
        }
        return self._result_from_payload(
            combined,
            status="enriched",
            project_id=project_id,
            run_id=run_id,
        )


def build_metadata_provider(name: str) -> BaseMetadataProvider:
    """Factory for provider selection."""

    if name == "ica":
        return ICAMetadataProvider()
    return NullMetadataProvider()
