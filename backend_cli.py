#!/usr/bin/env python3
"""Command-line entry point for AMR post-processing."""

from __future__ import annotations

import argparse
import os
import sys

from amr_backend import (
    DatasetError,
    UnsupportedFormatError,
    compute_bounds,
    scan_dataset,
)
from hardware_info import collect_hardware_info
from gpu_backend import create_backend
from i18n import get_language_preference, set_language, tr
from platform_support import default_plot_font
from runtime_policy import estimate_data_scale, recommend_workers
from visualizer import PlotConfig, generate_all


def _configure_console_encoding():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _csv_values(value, cast=str):
    if not value:
        return []
    return [cast(item.strip()) for item in value.split(",") if item.strip()]


def _video_formats(value):
    value = (value or "mp4").strip().lower()
    if value == "both":
        return ["mp4", "gif"]
    if value == "jif":
        return ["gif"]
    return [value]


def _parse_timesteps(spec, available):
    if spec in (None, "", "all"):
        return available
    selected = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = (int(value) for value in part.split("-", 1))
            selected.extend(step for step in available if start <= step <= end)
        else:
            value = int(part)
            if value in available:
                selected.append(value)
    return sorted(set(selected))


def print_metadata(metadata):
    print(tr("Dataset: {path}", path=metadata.root))
    print(tr("Input format: {format}", format=metadata.format_label))
    print(tr("Dimension: {dimension}D", dimension=metadata.dimension))
    print(tr("Timesteps: {timesteps}", timesteps=metadata.timesteps))
    print(tr("AMR levels: {levels}", levels=metadata.levels))
    variables = ", ".join(metadata.variables) if metadata.variables else f"({tr('not read')})"
    print(tr("Variables: {variables}", variables=variables))
    if metadata.bounds:
        labels = ("X", "Y", "Z")
        print(tr("Coordinate bounds:"))
        for label, bounds in zip(labels, metadata.bounds):
            print(f"  {label}: [{bounds[0]:g}, {bounds[1]:g}]")


def build_parser():
    argparse._ = tr
    parser = argparse.ArgumentParser(
        description=tr("2D AMR fields and parallel X/Y/Z slices for 3D data")
    )
    parser.add_argument(
        "--language",
        choices=["auto", "en", "zh_CN"],
        default=get_language_preference(),
        help=tr("Language: auto, en, or zh_CN"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help=tr("Inspect dataset format and metadata"))
    inspect_parser.add_argument("input", help=tr("Dataset directory"))

    render = subparsers.add_parser("render", help=tr("Render images or videos in batches"))
    render.add_argument("input", help=tr("Dataset directory"))
    render.add_argument("-o", "--output", required=True, help=tr("Output directory"))
    render.add_argument(
        "-v", "--variables", required=True,
        help=tr("Comma-separated variables, for example rho,p,T"),
    )
    render.add_argument(
        "-l", "--levels", default="all",
        help=tr("AMR levels, for example 0,1,2; default: all"),
    )
    render.add_argument(
        "-t", "--timesteps", default="all",
        help=tr("Timesteps, for example 0,500,1000-3000; default: all"),
    )
    render.add_argument(
        "--dimension", choices=["auto", "2", "3"], default="auto",
    )
    render.add_argument(
        "--slice-axis", choices=["x", "y", "z"], default="z",
        help=tr("3D slice normal"),
    )
    render.add_argument(
        "--slice-position", type=float,
        help=tr("3D slice position; defaults to the midpoint"),
    )
    render.add_argument(
        "--x-range", nargs=2, type=float, metavar=("MIN", "MAX"),
        help=tr("X output range; defaults to full bounds"),
    )
    render.add_argument(
        "--y-range", nargs=2, type=float, metavar=("MIN", "MAX"),
        help=tr("Y output range; defaults to full bounds"),
    )
    render.add_argument(
        "--z-range", nargs=2, type=float, metavar=("MIN", "MAX"),
        help=tr("Z output range; defaults to full bounds for 3D data"),
    )
    render.add_argument("--mode", choices=["contourf", "contour", "both"], default="contourf")
    render.add_argument("--cmap", default="redblue")
    render.add_argument("--vmin", type=float)
    render.add_argument("--vmax", type=float)
    render.add_argument("--norm", choices=["linear", "log"], default="linear")
    render.add_argument("--symmetric-color", action="store_true", help=tr("Use a symmetric color range"))
    render.add_argument("--global-color", action="store_true", help=tr("Use one color range for all timesteps"))
    render.add_argument("--dpi", type=int, default=600)
    render.add_argument("--font-family", default=default_plot_font())
    render.add_argument("--font-size", type=int, default=12)
    render.add_argument("--figsize", nargs=2, type=float, metavar=("W", "H"), default=(12, 6))
    render.add_argument("--x-label", default="")
    render.add_argument("--y-label", default="")
    render.add_argument("--contour-linewidth", type=float, default=0.5)
    render.add_argument("--contour-color", default="black")
    render.add_argument("--show-patch-edges", action="store_true")
    render.add_argument(
        "--workers", type=int, default=0,
        help=tr("Worker processes; 0 selects automatically"),
    )
    render.add_argument("--image-format", default="png", choices=["png", "jpg", "tiff", "svg", "pdf"])
    render.add_argument("--output-type", default="image", choices=["image", "video"])
    render.add_argument("--fps", type=int, default=10)
    render.add_argument(
        "--no-resume", action="store_true",
        help=tr("Force regeneration instead of skipping existing output"),
    )
    render.add_argument(
        "--no-gpu", action="store_true",
        help=tr("Disable GPU acceleration and force NumPy/CPU"),
    )
    render.add_argument(
        "--no-ascii-cache", action="store_true",
        help=tr("Disable the Tecplot ASCII binary cache"),
    )
    render.add_argument(
        "--video-format",
        default="mp4",
        choices=["mp4", "gif", "jif", "both"],
        help=tr("Video format; both writes MP4 and GIF"),
    )
    render.add_argument("--no-colorbar", action="store_true")
    render.add_argument("--no-frame", action="store_true")
    render.add_argument("--no-title", action="store_true", help=tr("Hide the title field above the plot"))
    return parser


def main(argv=None):
    _configure_console_encoding()
    argv = list(sys.argv[1:] if argv is None else argv)
    language = get_language_preference()
    for index, value in enumerate(argv):
        if value == "--language" and index + 1 < len(argv):
            language = argv[index + 1]
        elif value.startswith("--language="):
            language = value.split("=", 1)[1]
    set_language(language)
    args = build_parser().parse_args(argv)
    set_language(args.language)
    try:
        metadata = scan_dataset(args.input)
        if args.command == "inspect":
            print_metadata(metadata)
            return 0

        variables = _csv_values(args.variables)
        unknown = [name for name in variables if name not in metadata.variables]
        if unknown:
            raise DatasetError(tr("Dataset does not contain variables: {variables}", variables=", ".join(unknown)))
        levels = metadata.levels if args.levels == "all" else _csv_values(args.levels, int)
        invalid_levels = [level for level in levels if level not in metadata.levels]
        if invalid_levels:
            raise DatasetError(tr("Dataset does not contain levels: {levels}", levels=invalid_levels))
        timesteps = _parse_timesteps(args.timesteps, metadata.timesteps)
        if not timesteps:
            raise DatasetError(tr("No valid timesteps were selected"))

        dimension = metadata.dimension if args.dimension == "auto" else int(args.dimension)
        if dimension != metadata.dimension:
            raise DatasetError(tr(
                "Selected {selected}D mode does not match the dataset dimension {actual}D",
                selected=dimension, actual=metadata.dimension,
            ))
        dataset_bounds = compute_bounds(metadata)
        requested_ranges = {
            "x": args.x_range,
            "y": args.y_range,
            "z": args.z_range,
        }
        spatial_bounds = {}
        axis_count = 3 if dimension == 3 else 2
        for index, axis in enumerate(("x", "y", "z")[:axis_count]):
            domain_low, domain_high = dataset_bounds[index]
            values = requested_ranges[axis] or (domain_low, domain_high)
            low, high = float(values[0]), float(values[1])
            if low >= high:
                raise DatasetError(tr("The {axis} output minimum must be less than the maximum", axis=axis.upper()))
            tolerance = max(1.0, abs(domain_low), abs(domain_high)) * 1.0e-10
            if low < domain_low - tolerance or high > domain_high + tolerance:
                raise DatasetError(tr(
                    "The {axis} output range must be inside [{low:g}, {high:g}]",
                    axis=axis.upper(), low=domain_low, high=domain_high,
                ))
            spatial_bounds[axis] = (low, high)
        if dimension == 2 and args.z_range is not None:
            raise DatasetError(tr("2D data cannot use a Z output range"))

        slice_position = args.slice_position
        if dimension == 3 and slice_position is None:
            low, high = spatial_bounds[args.slice_axis]
            slice_position = 0.5 * (low + high)
        if dimension == 3:
            low, high = spatial_bounds[args.slice_axis]
            if not low <= slice_position <= high:
                raise DatasetError(tr(
                    "The slice position must be inside the selected {axis} range [{low:g}, {high:g}]",
                    axis=args.slice_axis.upper(), low=low, high=high,
                ))
        if args.norm == "log":
            if args.symmetric_color:
                raise DatasetError(tr("Log color scale cannot use a symmetric color range"))
            if args.vmin is not None and args.vmin <= 0:
                raise DatasetError(tr("The minimum value for a log color scale must be greater than zero"))

        hardware = collect_hardware_info(args.input)
        scale = estimate_data_scale(metadata, storage_kind=hardware.storage_kind)
        array_backend = create_backend(prefer_gpu=not args.no_gpu)
        worker_count = recommend_workers(
            hardware,
            scale,
            requested=args.workers,
            use_gpu=not args.no_gpu,
        )
        print(tr(
            "Schedule: {workers} processes; data about {size:.1f} MB; GPU backend={backend}; ASCII cache={cache}",
            workers=worker_count,
            size=scale.total_bytes / (1024 * 1024),
            backend=array_backend.status.device if array_backend.status.is_gpu else tr("CPU fallback"),
            cache=tr("enabled") if not args.no_ascii_cache else tr("disabled"),
        ))

        config = PlotConfig(
            variables=variables,
            selected_levels=levels,
            dimension=dimension,
            slice_axis=args.slice_axis,
            slice_position=slice_position,
            spatial_bounds=spatial_bounds,
            colormap=args.cmap,
            vmin=args.vmin,
            vmax=args.vmax,
            norm_type=args.norm,
            symmetric_color_range=args.symmetric_color,
            global_color_range=args.global_color,
            dpi=args.dpi,
            font_family=args.font_family,
            font_size=args.font_size,
            figsize=tuple(args.figsize),
            x_label=args.x_label,
            y_label=args.y_label,
            show_frame=not args.no_frame,
            show_title=not args.no_title,
            show_colorbar=not args.no_colorbar,
            plot_mode=args.mode,
            contour_linewidth=args.contour_linewidth,
            contour_color=args.contour_color,
            show_patch_edges=args.show_patch_edges,
            fps=args.fps,
            output_type=args.output_type,
            image_format=args.image_format,
            resume=not args.no_resume,
            use_gpu=not args.no_gpu,
            use_ascii_cache=not args.no_ascii_cache,
            video_format=args.video_format,
            video_formats=_video_formats(args.video_format),
            output_dir=os.path.abspath(args.output),
            num_workers=worker_count,
        )
        os.makedirs(config.output_dir, exist_ok=True)

        def progress(value):
            print("\r" + tr("Progress: {percent:6.2f}%", percent=value * 100), end="", flush=True)

        result = generate_all(metadata, timesteps, config, progress_callback=progress)
        print("\n" + tr("Processing completed"))
        for variable, paths in result.items():
            if isinstance(paths, list):
                count = sum(path is not None for path in paths)
                print("  " + tr("{variable}: {count} results", variable=variable, count=count))
            elif isinstance(paths, dict):
                formatted = ", ".join(
                    f"{video_format}: {path}" for video_format, path in paths.items()
                )
                print(f"  {variable}: {formatted}")
            else:
                print(f"  {variable}: {paths}")
        return 0
    except (DatasetError, UnsupportedFormatError, ValueError) as exc:
        print(tr("Error: {message}", message=exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
