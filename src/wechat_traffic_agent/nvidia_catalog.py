"""Secret-free inventory of NVIDIA hosted model capabilities.

The NVIDIA model catalog is broader than chat LLMs.  This module deliberately
separates:
1) a model existing in NVIDIA's hosted catalog,
2) its inferred capability family / endpoint family,
3) real probe evidence proving that a concrete endpoint is usable now.

Catalog presence alone never authorizes routing or adoption.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping
import urllib.error
import urllib.request


CATALOG_SCHEMA = "nvidia-capability-catalog.v1"
CATALOG_URL = "https://integrate.api.nvidia.com/v1/models"


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NVIDIA catalog observation requires timezone")
    return now.astimezone(timezone.utc).replace(microsecond=0)


def classify_nvidia_model(model_id: str) -> dict[str, Any]:
    """Infer a conservative capability family from a model id.

    The hosted /v1/models response currently exposes identifiers but not the
    Build.NVIDIA labels.  Classification is therefore only a discovery prior;
    actual endpoint protocol and quality still require verification.
    """
    model = str(model_id or "").strip()
    low = model.lower()
    tags: list[str] = []
    category = "specialized"
    endpoint_family = "unknown"
    probe_supported = False

    if any(token in low for token in ("rerank", "rankqa")):
        category = "retrieval_rerank"
        endpoint_family = "rerank"
        tags += ["retrieval", "rerank"]
    elif any(token in low for token in ("embed", "embedding", "nvclip")):
        category = "retrieval_embedding"
        endpoint_family = "embeddings"
        tags += ["retrieval", "embedding"]
    elif any(token in low for token in ("ocr", "parse", "table-structure", "deplot")):
        category = "document_extraction"
        endpoint_family = "specialized"
        tags += ["document", "ocr", "table", "extraction"]
    elif any(token in low for token in (
        "safety", "guard", "moderation", "jailbreak", "topic-control"
    )):
        category = "safety_moderation"
        endpoint_family = "openai_chat"
        probe_supported = True
        tags += ["safety", "moderation"]
    elif any(token in low for token in (
        "parakeet", "speech", "audio", "tts", "translate", "riva"
    )):
        category = "speech_audio"
        endpoint_family = "specialized"
        tags += ["speech", "audio", "translation"]
    elif "reward" in low:
        category = "reward_model"
        endpoint_family = "specialized"
        tags += ["reward"]
    elif any(token in low for token in (
        "cosmos", "synthetic-video", "image-edit", "text-to-image",
        "video", "diffusion"
    )):
        category = "physical_media"
        endpoint_family = "specialized"
        tags += ["physical_ai", "image_video"]
    elif any(token in low for token in (
        "vision", "vlm", "omni", "vila", "neva", "fuyu", "kosmos",
        "muse-glimmer", "kimi-k3", "glm-5.3-flash",
    )):
        category = "vision_multimodal"
        endpoint_family = "openai_chat"
        probe_supported = True
        tags += ["multimodal", "vision", "reasoning"]
    elif any(token in low for token in (
        "gpt", "llama", "nemotron", "deepseek", "glm", "qwen", "kimi",
        "mistral", "mixtral", "gemma", "granite", "jamba", "dbrx",
        "starcoder", "codestral", "palmyra", "yi-large", "zamba",
        "laguna", "phi-3.5",
    )):
        category = "text_reasoning"
        endpoint_family = "openai_chat"
        probe_supported = True
        tags += ["text", "reasoning"]
        if any(token in low for token in ("code", "coder", "codestral", "starcoder", "laguna")):
            tags.append("coding")
    elif "ising-calibration" in low:
        category = "scientific_multimodal"
        endpoint_family = "openai_chat"
        probe_supported = True
        tags += ["quantum", "vision", "scientific"]
    else:
        # Unknown models remain visible rather than silently disappearing.
        tags.append("unclassified")

    return {
        "model": model,
        "category": category,
        "endpoint_family": endpoint_family,
        "probe_supported": probe_supported,
        "tags": list(dict.fromkeys(tags)),
        "classification_source": "MODEL_ID_HEURISTIC",
    }


class NvidiaCapabilityCatalog:
    def __init__(self, *, state_root: Path, credential_env: str = "NVIDIA_API_KEY") -> None:
        self.state_root = Path(state_root)
        self.credential_env = str(credential_env)
        self.path = (
            self.state_root
            / "application"
            / "nvidia_capability_catalog"
            / "latest.json"
        )

    def observe(
        self,
        *,
        now: datetime,
        minimum_interval: timedelta = timedelta(hours=1),
    ) -> dict[str, Any]:
        now_utc = _utc(now)
        previous = self._read()
        if previous:
            try:
                last = datetime.fromisoformat(
                    str(previous.get("observed_at") or "").replace("Z", "+00:00")
                )
            except ValueError:
                last = None
            if (
                last is not None
                and last.tzinfo is not None
                and now_utc - last.astimezone(timezone.utc) < minimum_interval
            ):
                return self._summary(previous, status="NOT_DUE")

        api_key = str(os.environ.get(self.credential_env) or "").strip()
        if not api_key:
            return {
                "status": "CREDENTIAL_MISSING",
                "credential_env": self.credential_env,
            }
        payload = self._fetch(api_key)
        raw_models = payload.get("data")
        if not isinstance(raw_models, list):
            raise ValueError("NVIDIA catalog response has no model list")
        model_ids = sorted({
            str(row.get("id") or "").strip()
            for row in raw_models
            if isinstance(row, Mapping) and str(row.get("id") or "").strip()
        })
        models = [classify_nvidia_model(model) for model in model_ids]
        previous_ids = {
            str(row.get("model") or "")
            for row in (previous or {}).get("models") or []
            if isinstance(row, Mapping)
        }
        current_ids = set(model_ids)
        counts = Counter(str(row["category"]) for row in models)
        endpoint_counts = Counter(str(row["endpoint_family"]) for row in models)
        snapshot = {
            "schema_version": CATALOG_SCHEMA,
            "observed_at": now_utc.isoformat(),
            "credential_env": self.credential_env,
            "source": CATALOG_URL,
            "model_count": len(models),
            "category_counts": dict(sorted(counts.items())),
            "endpoint_family_counts": dict(sorted(endpoint_counts.items())),
            "new_models": sorted(current_ids - previous_ids),
            "removed_models": sorted(previous_ids - current_ids),
            "models": models,
        }
        self._write(snapshot)
        return self._summary(snapshot, status="OBSERVED")

    def models(
        self,
        *,
        categories: tuple[str, ...] | None = None,
        endpoint_family: str | None = None,
    ) -> list[dict[str, Any]]:
        snapshot = self._read() or {}
        rows = [
            dict(row)
            for row in snapshot.get("models") or []
            if isinstance(row, Mapping)
        ]
        if categories is not None:
            allowed = set(categories)
            rows = [row for row in rows if row.get("category") in allowed]
        if endpoint_family is not None:
            rows = [
                row for row in rows
                if row.get("endpoint_family") == endpoint_family
            ]
        return rows

    def _fetch(self, api_key: str) -> Mapping[str, Any]:
        request = urllib.request.Request(
            CATALOG_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"NVIDIA_CATALOG_HTTP_{exc.code}") from exc
        except TimeoutError as exc:
            raise RuntimeError("NVIDIA_CATALOG_TIMEOUT") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("NVIDIA_CATALOG_NETWORK_ERROR") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("NVIDIA catalog response is not an object")
        return payload

    def _read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict) or raw.get("schema_version") != CATALOG_SCHEMA:
            return None
        return raw

    def _write(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    @staticmethod
    def _summary(snapshot: Mapping[str, Any], *, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "observed_at": snapshot.get("observed_at"),
            "model_count": int(snapshot.get("model_count") or 0),
            "category_counts": dict(snapshot.get("category_counts") or {}),
            "endpoint_family_counts": dict(
                snapshot.get("endpoint_family_counts") or {}
            ),
            "new_models": list(snapshot.get("new_models") or []),
            "removed_models": list(snapshot.get("removed_models") or []),
        }


__all__ = [
    "CATALOG_SCHEMA",
    "NvidiaCapabilityCatalog",
    "classify_nvidia_model",
]
