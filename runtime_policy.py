"""Data-scale estimation and conservative execution policy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Set

from amr_backend import DatasetMetadata
from hardware_info import HardwareInfo
from i18n import tr_for


@dataclass
class DataScale:
    file_count: int = 0
    total_bytes: int = 0
    timestep_count: int = 0
    level_count: int = 0
    variable_count: int = 0
    dimension: int = 0
    estimated_timestep_bytes: int = 0
    storage_kind: str = "unknown"


def _unique_paths(metadata: DatasetMetadata) -> List[str]:
    paths: Set[str] = set()
    for timestep_data in metadata.sources.values():
        for level_paths in timestep_data.values():
            paths.update(level_paths)
    for path in paths:
        yield path


def _directory_size(path: str) -> tuple[int, int]:
    total = 0
    count = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                    count += 1
                except OSError:
                    continue
    except OSError:
        pass
    return count, total


def estimate_data_scale(metadata: DatasetMetadata, storage_kind: str = "unknown") -> DataScale:
    """Estimate input size without loading field arrays into memory."""

    file_count = 0
    total_bytes = 0
    if metadata.sources:
        for path in _unique_paths(metadata):
            try:
                total_bytes += os.path.getsize(path)
                file_count += 1
            except OSError:
                continue
    elif metadata.plotfiles:
        for path in metadata.plotfiles.values():
            count, size = _directory_size(path)
            file_count += count
            total_bytes += size

    timestep_count = len(metadata.timesteps)
    estimated_timestep_bytes = (
        int(total_bytes / timestep_count) if timestep_count else total_bytes
    )
    return DataScale(
        file_count=file_count,
        total_bytes=total_bytes,
        timestep_count=timestep_count,
        level_count=len(metadata.levels),
        variable_count=len(metadata.variables),
        dimension=metadata.dimension,
        estimated_timestep_bytes=estimated_timestep_bytes,
        storage_kind=storage_kind,
    )


def _memory_worker_cap(hardware: HardwareInfo, scale: DataScale) -> int:
    if hardware.available_memory_bytes <= 0 or scale.estimated_timestep_bytes <= 0:
        return 4
    estimated_peak = max(
        512 * 1024 * 1024,
        int(scale.estimated_timestep_bytes * (2.0 if scale.dimension == 3 else 1.5)),
    )
    usable = int(hardware.available_memory_bytes * 0.70)
    return max(1, usable // estimated_peak)


def recommend_workers(
    hardware: HardwareInfo,
    scale: DataScale,
    requested: int = 0,
    use_gpu: bool = False,
) -> int:
    """Return one worker count; zero means automatic selection."""

    task_cap = max(1, scale.timestep_count)
    if use_gpu:
        # The current renderer keeps one CUDA context and batches variables
        # inside that process. Multiple GPU processes would duplicate memory.
        return 1
    if requested > 0:
        return max(1, min(requested, task_cap))

    cpu_cap = max(1, hardware.physical_cores)
    dimension_cap = 4 if scale.dimension == 3 else 8
    kind = scale.storage_kind.lower()
    if kind in {"hdd", "hard disk drive"}:
        storage_cap = 2
    elif kind in {"ssd", "solid state drive"}:
        storage_cap = 6
    elif "nvme" in kind:
        storage_cap = 8
    else:
        storage_cap = 4

    return max(1, min(task_cap, cpu_cap, dimension_cap, storage_cap, _memory_worker_cap(hardware, scale)))


def format_data_scale(scale: DataScale, language: str | None = None) -> str:
    size_mb = scale.total_bytes / (1024 * 1024) if scale.total_bytes else 0
    timestep_mb = (
        scale.estimated_timestep_bytes / (1024 * 1024)
        if scale.estimated_timestep_bytes else 0
    )
    return "\n".join((
        tr_for(
            language,
            "Data: {files} files, {size:.1f} MB, {timesteps} timesteps, {levels} levels, {variables} variables, {dimension}D",
            files=scale.file_count,
            size=size_mb,
            timesteps=scale.timestep_count,
            levels=scale.level_count,
            variables=scale.variable_count,
            dimension=scale.dimension,
        ),
        tr_for(
            language,
            "Estimated timestep: {size:.1f} MB; storage: {storage}",
            size=timestep_mb,
            storage=scale.storage_kind,
        ),
    ))


__all__ = ["DataScale", "estimate_data_scale", "format_data_scale", "recommend_workers"]
