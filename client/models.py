"""Local model capability policy + Ollama detection (stdlib only).

All Qwen-specific knowledge lives in exactly one place: MODEL_PROFILES.
Nothing else in the codebase may hardcode per-model assumptions.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

LOCAL_AI_AVAILABLE = "AVAILABLE"
LOCAL_AI_UNAVAILABLE = "UNAVAILABLE"
LOCAL_AI_INSUFFICIENT = "INSUFFICIENT"
LOCAL_AI_UNKNOWN = "UNKNOWN"

#: Per-model requirements. Sizes are rough installed-size estimates used
#: only for capability gating, never as exact measurements.
MODEL_PROFILES = {
    "qwen3-coder:30b": {
        "size_gb": 18.0,
        "min_ram_total_gb": 24.0,
        "min_ram_available_gb": 8.0,
        "min_vram_gb": 0.0,
        "runtime": "ollama",
    },
    "qwen2.5-coder:14b": {
        "size_gb": 9.0,
        "min_ram_total_gb": 12.0,
        "min_ram_available_gb": 4.0,
        "min_vram_gb": 0.0,
        "runtime": "ollama",
    },
    "qwen3:14b": {
        "size_gb": 9.0,
        "min_ram_total_gb": 12.0,
        "min_ram_available_gb": 4.0,
        "min_vram_gb": 0.0,
        "runtime": "ollama",
    },
}


def _gb(value) -> float | None:
    return (value / (1024 ** 3)) if isinstance(value, int) and value >= 0 else None


def ollama_models(
    host: str = "127.0.0.1", port: int = 11434, timeout: float = 5.0
) -> list[dict] | None:
    """List locally installed Ollama models, or None when unreachable."""
    url = f"http://{host}:{port}/api/tags"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    models = payload.get("models")
    return list(models) if isinstance(models, list) else []


def ollama_available(host: str = "127.0.0.1", port: int = 11434) -> bool:
    """Whether an Ollama server answers locally."""
    return ollama_models(host, port) is not None


def find_model(models: list[dict], model_id: str) -> dict | None:
    """Find an installed model by id prefix (Ollama tags carry suffixes)."""
    for entry in models:
        name = entry.get("name", "") if isinstance(entry, dict) else ""
        if isinstance(name, str) and name.startswith(model_id):
            return entry
    return None


def evaluate_model(
    model_id: str,
    capabilities: dict,
    installed: list[dict] | None,
    *,
    profiles: dict | None = None,
) -> dict:
    """Deterministically evaluate one model on this device.

    Returns ``{"state": LOCAL_AI_*, "model_id": ..., "detail": {...}}``.
    """
    table = profiles or MODEL_PROFILES
    profile = table.get(model_id)
    if profile is None:
        return {
            "state": LOCAL_AI_UNKNOWN,
            "model_id": model_id,
            "detail": {"reason": "no capability profile for model"},
        }
    if installed is None:
        return {
            "state": LOCAL_AI_UNKNOWN,
            "model_id": model_id,
            "detail": {"reason": "ollama unreachable"},
        }
    entry = find_model(installed, model_id)
    if entry is None:
        return {
            "state": LOCAL_AI_UNAVAILABLE,
            "model_id": model_id,
            "detail": {"reason": "model not installed"},
        }
    total = _gb(capabilities.get("ram_total_bytes"))
    available = _gb(capabilities.get("ram_available_bytes"))
    problems = []
    if total is not None and total < profile["min_ram_total_gb"]:
        problems.append(
            f"total RAM {total:.1f}GB < required {profile['min_ram_total_gb']:.0f}GB"
        )
    if available is not None and available < profile["min_ram_available_gb"]:
        problems.append(
            f"available RAM {available:.1f}GB < required "
            f"{profile['min_ram_available_gb']:.0f}GB"
        )
    gpu = capabilities.get("gpu") or {}
    vram = _gb(gpu.get("vram_bytes")) if gpu.get("present") else 0.0
    if (vram or 0.0) < profile["min_vram_gb"] and profile["min_vram_gb"] > 0:
        problems.append("insufficient GPU memory")
    if problems:
        return {
            "state": LOCAL_AI_INSUFFICIENT,
            "model_id": model_id,
            "detail": {"reasons": problems, "installed_size": entry.get("size")},
        }
    detail = {"installed_size": entry.get("size")}
    if total is None and available is None:
        detail["note"] = "RAM unknown; assuming fit since model is installed"
    return {"state": LOCAL_AI_AVAILABLE, "model_id": model_id, "detail": detail}
