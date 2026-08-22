"""Compatibility exports for the unified AMR backend."""

from amr_backend import (  # noqa: F401
    DatasetError,
    DatasetMetadata,
    Patch2D,
    UnsupportedFormatError,
    ZoneData,
    axis_bounds,
    build_patches,
    compute_bounds,
    detect_dataset_format,
    extract_patch,
    load_timestep,
    read_ascii_file,
    scan_dataset,
)


def scan_folder(folder_path):
    """Legacy four-value return used by older callers."""
    metadata = scan_dataset(folder_path)
    return metadata.sources, metadata.variables, metadata.timesteps, metadata.levels
