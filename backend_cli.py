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
from runtime_policy import estimate_data_scale, recommend_workers
from visualizer import PlotConfig, generate_all


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
    print(f"数据目录: {metadata.root}")
    print(f"输入格式: {metadata.format_label}")
    print(f"数据维度: {metadata.dimension}D")
    print(f"时间步: {metadata.timesteps}")
    print(f"AMR层级: {metadata.levels}")
    print(f"变量: {', '.join(metadata.variables) if metadata.variables else '(未读取)'}")
    if metadata.bounds:
        labels = ("X", "Y", "Z")
        print("坐标范围:")
        for label, bounds in zip(labels, metadata.bounds):
            print(f"  {label}: [{bounds[0]:g}, {bounds[1]:g}]")


def build_parser():
    parser = argparse.ArgumentParser(
        description="二维 AMR 云图与三维 X/Y/Z 平行切片批处理工具"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="检查数据格式和元数据")
    inspect_parser.add_argument("input", help="计算结果目录")

    render = subparsers.add_parser("render", help="批量生成图片或视频")
    render.add_argument("input", help="计算结果目录")
    render.add_argument("-o", "--output", required=True, help="输出目录")
    render.add_argument(
        "-v", "--variables", required=True,
        help="变量列表，例如 rho,p,T",
    )
    render.add_argument(
        "-l", "--levels", default="all",
        help="层级列表，例如 0,1,2；默认 all",
    )
    render.add_argument(
        "-t", "--timesteps", default="all",
        help="时间步，例如 0,500,1000-3000；默认 all",
    )
    render.add_argument(
        "--dimension", choices=["auto", "2", "3"], default="auto",
    )
    render.add_argument(
        "--slice-axis", choices=["x", "y", "z"], default="z",
        help="三维切片法向",
    )
    render.add_argument(
        "--slice-position", type=float,
        help="三维切片坐标；未给出时使用该方向中点",
    )
    render.add_argument(
        "--x-range", nargs=2, type=float, metavar=("MIN", "MAX"),
        help="X方向输出范围；默认完整边界",
    )
    render.add_argument(
        "--y-range", nargs=2, type=float, metavar=("MIN", "MAX"),
        help="Y方向输出范围；默认完整边界",
    )
    render.add_argument(
        "--z-range", nargs=2, type=float, metavar=("MIN", "MAX"),
        help="Z方向输出范围；三维数据默认完整边界",
    )
    render.add_argument("--mode", choices=["contourf", "contour", "both"], default="contourf")
    render.add_argument("--cmap", default="redblue")
    render.add_argument("--vmin", type=float)
    render.add_argument("--vmax", type=float)
    render.add_argument("--norm", choices=["linear", "log"], default="linear")
    render.add_argument("--symmetric-color", action="store_true", help="使用正负对称色标")
    render.add_argument("--global-color", action="store_true", help="所有时间步使用统一色标")
    render.add_argument("--dpi", type=int, default=600)
    render.add_argument("--font-family", default="Times New Roman")
    render.add_argument("--font-size", type=int, default=12)
    render.add_argument("--figsize", nargs=2, type=float, metavar=("W", "H"), default=(12, 6))
    render.add_argument("--x-label", default="")
    render.add_argument("--y-label", default="")
    render.add_argument("--contour-linewidth", type=float, default=0.5)
    render.add_argument("--contour-color", default="black")
    render.add_argument("--show-patch-edges", action="store_true")
    render.add_argument(
        "--workers", type=int, default=0,
        help="并行进程数；0 表示根据硬件和数据规模自动选择",
    )
    render.add_argument("--image-format", default="png", choices=["png", "jpg", "tiff", "svg", "pdf"])
    render.add_argument("--output-type", default="image", choices=["image", "video"])
    render.add_argument("--fps", type=int, default=10)
    render.add_argument(
        "--no-resume", action="store_true",
        help="不跳过已有结果，强制重新生成",
    )
    render.add_argument(
        "--no-gpu", action="store_true",
        help="禁用 GPU 数组加速，强制使用 NumPy/CPU",
    )
    render.add_argument(
        "--no-ascii-cache", action="store_true",
        help="禁用 Tecplot ASCII 二进制缓存",
    )
    render.add_argument(
        "--video-format",
        default="mp4",
        choices=["mp4", "gif", "jif", "both"],
        help="视频格式；gif 与 jif 等价，both 表示同时生成 MP4 和 GIF",
    )
    render.add_argument("--no-colorbar", action="store_true")
    render.add_argument("--no-frame", action="store_true")
    render.add_argument("--no-title", action="store_true", help="隐藏图像顶部字段")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        metadata = scan_dataset(args.input)
        if args.command == "inspect":
            print_metadata(metadata)
            return 0

        variables = _csv_values(args.variables)
        unknown = [name for name in variables if name not in metadata.variables]
        if unknown:
            raise DatasetError(f"数据中不存在变量: {', '.join(unknown)}")
        levels = metadata.levels if args.levels == "all" else _csv_values(args.levels, int)
        invalid_levels = [level for level in levels if level not in metadata.levels]
        if invalid_levels:
            raise DatasetError(f"数据中不存在层级: {invalid_levels}")
        timesteps = _parse_timesteps(args.timesteps, metadata.timesteps)
        if not timesteps:
            raise DatasetError("没有选中有效时间步")

        dimension = metadata.dimension if args.dimension == "auto" else int(args.dimension)
        if dimension != metadata.dimension:
            raise DatasetError(
                f"所选 {dimension}D 模式与数据实际维度 {metadata.dimension}D 不一致"
            )
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
                raise DatasetError(
                    f"{axis.upper()}输出范围最小值必须小于最大值"
                )
            tolerance = max(1.0, abs(domain_low), abs(domain_high)) * 1.0e-10
            if low < domain_low - tolerance or high > domain_high + tolerance:
                raise DatasetError(
                    f"{axis.upper()}输出范围必须位于 "
                    f"[{domain_low:g}, {domain_high:g}]"
                )
            spatial_bounds[axis] = (low, high)
        if dimension == 2 and args.z_range is not None:
            raise DatasetError("二维数据不能设置Z输出范围")

        slice_position = args.slice_position
        if dimension == 3 and slice_position is None:
            low, high = spatial_bounds[args.slice_axis]
            slice_position = 0.5 * (low + high)
        if dimension == 3:
            low, high = spatial_bounds[args.slice_axis]
            if not low <= slice_position <= high:
                raise DatasetError(
                    f"切片坐标必须位于所设 {args.slice_axis.upper()} "
                    f"输出范围 [{low:g}, {high:g}]"
                )
        if args.norm == "log":
            if args.symmetric_color:
                raise DatasetError("log 色标不能同时使用正负对称色标")
            if args.vmin is not None and args.vmin <= 0:
                raise DatasetError("log 色标的最小值必须大于 0")

        hardware = collect_hardware_info(args.input)
        scale = estimate_data_scale(metadata, storage_kind=hardware.storage_kind)
        array_backend = create_backend(prefer_gpu=not args.no_gpu)
        worker_count = recommend_workers(
            hardware,
            scale,
            requested=args.workers,
            use_gpu=not args.no_gpu,
        )
        print(
            f"调度: {worker_count} 个进程；"
            f"数据约 {scale.total_bytes / (1024 * 1024):.1f} MB；"
            f"GPU数组后端={array_backend.status.device if array_backend.status.is_gpu else 'CPU回退'}；"
            f"ASCII缓存={'启用' if not args.no_ascii_cache else '关闭'}"
        )

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
            print(f"\r进度: {value * 100:6.2f}%", end="", flush=True)

        result = generate_all(metadata, timesteps, config, progress_callback=progress)
        print("\n处理完成")
        for variable, paths in result.items():
            if isinstance(paths, list):
                count = sum(path is not None for path in paths)
                print(f"  {variable}: {count} 个结果")
            elif isinstance(paths, dict):
                formatted = ", ".join(
                    f"{video_format}: {path}" for video_format, path in paths.items()
                )
                print(f"  {variable}: {formatted}")
            else:
                print(f"  {variable}: {paths}")
        return 0
    except (DatasetError, UnsupportedFormatError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
