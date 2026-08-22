"""Rendering and parallel batch generation for AMR datasets."""

from __future__ import annotations

import gc
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from amr_backend import (
    DatasetError,
    DatasetMetadata,
    Patch2D,
    ascii_cache_is_valid,
    build_patches,
    load_timestep,
    read_ascii_file,
)
from gpu_backend import ArrayBackend, create_backend


logger = logging.getLogger(__name__)
_ARRAY_BACKENDS = {}
_GPU_LUTS = {}


def _array_backend(config: "PlotConfig") -> ArrayBackend:
    """Reuse one CPU/GPU array backend per worker process."""

    prefer_gpu = bool(getattr(config, "use_gpu", False))
    if prefer_gpu not in _ARRAY_BACKENDS:
        _ARRAY_BACKENDS[prefer_gpu] = create_backend(prefer_gpu=prefer_gpu)
        status = _ARRAY_BACKENDS[prefer_gpu].status
        logger.info("Array backend: %s (%s)", status.name, status.device)
    return _ARRAY_BACKENDS[prefer_gpu]


def _upload_level_data(level_data, variables, backend: ArrayBackend):
    """Upload all selected variables of each zone in one contiguous transfer."""

    if not backend.status.is_gpu:
        return {}
    uploaded = {}
    total_bytes = 0
    for zones in level_data.values():
        for zone in zones:
            entries = []
            for variable in variables:
                values = zone.data.get(variable)
                if values is None:
                    continue
                array = np.ascontiguousarray(values, dtype=np.float32)
                entries.append((variable, array, array.shape))
            if not entries:
                continue
            host_buffer = (
                entries[0][1].reshape(-1)
                if len(entries) == 1
                else np.concatenate([item[1].reshape(-1) for item in entries])
            )
            device_buffer = backend.asarray(host_buffer)
            offset = 0
            for variable, array, shape in entries:
                size = int(array.size)
                uploaded[(id(zone), variable)] = device_buffer[offset:offset + size].reshape(shape)
                offset += size
                total_bytes += int(array.nbytes)
    logger.info(
        "Uploaded %d variables in one batch: %.1f MB",
        len(uploaded),
        total_bytes / (1024 * 1024),
    )
    return uploaded


def _gpu_lut(cmap, backend: ArrayBackend):
    key = (backend.status.device, cmap.name)
    if key not in _GPU_LUTS:
        _GPU_LUTS[key] = backend.asarray(
            np.asarray(cmap(np.linspace(0.0, 1.0, 1024)), dtype=np.float32)
        )
    return _GPU_LUTS[key]


def _prepare_ascii_cache_one(args):
    """Parse only cache-missing ASCII files; never creates a CUDA context."""

    metadata, timestep, config = args
    cache_dir = Path(metadata.root) / ".amrex_viewer_cache"
    levels = config.selected_levels or metadata.levels
    sources = metadata.sources.get(timestep, {})
    prepared = 0
    for level in levels:
        for filepath in sources.get(level, []):
            if ascii_cache_is_valid(filepath, level, cache_dir):
                continue
            read_ascii_file(
                filepath,
                config.variables,
                level,
                cache_dir=cache_dir,
            )
            prepared += 1
    return timestep, prepared


def _prepare_ascii_cache_for_gpu(
    metadata: DatasetMetadata,
    timesteps: List[int],
    config: "PlotConfig",
    progress_callback=None,
):
    """Warm missing ASCII caches with bounded CPU parallelism before GPU work."""

    if (
        not getattr(config, "use_gpu", False)
        or not getattr(config, "use_ascii_cache", True)
        or metadata.source_format != "tecplot_ascii"
        or not timesteps
    ):
        return False

    cache_dir = Path(metadata.root) / ".amrex_viewer_cache"
    # Do not scan thousands of cache files on every run. Existing caches are
    # loaded lazily and invalid files are rebuilt by the normal reader.
    if cache_dir.exists() and any(cache_dir.glob("*.npz")):
        return False

    tasks = [(metadata, timestep, config) for timestep in timesteps]

    # Two CPU readers are a safe starting point for HDD input and avoid
    # multiplying the same ASCII stream across too many processes.
    worker_count = max(1, min(2, len(tasks), os.cpu_count() or 1))
    completed = 0
    with ProcessPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(_prepare_ascii_cache_one, task) for task in tasks]
        for future in as_completed(futures):
            future.result()
            completed += 1
            if progress_callback:
                progress_callback(completed / len(tasks))
    return True

if "redblue" not in plt.colormaps():
    plt.colormaps.register(cmap=plt.get_cmap("RdBu_r"), name="redblue")

PREFERRED_COLORMAPS = [
    "redblue", "turbo", "viridis", "plasma", "inferno", "magma", "cividis",
    "coolwarm", "seismic", "bwr", "RdBu_r", "RdYlBu_r", "Spectral_r",
    "jet", "rainbow", "nipy_spectral", "gist_ncar", "gnuplot", "gnuplot2",
    "Blues", "Reds", "Greens", "Purples", "Oranges", "Greys",
    "hot", "afmhot", "gist_heat", "gray", "binary", "bone",
    "terrain", "ocean", "gist_earth", "cubehelix",
    "viridis_r", "turbo_r", "gray_r", "hot_r", "RdBu", "Spectral",
]
COLORMAPS = PREFERRED_COLORMAPS + [
    name for name in sorted(plt.colormaps()) if name not in PREFERRED_COLORMAPS
]


@dataclass
class PlotConfig:
    variables: List[str] = field(default_factory=list)
    selected_levels: List[int] = field(default_factory=list)
    dimension: int = 0  # 0=auto, 2 or 3
    slice_axis: str = "z"
    slice_position: Optional[float] = None
    spatial_bounds: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    colormap: str = "redblue"
    vmin: Optional[float] = None
    vmax: Optional[float] = None
    norm_type: str = "linear"  # linear or log
    symmetric_color_range: bool = False
    global_color_range: bool = False
    color_ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    dpi: int = 600
    font_family: str = "Times New Roman"
    font_size: int = 12
    x_label: str = ""
    y_label: str = ""
    show_frame: bool = True
    show_title: bool = True
    show_colorbar: bool = True
    figsize: tuple = (12, 6)

    plot_mode: str = "contourf"  # contourf, contour, or both
    contour_levels: int = 20
    contour_linewidth: float = 0.5
    contour_color: str = "black"
    show_patch_edges: bool = False
    patch_edge_color: str = "#222222"
    patch_edge_linewidth: float = 0.35
    patch_edge_alpha: float = 0.55

    fps: int = 10
    output_type: str = "image"
    image_format: str = "png"
    resume: bool = True
    use_gpu: bool = True
    use_ascii_cache: bool = True
    video_format: str = "mp4"
    video_formats: List[str] = field(default_factory=lambda: ["mp4"])
    output_dir: str = ""
    num_workers: int = 32


def _crop_patch(
    patch: Patch2D,
    x_bounds: Optional[Tuple[float, float]],
    y_bounds: Optional[Tuple[float, float]],
) -> Optional[Patch2D]:
    """Keep cells intersecting the requested output rectangle."""

    x_low, x_high = x_bounds or (float(patch.x_edges[0]), float(patch.x_edges[-1]))
    y_low, y_high = y_bounds or (float(patch.y_edges[0]), float(patch.y_edges[-1]))
    x_cells = np.flatnonzero(
        (patch.x_edges[:-1] < x_high) & (patch.x_edges[1:] > x_low)
    )
    y_cells = np.flatnonzero(
        (patch.y_edges[:-1] < y_high) & (patch.y_edges[1:] > y_low)
    )
    if x_cells.size == 0 or y_cells.size == 0:
        return None

    ix0, ix1 = int(x_cells[0]), int(x_cells[-1]) + 1
    iy0, iy1 = int(y_cells[0]), int(y_cells[-1]) + 1
    return Patch2D(
        level=patch.level,
        x_edges=patch.x_edges[ix0:ix1 + 1],
        y_edges=patch.y_edges[iy0:iy1 + 1],
        values=patch.values[iy0:iy1, ix0:ix1],
        x_label=patch.x_label,
        y_label=patch.y_label,
    )


def _crop_patches(patches: List[Patch2D], config: PlotConfig) -> List[Patch2D]:
    if not patches or not config.spatial_bounds:
        return patches
    cropped = []
    for patch in patches:
        item = _crop_patch(
            patch,
            config.spatial_bounds.get(patch.x_label),
            config.spatial_bounds.get(patch.y_label),
        )
        if item is not None:
            cropped.append(item)
    return cropped


def _build_contour_composite(
    patches: List[Patch2D],
    max_cells=12_000_000,
    backend: Optional[ArrayBackend] = None,
    keep_device: bool = False,
):
    """Compose AMR patches on one grid so contours cross patch boundaries."""

    xmin = min(float(patch.x_edges[0]) for patch in patches)
    xmax = max(float(patch.x_edges[-1]) for patch in patches)
    ymin = min(float(patch.y_edges[0]) for patch in patches)
    ymax = max(float(patch.y_edges[-1]) for patch in patches)
    dx = min(float(np.min(np.diff(patch.x_edges))) for patch in patches)
    dy = min(float(np.min(np.diff(patch.y_edges))) for patch in patches)
    nx = max(1, int(round((xmax - xmin) / dx)))
    ny = max(1, int(round((ymax - ymin) / dy)))

    cell_count = nx * ny
    if cell_count > max_cells:
        scale = (cell_count / max_cells) ** 0.5
        nx = max(1, int(nx / scale))
        ny = max(1, int(ny / scale))

    x_edges = np.linspace(xmin, xmax, nx + 1)
    y_edges = np.linspace(ymin, ymax, ny + 1)
    x_centers = 0.5 * (x_edges[:-1] + x_edges[1:])
    y_centers = 0.5 * (y_edges[:-1] + y_edges[1:])
    composite = np.full((ny, nx), np.nan, dtype=np.float32)
    device_composite = (
        backend.asarray(composite)
        if backend is not None and backend.status.is_gpu
        else composite
    )

    for patch in sorted(patches, key=lambda item: item.level):
        ix0 = int(np.searchsorted(x_centers, patch.x_edges[0], side="left"))
        ix1 = int(np.searchsorted(x_centers, patch.x_edges[-1], side="left"))
        iy0 = int(np.searchsorted(y_centers, patch.y_edges[0], side="left"))
        iy1 = int(np.searchsorted(y_centers, patch.y_edges[-1], side="left"))
        if ix0 >= ix1 or iy0 >= iy1:
            continue

        source_x = np.searchsorted(
            patch.x_edges, x_centers[ix0:ix1], side="right"
        ) - 1
        source_y = np.searchsorted(
            patch.y_edges, y_centers[iy0:iy1], side="right"
        ) - 1
        source_x = np.clip(source_x, 0, patch.values.shape[1] - 1)
        source_y = np.clip(source_y, 0, patch.values.shape[0] - 1)
        if backend is not None and backend.status.is_gpu:
            sampled = patch.values[
                backend.cp.ix_(
                    backend.asarray(source_y), backend.asarray(source_x)
                )
            ]
        else:
            sampled = patch.values[np.ix_(source_y, source_x)]
        device_composite[iy0:iy1, ix0:ix1] = (
            backend.asarray(sampled)
            if backend is not None and backend.status.is_gpu
            else sampled
        )

    if backend is not None and backend.status.is_gpu and not keep_device:
        composite = backend.to_cpu(device_composite)
    elif backend is not None and backend.status.is_gpu and keep_device:
        composite = device_composite

    if backend is not None and backend.status.is_gpu and keep_device:
        return x_centers, y_centers, composite
    return x_centers, y_centers, np.ma.masked_invalid(composite)


def _finite_values(patches: List[Patch2D], config: PlotConfig):
    values = []
    for patch in patches:
        data = patch.values[np.isfinite(patch.values)]
        if config.norm_type == "log":
            data = data[data > 0]
        if data.size:
            values.append(data)
    return values


def _value_range(patches: List[Patch2D], config: PlotConfig, var_name: str = ""):
    if var_name and var_name in config.color_ranges:
        return config.color_ranges[var_name]
    if config.vmin is not None and config.vmax is not None:
        return config.vmin, config.vmax
    backend = _array_backend(config)
    result = backend.minmax(
        (patch.values for patch in patches),
        positive_only=config.norm_type == "log",
    )
    if result is None:
        return (1.0e-12, 1.0) if config.norm_type == "log" else (0.0, 1.0)
    vmin = config.vmin
    vmax = config.vmax
    if vmin is None:
        vmin = result[0]
    if vmax is None:
        vmax = result[1]
    if config.symmetric_color_range and config.norm_type != "log":
        limit = max(abs(vmin), abs(vmax))
        vmin, vmax = -limit, limit
    if config.norm_type == "log":
        tiny = np.finfo(float).tiny
        vmin = max(float(vmin), tiny)
        vmax = max(float(vmax), vmin * 1.0e-12)
    if vmin == vmax:
        vmax = vmin + 1.0e-12
    return vmin, vmax


def _normalizer(vmin: float, vmax: float, config: PlotConfig):
    if config.norm_type == "log":
        return mcolors.LogNorm(vmin=max(vmin, np.finfo(float).tiny), vmax=vmax)
    return mcolors.Normalize(vmin=vmin, vmax=vmax)


def _gpu_colorize(
    values: np.ndarray,
    vmin: float,
    vmax: float,
    config: PlotConfig,
    cmap,
    backend: ArrayBackend,
    lut=None,
) -> np.ndarray:
    """Map one patch to RGBA on the GPU before Matplotlib writes the image."""

    cp = backend.cp
    data = backend.asarray(values)
    valid = cp.isfinite(data)
    if config.norm_type == "log":
        valid &= data > 0
        lower = np.log(max(vmin, np.finfo(float).tiny))
        upper = np.log(max(vmax, vmin * (1.0 + 1.0e-12)))
        normalized = (cp.log(cp.maximum(data, vmin)) - lower) / (upper - lower)
    else:
        normalized = (data - vmin) / (vmax - vmin)

    lut = lut if lut is not None else _gpu_lut(cmap, backend)
    indices = cp.clip((normalized * 1023.0).astype(cp.int32), 0, 1023)
    rgba = lut[indices].copy()
    rgba[~valid] = 0.0
    return backend.to_cpu(rgba)


def _render_values(values: np.ndarray, config: PlotConfig):
    if config.norm_type == "log":
        return np.ma.masked_where((values <= 0) | ~np.isfinite(values), values)
    return values


def _add_patch_edges(ax, patches: List[Patch2D], config: PlotConfig):
    if not config.show_patch_edges:
        return
    for patch in patches:
        x0 = float(patch.x_edges[0])
        y0 = float(patch.y_edges[0])
        width = float(patch.x_edges[-1] - patch.x_edges[0])
        height = float(patch.y_edges[-1] - patch.y_edges[0])
        ax.add_patch(
            mpatches.Rectangle(
                (x0, y0),
                width,
                height,
                fill=False,
                edgecolor=config.patch_edge_color,
                linewidth=config.patch_edge_linewidth,
                alpha=config.patch_edge_alpha,
                zorder=patch.level + 0.2,
            )
        )


def _safe_filename_part(value):
    if value is None:
        return ""
    return str(value).replace("-", "m").replace(".", "p").replace("+", "")


def _output_filename(var_name: str, timestep: int, config: PlotConfig, extension: str):
    parts = [var_name]
    if config.dimension == 3:
        parts.extend([config.slice_axis, _safe_filename_part(f"{config.slice_position:g}")])
    parts.append(f"{timestep:06d}")
    return "_".join(part for part in parts if part) + f".{extension}"


def _frame_path(var_name: str, timestep: int, config: PlotConfig) -> str:
    extension = "png" if config.output_type == "video" else config.image_format
    filename = _output_filename(var_name, timestep, config, extension)
    return os.path.join(config.output_dir, var_name, filename)


def _valid_output(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def render_plot(
    level_data,
    var_name: str,
    config: PlotConfig,
    save_path: str,
    solutiontime: Optional[float] = None,
    gpu_values=None,
):
    dimension = config.dimension or 2
    if dimension == 3 and config.slice_position is not None:
        normal_bounds = config.spatial_bounds.get(config.slice_axis)
        if normal_bounds and not normal_bounds[0] <= config.slice_position <= normal_bounds[1]:
            raise DatasetError(
                f"切片坐标 {config.slice_position:g} 不在所设 "
                f"{config.slice_axis.upper()} 范围 "
                f"[{normal_bounds[0]:g}, {normal_bounds[1]:g}] 内"
            )
    patches = build_patches(
        level_data,
        var_name,
        dimension,
        config.slice_axis,
        config.slice_position,
        value_overrides=gpu_values,
    )
    patches = _crop_patches(patches, config)
    if not patches:
        raise DatasetError("所选空间输出范围内没有可绘制数据")

    backend = _array_backend(config)
    vmin, vmax = _value_range(patches, config, var_name)
    norm = _normalizer(vmin, vmax, config)
    cmap = plt.get_cmap(config.colormap)

    plt.rcParams["font.family"] = config.font_family
    plt.rcParams["font.size"] = config.font_size
    fig, ax = plt.subplots(figsize=config.figsize)
    mappable = None
    gpu_composite = None
    gpu_contour_x = gpu_contour_y = None

    if config.plot_mode in ("contourf", "both"):
        if backend.status.is_gpu:
            gpu_contour_x, gpu_contour_y, gpu_composite = _build_contour_composite(
                patches, backend=backend, keep_device=True
            )
            rgba = _gpu_colorize(
                gpu_composite,
                vmin,
                vmax,
                config,
                cmap,
                backend,
                lut=_gpu_lut(cmap, backend),
            )
            ax.imshow(
                rgba,
                extent=(
                    float(min(patch.x_edges[0] for patch in patches)),
                    float(max(patch.x_edges[-1] for patch in patches)),
                    float(min(patch.y_edges[0] for patch in patches)),
                    float(max(patch.y_edges[-1] for patch in patches)),
                ),
                origin="lower",
                interpolation="nearest",
                aspect="auto",
                zorder=max(patch.level for patch in patches),
            )
            mappable = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
            mappable.set_array(np.asarray([vmin, vmax], dtype=np.float32))
        else:
            for patch in sorted(patches, key=lambda item: item.level):
                # Preserve the original cell-centered field on the CPU path.
                mappable = ax.pcolormesh(
                    patch.x_edges,
                    patch.y_edges,
                    _render_values(patch.values, config),
                    cmap=cmap,
                    norm=norm,
                    shading="flat",
                    edgecolors="none",
                    antialiased=False,
                    rasterized=True,
                    zorder=patch.level,
                )
    if config.plot_mode in ("contour", "both"):
        levels = np.linspace(vmin, vmax, config.contour_levels)
        if backend.status.is_gpu and gpu_composite is not None:
            contour_x = gpu_contour_x
            contour_y = gpu_contour_y
            contour_values = np.ma.masked_invalid(backend.to_cpu(gpu_composite))
        else:
            contour_x, contour_y, contour_values = _build_contour_composite(
                patches, backend=backend
            )
        if config.norm_type == "log":
            contour_values = np.ma.masked_where(contour_values <= 0, contour_values)
            levels = np.geomspace(vmin, vmax, config.contour_levels)
        contour_set = ax.contour(
            contour_x,
            contour_y,
            contour_values,
            levels=levels,
            colors=config.contour_color,
            linewidths=config.contour_linewidth,
            zorder=max(patch.level for patch in patches) + 0.1,
        )
        if config.plot_mode == "contour":
            mappable = contour_set

    _add_patch_edges(ax, patches, config)

    if config.show_colorbar and mappable is not None:
        colorbar = fig.colorbar(mappable, ax=ax, shrink=0.9, pad=0.02)
        colorbar.ax.tick_params(labelsize=config.font_size - 2)

    x_label = patches[0].x_label
    y_label = patches[0].y_label
    xmin = config.spatial_bounds.get(
        x_label, (min(float(patch.x_edges[0]) for patch in patches), 0.0)
    )[0]
    xmax = config.spatial_bounds.get(
        x_label, (0.0, max(float(patch.x_edges[-1]) for patch in patches))
    )[1]
    ymin = config.spatial_bounds.get(
        y_label, (min(float(patch.y_edges[0]) for patch in patches), 0.0)
    )[0]
    ymax = config.spatial_bounds.get(
        y_label, (0.0, max(float(patch.y_edges[-1]) for patch in patches))
    )[1]
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")
    ax.set_xlabel(config.x_label or patches[0].x_label, fontsize=config.font_size)
    ax.set_ylabel(config.y_label or patches[0].y_label, fontsize=config.font_size)

    if config.show_title:
        title = var_name
        if dimension == 3:
            title += f"  ({config.slice_axis.upper()}={config.slice_position:g})"
        if solutiontime is not None:
            title += f"  (t={solutiontime:.6g})"
        ax.set_title(title, fontsize=config.font_size + 2)
    ax.tick_params(labelsize=config.font_size - 2)
    if not config.show_frame:
        ax.axis("off")

    # tight_layout already accounts for labels and the colorbar. Avoiding a
    # second bbox_inches='tight' pass reduces per-frame CPU layout work.
    fig.tight_layout()
    save_kwargs = {
        "dpi": config.dpi,
        "bbox_inches": None,
    }
    if config.image_format == "png":
        # PNG remains lossless; a lower compression level reduces CPU time for
        # large frame batches at the cost of larger intermediate files.
        save_kwargs["pil_kwargs"] = {"compress_level": 1}
    fig.savefig(save_path, **save_kwargs)
    plt.close(fig)
    del patches
    return True


def _process_one_timestep(args):
    metadata, timestep, config = args
    levels = config.selected_levels or metadata.levels

    expected_paths = {
        var_name: _frame_path(var_name, timestep, config)
        for var_name in config.variables
    }
    if config.resume and all(_valid_output(path) for path in expected_paths.values()):
        return timestep, expected_paths

    level_data = load_timestep(
        metadata,
        timestep,
        config.variables,
        levels,
        use_cache=getattr(config, "use_ascii_cache", True),
    )
    if not level_data:
        return timestep, {}

    solutiontime = None
    for zones in level_data.values():
        if zones:
            solutiontime = zones[0].solutiontime
            break

    results: Dict[str, str] = {}
    gpu_values = _upload_level_data(
        level_data,
        config.variables,
        _array_backend(config),
    )
    for var_name in config.variables:
        save_path = expected_paths[var_name]
        if config.resume and _valid_output(save_path):
            results[var_name] = save_path
            continue
        try:
            render_plot(
                level_data,
                var_name,
                config,
                save_path,
                solutiontime,
                gpu_values=gpu_values,
            )
            results[var_name] = save_path
        except Exception as exc:
            logger.exception("Render failed: variable=%s timestep=%s", var_name, timestep)
            results[var_name] = f"ERROR: {exc}"

    del level_data
    del gpu_values
    gc.collect()
    return timestep, results


def _worker_count(config: PlotConfig, task_count: int):
    if getattr(config, "use_gpu", False):
        backend = _array_backend(config)
        if backend.status.is_gpu:
            # One process owns the CUDA context; variables remain batched inside it.
            return max(1, min(1, task_count))
    if config.num_workers and config.num_workers > 0:
        return max(1, min(config.num_workers, task_count))
    default = 32
    return max(1, min(default, os.cpu_count() or 1, task_count))


def _compute_timestep_color_ranges(args):
    metadata, timestep, config = args
    levels = config.selected_levels or metadata.levels
    level_data = load_timestep(
        metadata,
        timestep,
        config.variables,
        levels,
        use_cache=getattr(config, "use_ascii_cache", True),
    )
    backend = _array_backend(config)
    gpu_values = _upload_level_data(level_data, config.variables, backend)
    ranges = {}
    for var_name in config.variables:
        patches = build_patches(
            level_data,
            var_name,
            config.dimension or metadata.dimension,
            config.slice_axis,
            config.slice_position,
            value_overrides=gpu_values,
        )
        patches = _crop_patches(patches, config)
        result = backend.minmax(
            (patch.values for patch in patches),
            positive_only=config.norm_type == "log",
        )
        if result is not None:
            ranges[var_name] = result
        del patches
    del level_data
    del gpu_values
    gc.collect()
    return timestep, ranges


def _compute_global_color_ranges(
    metadata: DatasetMetadata,
    timesteps: List[int],
    config: PlotConfig,
    progress_callback=None,
):
    if not config.global_color_range or (config.vmin is not None and config.vmax is not None):
        return

    tasks = [(metadata, timestep, config) for timestep in timesteps]
    if not tasks:
        return

    ranges = {
        var_name: [config.vmin, config.vmax]
        for var_name in config.variables
    }
    errors = []
    completed = 0
    worker_count = _worker_count(config, len(tasks))
    with ProcessPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(_compute_timestep_color_ranges, task): task for task in tasks}
        for future in as_completed(futures):
            _, timestep, _ = futures[future]
            try:
                _, timestep_ranges = future.result()
            except Exception as exc:
                logger.exception("Color range failed: timestep=%s", timestep)
                errors.append(f"时间步 {timestep}: {exc}")
            else:
                for variable, (local_min, local_max) in timestep_ranges.items():
                    current_min, current_max = ranges[variable]
                    ranges[variable][0] = (
                        local_min if current_min is None else min(current_min, local_min)
                    )
                    ranges[variable][1] = (
                        local_max if current_max is None else max(current_max, local_max)
                    )
            completed += 1
            if progress_callback:
                progress_callback(completed / len(tasks))

    if errors:
        details = "\n".join(errors[:5])
        if len(errors) > 5:
            details += f"\n其余 {len(errors) - 5} 项错误已省略"
        raise DatasetError(f"统一色标扫描失败:\n{details}")

    final_ranges = {}
    for var_name, (min_value, max_value) in ranges.items():
        if min_value is None or max_value is None:
            continue
        if config.symmetric_color_range and config.norm_type != "log":
            limit = max(abs(min_value), abs(max_value))
            min_value, max_value = -limit, limit
        if min_value == max_value:
            max_value = min_value + 1.0e-12
        final_ranges[var_name] = (min_value, max_value)
    ranges = final_ranges
    config.color_ranges = ranges


def _write_render_config(
    metadata: DatasetMetadata,
    timesteps: List[int],
    config: PlotConfig,
):
    payload = {
        "dataset": {
            "root": metadata.root,
            "format": metadata.format_label,
            "dimension": metadata.dimension,
            "timesteps": timesteps,
            "levels": metadata.levels,
        },
        "config": asdict(config),
    }
    path = os.path.join(config.output_dir, "render_config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def generate_images(
    metadata: DatasetMetadata,
    timesteps: List[int],
    config: PlotConfig,
    progress_callback=None,
):
    for var_name in config.variables:
        os.makedirs(os.path.join(config.output_dir, var_name), exist_ok=True)

    tasks = [(metadata, timestep, config) for timestep in timesteps]
    if not tasks:
        return {name: [] for name in config.variables}

    worker_count = _worker_count(config, len(tasks))
    results_by_timestep = {}
    completed = 0
    with ProcessPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(_process_one_timestep, task): task[1]
            for task in tasks
        }
        for future in as_completed(futures):
            timestep = futures[future]
            try:
                _, results = future.result()
            except Exception as exc:
                logger.exception("Timestep %s failed", timestep)
                results = {name: f"ERROR: {exc}" for name in config.variables}
            results_by_timestep[timestep] = results
            completed += 1
            if progress_callback:
                progress_callback(completed / len(tasks))

    output = {name: [] for name in config.variables}
    errors = []
    for timestep in timesteps:
        timestep_result = results_by_timestep.get(timestep, {})
        for name in config.variables:
            path = timestep_result.get(name)
            if path and path.startswith("ERROR:"):
                errors.append(f"时间步 {timestep}，变量 {name}: {path[6:].strip()}")
                output[name].append(None)
            elif path:
                output[name].append(path)
            else:
                errors.append(f"时间步 {timestep}，变量 {name}: 未生成结果")
                output[name].append(None)
    if errors:
        details = "\n".join(errors[:5])
        if len(errors) > 5:
            details += f"\n其余 {len(errors) - 5} 项错误已省略"
        raise DatasetError(f"生成过程中有 {len(errors)} 项失败:\n{details}")
    return output


def _normalize_video_format(video_format: str) -> str:
    value = (video_format or "mp4").strip().lower()
    if value == "jif":
        value = "gif"
    if value not in {"mp4", "gif"}:
        raise DatasetError(f"不支持的视频格式: {video_format}")
    return value


def _selected_video_formats(config: PlotConfig) -> List[str]:
    formats = list(config.video_formats or [])
    if not formats:
        if config.video_format == "both":
            formats = ["mp4", "gif"]
        else:
            formats = [config.video_format]
    normalized = []
    for video_format in formats:
        value = _normalize_video_format(video_format)
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise DatasetError("请至少选择一种视频格式")
    return normalized


def _with_video_extension(output_path: str, video_format: str) -> str:
    extension = f".{video_format}"
    root, current_extension = os.path.splitext(output_path)
    if current_extension.lower() in {".mp4", ".gif", ".jif", ".avi"}:
        output_path = root
    return output_path + extension


def _generate_mp4(image_paths, output_path, fps):
    first = cv2.imread(image_paths[0])
    if first is None:
        raise DatasetError(f"无法读取首帧图片: {image_paths[0]}")
    height, width = first.shape[:2]
    writer = cv2.VideoWriter(
        output_path,
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise DatasetError("MP4 视频写入器打开失败，请检查 opencv-python 安装和本机编码器支持")

    written = 0
    for path in image_paths:
        image = cv2.imread(path)
        if image is None:
            continue
        if image.shape[:2] != (height, width):
            image = cv2.resize(image, (width, height))
        writer.write(image)
        written += 1
    writer.release()
    if written == 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise DatasetError("MP4 视频写入失败，没有生成有效文件")


def _generate_gif(image_paths, output_path, fps):
    try:
        from PIL import Image
    except ImportError as exc:
        raise DatasetError("生成 GIF/JIF 需要安装 Pillow，请先运行: pip install Pillow") from exc

    frames = []
    base_size = None
    for path in image_paths:
        try:
            image = Image.open(path).convert("RGB")
        except OSError:
            continue
        if base_size is None:
            base_size = image.size
        elif image.size != base_size:
            image = image.resize(base_size)
        frames.append(image)
    if not frames:
        raise DatasetError("GIF/JIF 生成失败，没有可用帧")

    duration_ms = max(1, int(round(1000 / max(1, fps))))
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=False,
    )
    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise DatasetError("GIF/JIF 写入失败，没有生成有效文件")


def generate_video(image_paths, output_path, fps=10, video_format="mp4"):
    video_format = _normalize_video_format(video_format)
    existing = [path for path in image_paths if path and os.path.isfile(path)]
    if not existing:
        raise DatasetError("没有可用于生成视频的图片帧")
    output_path = _with_video_extension(output_path, video_format)
    if video_format == "mp4":
        _generate_mp4(existing, output_path, fps)
    else:
        _generate_gif(existing, output_path, fps)
    return output_path


def generate_all(
    metadata: DatasetMetadata,
    timesteps: List[int],
    config: PlotConfig,
    progress_callback=None,
):
    config.dimension = config.dimension or metadata.dimension
    cache_prepared = _prepare_ascii_cache_for_gpu(
        metadata,
        timesteps,
        config,
        progress_callback=(
            (lambda value: progress_callback(value * 0.1))
            if progress_callback else None
        ),
    )
    progress_offset = 0.1 if cache_prepared else 0.0
    has_global_scan = config.global_color_range and not (
        config.vmin is not None and config.vmax is not None
    )
    if has_global_scan:
        _compute_global_color_ranges(
            metadata,
            timesteps,
            config,
            progress_callback=(
                (lambda value: progress_callback(progress_offset + value * 0.2))
                if progress_callback else None
            ),
        )
    else:
        _compute_global_color_ranges(metadata, timesteps, config)
    _write_render_config(metadata, timesteps, config)
    image_map = generate_images(
        metadata,
        timesteps,
        config,
        progress_callback=(
            (
                lambda value: progress_callback(
                    progress_offset + (0.2 if has_global_scan else 0.0)
                    + value * (0.8 if has_global_scan else 0.9)
                )
            )
            if progress_callback and (cache_prepared or has_global_scan)
            else progress_callback
        ),
    )
    if config.output_type != "video":
        return image_map

    videos = {}
    formats = _selected_video_formats(config)
    for variable, paths in image_map.items():
        output_path = os.path.join(config.output_dir, variable)
        videos[variable] = {
            video_format: generate_video(paths, output_path, config.fps, video_format)
            for video_format in formats
        }
    return videos
