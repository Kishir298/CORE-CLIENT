"""Deterministic hardware capability detection (stdlib only).

Everything degrades gracefully: any probe that fails yields ``None`` /
``UNKNOWN`` instead of raising. No assumptions about vendor (NVIDIA /
Apple / CUDA / Metal), OS, or installed software.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

CAPABILITY_HIGH = "HIGH"
CAPABILITY_MEDIUM = "MEDIUM"
CAPABILITY_LOW = "LOW"
CAPABILITY_UNKNOWN = "UNKNOWN"

#: GB thresholds for capability classes. Override per call when needed.
DEFAULT_POLICY = {
    "high_ram_total_gb": 24.0,
    "high_ram_available_gb": 8.0,
    "high_cpu_cores": 8,
    "medium_ram_total_gb": 8.0,
    "medium_ram_available_gb": 2.0,
    "medium_cpu_cores": 4,
}


def _total_ram_bytes() -> int | None:
    """Best-effort total physical RAM in bytes (None when unknown)."""
    try:
        if os.name == "nt":
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(_MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
            return None
        if sys.platform == "darwin":
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0:
                return int(out.stdout.strip())
            return None
        # POSIX: sysconf pages.
        if hasattr(os, "sysconf"):
            try:
                pages = os.sysconf("SC_PHYS_PAGES")
                page_size = os.sysconf("SC_PAGE_SIZE")
                if pages > 0 and page_size > 0:
                    return int(pages) * int(page_size)
            except (ValueError, OSError):
                pass
        return None
    except Exception:
        return None


def _available_ram_bytes() -> int | None:
    """Best-effort currently available RAM in bytes (None when unknown)."""
    try:
        if os.name == "nt":
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(_MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys)
            return None
        if sys.platform == "darwin":
            out = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5
            )
            if out.returncode != 0:
                return None
            page_size = 16384
            for line in out.stdout.splitlines():
                if "page size of" in line:
                    try:
                        page_size = int(line.split("page size of")[1].split()[0])
                    except (ValueError, IndexError):
                        pass
            free_pages = 0
            for line in out.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith(("Pages free:", "Pages inactive:",
                                        "Pages speculative:")):
                    try:
                        free_pages += int(stripped.split(":")[1].strip().rstrip("."))
                    except (ValueError, IndexError):
                        pass
            return free_pages * page_size if free_pages else None
        if hasattr(os, "sysconf"):
            try:
                pages = os.sysconf("SC_AVPHYS_PAGES")
                page_size = os.sysconf("SC_PAGE_SIZE")
                if pages > 0 and page_size > 0:
                    return int(pages) * int(page_size)
            except (ValueError, OSError):
                pass
        return None
    except Exception:
        return None


def _gpu_info() -> dict:
    """Best-effort GPU discovery; always safe to be empty/unknown."""
    info: dict = {"present": False, "name": None, "vram_bytes": None,
                  "backend": None}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if out.returncode == 0 and out.stdout.strip():
            name, _, total = out.stdout.strip().splitlines()[0].partition(",")
            info["present"] = True
            info["name"] = name.strip() or None
            info["backend"] = "cuda"
            try:
                info["vram_bytes"] = int(float(total.strip()) * 1024 * 1024)
            except (ValueError, TypeError):
                pass
            return info
    except Exception:
        pass
    # Apple Silicon: unified memory, no discrete VRAM to report.
    try:
        if sys.platform == "darwin" and platform.machine().lower() == "arm64":
            info["present"] = True
            info["name"] = "Apple Silicon (unified memory)"
            info["backend"] = "metal"
            return info
    except Exception:
        pass
    return info


def _disk_info(path: str = "/") -> dict:
    try:
        usage = shutil.disk_usage(path)
        return {"total_bytes": usage.total, "free_bytes": usage.free}
    except Exception:
        return {"total_bytes": None, "free_bytes": None}


def detect_capabilities() -> dict:
    """Return the device capability profile (never raises, never assumes)."""
    total_ram = _total_ram_bytes()
    available_ram = _available_ram_bytes()
    disk = _disk_info()
    return {
        "os": platform.system() or None,
        "os_release": platform.release() or None,
        "architecture": platform.machine() or None,
        "cpu_cores": os.cpu_count(),
        "python_version": platform.python_version(),
        "ram_total_bytes": total_ram,
        "ram_available_bytes": available_ram,
        "gpu": _gpu_info(),
        "disk_total_bytes": disk["total_bytes"],
        "disk_free_bytes": disk["free_bytes"],
    }


def _gb(value: int | None) -> float | None:
    return (value / (1024 ** 3)) if isinstance(value, int) and value >= 0 else None


def evaluate_class(
    profile: dict,
    *,
    policy: dict | None = None,
) -> str:
    """Deterministically classify a profile: HIGH/MEDIUM/LOW/UNKNOWN.

    No LLM involved. Missing data degrades toward UNKNOWN, never guesses.
    """
    rules = dict(DEFAULT_POLICY)
    if policy:
        rules.update(policy)
    total = _gb(profile.get("ram_total_bytes"))
    available = _gb(profile.get("ram_available_bytes"))
    cores = profile.get("cpu_cores")
    if total is None or not isinstance(cores, int):
        # Without RAM size and core count there is no basis to judge.
        return CAPABILITY_UNKNOWN
    if (
        total >= rules["high_ram_total_gb"]
        and cores >= rules["high_cpu_cores"]
        and (available is None or available >= rules["high_ram_available_gb"])
    ):
        return CAPABILITY_HIGH
    if (
        total >= rules["medium_ram_total_gb"]
        and cores >= rules["medium_cpu_cores"]
        and (available is None or available >= rules["medium_ram_available_gb"])
    ):
        return CAPABILITY_MEDIUM
    return CAPABILITY_LOW
