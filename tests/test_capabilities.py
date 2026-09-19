"""Deterministic capability fixtures + Ollama/model states (offline)."""

from __future__ import annotations

from client.capabilities import (
    CAPABILITY_HIGH,
    CAPABILITY_LOW,
    CAPABILITY_MEDIUM,
    CAPABILITY_UNKNOWN,
    detect_capabilities,
    evaluate_class,
)
from client.models import (
    LOCAL_AI_AVAILABLE,
    LOCAL_AI_INSUFFICIENT,
    LOCAL_AI_UNAVAILABLE,
    LOCAL_AI_UNKNOWN,
    evaluate_model,
    find_model,
)

GB = 1024 ** 3


def _profile(*, ram_gb, avail_gb=None, cores=8, gpu=None):
    return {
        "ram_total_bytes": int(ram_gb * GB),
        "ram_available_bytes": int((avail_gb if avail_gb is not None else ram_gb) * GB),
        "cpu_cores": cores,
        "gpu": gpu or {"present": False},
    }


def test_high_end_workstation():
    profile = _profile(ram_gb=64, avail_gb=32, cores=16)
    assert evaluate_class(profile) == CAPABILITY_HIGH


def test_apple_silicon_laptop():
    profile = _profile(ram_gb=24, avail_gb=10, cores=10)
    profile["gpu"] = {"present": True, "backend": "metal", "vram_bytes": None}
    assert evaluate_class(profile) == CAPABILITY_HIGH


def test_nvidia_workstation_mid_ram():
    profile = _profile(ram_gb=16, avail_gb=6, cores=8)
    profile["gpu"] = {"present": True, "backend": "cuda", "vram_bytes": 8 * GB}
    assert evaluate_class(profile) == CAPABILITY_MEDIUM


def test_medium_laptop():
    assert evaluate_class(_profile(ram_gb=16, avail_gb=4, cores=8)) == CAPABILITY_MEDIUM


def test_low_end_cpu_only():
    assert evaluate_class(_profile(ram_gb=4, avail_gb=1, cores=2)) == CAPABILITY_LOW


def test_unknown_device():
    assert (
        evaluate_class({"ram_total_bytes": None, "ram_available_bytes": None,
                        "cpu_cores": None})
        == CAPABILITY_UNKNOWN
    )


def test_custom_policy_thresholds():
    profile = _profile(ram_gb=16, avail_gb=4, cores=8)
    assert (
        evaluate_class(profile, policy={"medium_ram_total_gb": 32.0})
        == CAPABILITY_LOW
    )


def test_ollama_unreachable_is_unknown():
    capabilities = _profile(ram_gb=64, avail_gb=32, cores=16)
    result = evaluate_model("qwen3-coder:30b", capabilities, None)
    assert result["state"] == LOCAL_AI_UNKNOWN


def test_model_missing_is_unavailable():
    capabilities = _profile(ram_gb=64, avail_gb=32, cores=16)
    result = evaluate_model("qwen3-coder:30b", capabilities, [{"name": "other:1b"}])
    assert result["state"] == LOCAL_AI_UNAVAILABLE


def test_model_installed_and_fits():
    capabilities = _profile(ram_gb=64, avail_gb=32, cores=16)
    installed = [{"name": "qwen3-coder:30b", "size": 18556700761}]
    result = evaluate_model("qwen3-coder:30b", capabilities, installed)
    assert result["state"] == LOCAL_AI_AVAILABLE


def test_model_too_large_for_device():
    capabilities = _profile(ram_gb=8, avail_gb=3, cores=4)
    installed = [{"name": "qwen3-coder:30b", "size": 18556700761}]
    result = evaluate_model("qwen3-coder:30b", capabilities, installed)
    assert result["state"] == LOCAL_AI_INSUFFICIENT
    assert result["detail"]["reasons"]


def test_unknown_model_is_unknown():
    capabilities = _profile(ram_gb=64, avail_gb=32, cores=16)
    result = evaluate_model("mystery:99b", capabilities, [])
    assert result["state"] == LOCAL_AI_UNKNOWN


def test_find_model_prefix_match():
    installed = [{"name": "qwen3-coder:30b-instruct-q4_K_M"}]
    assert find_model(installed, "qwen3-coder:30b") is not None
    assert find_model(installed, "qwen3:14b") is None


def test_detect_capabilities_never_raises():
    profile = detect_capabilities()
    assert isinstance(profile, dict)
    assert "cpu_cores" in profile and "gpu" in profile
    assert evaluate_class(profile) in (
        CAPABILITY_HIGH, CAPABILITY_MEDIUM, CAPABILITY_LOW, CAPABILITY_UNKNOWN,
    )
