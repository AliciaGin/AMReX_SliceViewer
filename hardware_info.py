"""Hardware and runtime capability discovery for amrex_Viewer."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import psutil
except ImportError:  # Optional fallback for a minimal installation.
    psutil = None


@dataclass
class HardwareInfo:
    cpu_model: str = "Unknown"
    physical_cores: int = 1
    logical_cores: int = 1
    cpu_frequency_mhz: Optional[float] = None
    total_memory_bytes: int = 0
    available_memory_bytes: int = 0
    gpu_name: str = "Not detected"
    gpu_vendor: str = "Unknown"
    gpu_memory_bytes: int = 0
    gpu_driver: str = "Unknown"
    gpu_available: bool = False
    gpu_utilization_percent: Optional[float] = None
    storage_path: str = ""
    storage_device: str = "Unknown"
    storage_kind: str = "unknown"
    storage_total_bytes: int = 0
    storage_free_bytes: int = 0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _format_bytes(value: int) -> str:
    if value <= 0:
        return "unknown"
    units = ("B", "KB", "MB", "GB", "TB")
    number = float(value)
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{number:.1f} {unit}"
        number /= 1024
    return "unknown"


def _cpu_info() -> Dict[str, Any]:
    physical = os.cpu_count() or 1
    logical = os.cpu_count() or 1
    frequency = None
    if psutil is not None:
        physical = psutil.cpu_count(logical=False) or physical
        logical = psutil.cpu_count(logical=True) or logical
        try:
            current = psutil.cpu_freq()
            if current is not None:
                frequency = float(current.current or current.max or 0.0) or None
        except (OSError, RuntimeError):
            pass
    model = platform.processor() or platform.machine() or "Unknown"
    return {
        "cpu_model": model,
        "physical_cores": max(1, int(physical)),
        "logical_cores": max(1, int(logical)),
        "cpu_frequency_mhz": frequency,
    }


def _memory_info() -> Dict[str, int]:
    if psutil is not None:
        try:
            memory = psutil.virtual_memory()
            return {
                "total_memory_bytes": int(memory.total),
                "available_memory_bytes": int(memory.available),
            }
        except OSError:
            pass
    return {"total_memory_bytes": 0, "available_memory_bytes": 0}


def _run_nvidia_smi() -> Optional[Dict[str, str]]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    query = (
        "name,memory.total,driver_version,utilization.gpu,"
        "memory.used,temperature.gpu"
    )
    try:
        result = subprocess.run(
            [executable, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    fields = [item.strip() for item in result.stdout.splitlines()[0].split(",")]
    if len(fields) < 6:
        return None
    return dict(zip(("name", "memory_mb", "driver", "utilization", "used_mb", "temperature"), fields))


def _windows_storage_info(path: str) -> Dict[str, str]:
    if os.name != "nt":
        return {}
    drive = Path(path).anchor.rstrip("\\/")
    if len(drive) < 2 or drive[1] != ":":
        return {}
    letter = drive[0]
    command = (
        f"$p=Get-Partition -DriveLetter '{letter}' -ErrorAction SilentlyContinue; "
        "$p | Get-Disk | Select-Object -First 1 FriendlyName,MediaType "
        "| ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        payload = json.loads(result.stdout)
        if isinstance(payload, dict):
            return {
                "storage_device": str(payload.get("FriendlyName") or "Unknown"),
                "storage_kind": str(payload.get("MediaType") or "unknown").lower(),
            }
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return {}


def collect_hardware_info(data_path: str = "") -> HardwareInfo:
    """Collect read-only hardware and runtime information."""

    info = HardwareInfo()
    info.__dict__.update(_cpu_info())
    info.__dict__.update(_memory_info())

    gpu = _run_nvidia_smi()
    if gpu:
        info.gpu_available = True
        info.gpu_vendor = "NVIDIA"
        info.gpu_name = gpu["name"]
        info.gpu_driver = gpu["driver"]
        try:
            info.gpu_memory_bytes = int(float(gpu["memory_mb"]) * 1024 * 1024)
        except (TypeError, ValueError):
            pass
        try:
            info.gpu_utilization_percent = float(gpu["utilization"])
        except (TypeError, ValueError):
            pass
    else:
        info.notes = "未检测到可用 NVIDIA GPU；GPU 绘图后端将回退 CPU。"

    if data_path:
        try:
            resolved = str(Path(data_path).resolve())
            usage = shutil.disk_usage(resolved)
            info.storage_path = resolved
            info.storage_total_bytes = int(usage.total)
            info.storage_free_bytes = int(usage.free)
            info.__dict__.update(_windows_storage_info(resolved))
        except OSError as exc:
            info.notes = f"磁盘信息读取失败: {exc}"
    return info


def format_hardware_summary(info: HardwareInfo) -> str:
    cpu_frequency = (
        f"{info.cpu_frequency_mhz:.0f} MHz"
        if info.cpu_frequency_mhz else "unknown"
    )
    gpu_memory = _format_bytes(info.gpu_memory_bytes)
    memory = (
        f"{_format_bytes(info.available_memory_bytes)} available / "
        f"{_format_bytes(info.total_memory_bytes)} total"
    )
    disk = (
        f"{info.storage_kind}, {_format_bytes(info.storage_free_bytes)} free / "
        f"{_format_bytes(info.storage_total_bytes)} total"
    )
    return "\n".join(
        (
            f"CPU: {info.cpu_model} ({info.physical_cores}C/{info.logical_cores}T, {cpu_frequency})",
            f"Memory: {memory}",
            f"GPU: {info.gpu_name} ({gpu_memory}, driver {info.gpu_driver})",
            f"Disk: {info.storage_device} ({disk})",
            f"GPU backend: {'detected; array acceleration selectable' if info.gpu_available else 'CPU fallback'}",
        )
    )


__all__ = ["HardwareInfo", "collect_hardware_info", "format_hardware_summary"]
