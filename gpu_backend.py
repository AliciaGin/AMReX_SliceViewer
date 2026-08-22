"""Optional GPU array backend with a NumPy fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

import numpy as np


@dataclass(frozen=True)
class BackendStatus:
    name: str
    device: str
    is_gpu: bool
    reason: str = ""


class ArrayBackend:
    """Small common interface for CPU/GPU array operations."""

    status = BackendStatus("numpy", "CPU", False)

    def asarray(self, value):
        return np.asarray(value)

    def to_cpu(self, value):
        return np.asarray(value)

    def minmax(
        self, arrays: Iterable, positive_only: bool = False
    ) -> Optional[Tuple[float, float]]:
        minimum = None
        maximum = None
        for array in arrays:
            values = self.asarray(array)
            finite = values[np.isfinite(values)]
            if positive_only:
                finite = finite[finite > 0]
            if finite.size == 0:
                continue
            local_min = float(self.to_cpu(finite.min()))
            local_max = float(self.to_cpu(finite.max()))
            minimum = local_min if minimum is None else min(minimum, local_min)
            maximum = local_max if maximum is None else max(maximum, local_max)
        return None if minimum is None else (minimum, maximum)


class CupyBackend(ArrayBackend):
    def __init__(self, cupy_module, device_id: int = 0):
        self.cp = cupy_module
        self.device_id = device_id
        with self.cp.cuda.Device(device_id):
            name = self.cp.cuda.runtime.getDeviceProperties(device_id)["name"]
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        self.status = BackendStatus("cupy", str(name), True)

    def asarray(self, value):
        return self.cp.asarray(value)

    def to_cpu(self, value):
        return self.cp.asnumpy(value)

    def minmax(
        self, arrays: Iterable, positive_only: bool = False
    ) -> Optional[Tuple[float, float]]:
        minimum = None
        maximum = None
        for array in arrays:
            values = self.asarray(array)
            finite = values[self.cp.isfinite(values)]
            if positive_only:
                finite = finite[finite > 0]
            if finite.size == 0:
                continue
            local_min = float(self.to_cpu(self.cp.min(finite)))
            local_max = float(self.to_cpu(self.cp.max(finite)))
            minimum = local_min if minimum is None else min(minimum, local_min)
            maximum = local_max if maximum is None else max(maximum, local_max)
        return None if minimum is None else (minimum, maximum)


def create_backend(prefer_gpu: bool = True) -> ArrayBackend:
    """Create one backend; GPU use is opt-in and always has a CPU fallback."""

    if not prefer_gpu:
        backend = ArrayBackend()
        backend.status = BackendStatus("numpy", "CPU", False, "GPU disabled by user")
        return backend
    try:
        import cupy as cp
        device_count = int(cp.cuda.runtime.getDeviceCount())
        if device_count <= 0:
            raise RuntimeError("no CUDA device")
        return CupyBackend(cp)
    except Exception as exc:
        backend = ArrayBackend()
        backend.status = BackendStatus(
            "numpy", "CPU", False, f"GPU fallback: {exc}"
        )
        return backend


__all__ = ["ArrayBackend", "BackendStatus", "CupyBackend", "create_backend"]
