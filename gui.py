"""Tkinter frontend for the AMR post-processing backend."""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from amr_backend import (
    DatasetError,
    UnsupportedFormatError,
    axis_bounds,
    compute_bounds,
    normalize_input_path,
    scan_dataset,
)
from hardware_info import collect_hardware_info, format_hardware_summary
from gpu_backend import create_backend
from runtime_policy import estimate_data_scale, format_data_scale, recommend_workers
from visualizer import COLORMAPS, PlotConfig, generate_all


class AMRVisualizerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("amrex_Viewer v2 - AMR 后处理")
        self.root.geometry("1320x820")
        self._base_width = 1320
        self._base_height = 820
        self._font_sizes = {}
        self._font_resize_job = None
        self.metadata = None
        self.var_checks = {}
        self.level_checks = {}
        self._cancel_flag = False
        self._run_started_at = None
        self.hardware_info = collect_hardware_info()
        self.data_scale = None
        self._fullscreen = False
        self._closing = False
        self._run_active = False
        self._configure_style()
        self._prepare_responsive_fonts()
        self._build_ui()

    def _configure_style(self):
        self.root.minsize(1180, 720)
        self.root.configure(bg="#eef2f7")
        style = ttk.Style(self.root)
        for theme in ("vista", "xpnative", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure("TFrame", background="#eef2f7")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Section.TLabelframe", background="#ffffff", padding=8)
        style.configure(
            "Section.TLabelframe.Label",
            font=("Microsoft YaHei UI", 9, "bold"),
            foreground="#1f2937",
            background="#eef2f7",
        )
        style.configure("TLabel", background="#eef2f7", foreground="#334155")
        style.configure("Section.TLabel", background="#ffffff", foreground="#334155")
        style.configure("Hint.TLabel", background="#ffffff", foreground="#64748b")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 15, "bold"), foreground="#0f172a")
        style.configure("Subtitle.TLabel", font=("Microsoft YaHei UI", 9), foreground="#64748b")
        style.configure("TButton", padding=(8, 4))
        style.configure("Accent.TButton", padding=(12, 5))
        style.configure("TCheckbutton", background="#ffffff", foreground="#334155")
        style.configure("TRadiobutton", background="#ffffff", foreground="#334155")

    def _prepare_responsive_fonts(self):
        for name in tkfont.names(self.root):
            try:
                font = tkfont.nametofont(name, root=self.root)
                size = int(font.cget("size"))
            except (tk.TclError, ValueError):
                continue
            if size:
                self._font_sizes[name] = size
        self.root.bind("<Configure>", self._schedule_font_resize, add="+")

    def _schedule_font_resize(self, event):
        if event.widget is not self.root:
            return
        if self._font_resize_job is not None:
            self.root.after_cancel(self._font_resize_job)
        self._font_resize_job = self.root.after(100, self._resize_fonts)

    def _resize_fonts(self):
        self._font_resize_job = None
        width = max(self.root.winfo_width(), 1)
        height = max(self.root.winfo_height(), 1)
        area_scale = (
            width * height / (self._base_width * self._base_height)
        ) ** 0.5
        scale = min(1.05, max(0.9, area_scale))
        for name, base_size in self._font_sizes.items():
            font = tkfont.nametofont(name, root=self.root)
            sign = -1 if base_size < 0 else 1
            scaled_size = max(8, round(abs(base_size) * scale))
            font.configure(size=sign * scaled_size)

    def _build_ui(self):
        self.root.resizable(True, True)
        self.root.minsize(900, 650)
        self.root.bind("<F11>", self._toggle_fullscreen)
        self.root.bind("<Escape>", self._exit_fullscreen)

        self.action_bar = ttk.Frame(self.root, padding=(14, 6, 14, 10))
        self.action_bar.pack(side="bottom", fill="x")
        self.action_bar.pack_propagate(False)
        self.action_bar.configure(height=78)

        self.scroll_canvas = tk.Canvas(self.root, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            self.root, orient="vertical", command=self.scroll_canvas.yview
        )
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.main = ttk.Frame(self.scroll_canvas, padding=(14, 10, 14, 10))
        self.canvas_window = self.scroll_canvas.create_window(
            (0, 0), window=self.main, anchor="nw"
        )
        self.main.bind("<Configure>", self._main_panel_changed)
        self.scroll_canvas.bind("<Configure>", self._center_main_panel)
        self.scroll_canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")

        header = ttk.Frame(self.main)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="amrex_Viewer v2", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="批量生成二维云图、三维切片、论文图片和视频结果",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(1, 0))

        self._section_parent = self.main
        self._build_hardware_section()

        content = ttk.Frame(self.main)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1, uniform="main")
        content.columnconfigure(1, weight=1, uniform="main")
        content.rowconfigure(0, weight=1)
        self.left_column = ttk.Frame(content)
        self.right_column = ttk.Frame(content)
        self.left_column.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.right_column.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._section_parent = self.left_column
        self._build_source_section()
        self._build_variable_section()
        self._build_dimension_section()
        self._build_spatial_range_section()
        self._build_level_section()
        self._build_timestep_section()

        self._section_parent = self.right_column
        self._build_plot_section()
        self._build_output_section()
        self._build_action_section(self.action_bar)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _center_main_panel(self, event):
        panel_width = max(900, event.width)
        self.scroll_canvas.itemconfigure(self.canvas_window, width=panel_width)

    def _main_panel_changed(self, _event):
        self.scroll_canvas.configure(
            scrollregion=self.scroll_canvas.bbox("all")
        )

    def _on_mousewheel(self, event):
        if event.delta:
            self.scroll_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _toggle_fullscreen(self, _event=None):
        self._fullscreen = not self._fullscreen
        self.root.attributes("-fullscreen", self._fullscreen)
        return "break"

    def _exit_fullscreen(self, _event=None):
        if self._fullscreen:
            self._fullscreen = False
            self.root.attributes("-fullscreen", False)
        return "break"

    def _on_close(self):
        if self._run_active:
            should_close = messagebox.askyesno(
                "任务正在运行",
                "当前仍在生成图片或视频。关闭窗口将结束 GUI，未完成任务不会保证继续运行。\n\n确认关闭吗？",
            )
            if not should_close:
                return
        self._closing = True
        self.root.destroy()

    def _post_to_ui(self, callback):
        if self._closing:
            return
        try:
            self.root.after(0, callback)
        except tk.TclError:
            pass

    def _section(self, title):
        parent = getattr(self, "_section_parent", self.main)
        frame = ttk.LabelFrame(parent, text=title, padding=8, style="Section.TLabelframe")
        frame.pack(fill="x", pady=4)
        return frame

    def _build_source_section(self):
        section = self._section("1. 数据源")
        row = ttk.Frame(section)
        row.pack(fill="x")
        self.folder_var = tk.StringVar()
        self.folder_entry = ttk.Entry(row, textvariable=self.folder_var)
        self.folder_entry.pack(
            side="left", fill="x", expand=True
        )
        self.folder_entry.bind("<Return>", lambda _: self._load_entered_folder())
        ttk.Button(row, text="加载", command=self._load_entered_folder).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(row, text="浏览...", command=self._select_folder).pack(
            side="left", padx=(6, 0)
        )
        self.source_info = ttk.Label(
            section,
            text="请选择 AMReX plotfile（plot_format=0）或 Tecplot ASCII（plot_format=1）数据目录",
        )
        self.source_info.pack(anchor="w", pady=(6, 0))

    def _build_variable_section(self):
        section = self._section("2. 绘图变量（可多选）")
        self.variable_frame = ttk.Frame(section)
        self.variable_frame.pack(fill="x")
        ttk.Label(self.variable_frame, text="请先加载数据目录").pack(anchor="w")

    def _build_dimension_section(self):
        section = self._section("3. 数据维度与三维切片")
        row = ttk.Frame(section)
        row.pack(fill="x")
        ttk.Label(row, text="维度:").pack(side="left")
        self.dimension_var = tk.StringVar(value="auto")
        for label, value in (("自动", "auto"), ("二维", "2"), ("三维", "3")):
            ttk.Radiobutton(
                row, text=label, value=value,
                variable=self.dimension_var,
                command=self._update_slice_controls,
            ).pack(side="left", padx=6)

        self.slice_frame = ttk.Frame(section)
        self.slice_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(self.slice_frame, text="切片法向:").pack(side="left")
        self.slice_axis_var = tk.StringVar(value="z")
        self.slice_axis_combo = ttk.Combobox(
            self.slice_frame,
            textvariable=self.slice_axis_var,
            values=["x", "y", "z"],
            width=7,
            state="readonly",
        )
        self.slice_axis_combo.pack(side="left", padx=5)
        self.slice_axis_combo.bind("<<ComboboxSelected>>", self._axis_changed)
        ttk.Label(self.slice_frame, text="切片坐标:").pack(side="left", padx=(12, 0))
        self.slice_position_var = tk.StringVar()
        self.slice_position_entry = ttk.Entry(
            self.slice_frame, textvariable=self.slice_position_var, width=14
        )
        self.slice_position_entry.pack(side="left", padx=5)
        self.slice_range_label = ttk.Label(self.slice_frame, text="范围: -")
        self.slice_range_label.pack(side="left", padx=10)
        self._update_slice_controls()

    def _build_spatial_range_section(self):
        section = self._section("4. 空间输出范围（默认完整计算域）")
        grid = ttk.Frame(section)
        grid.pack(fill="x")
        ttk.Label(grid, text="方向").grid(row=0, column=0, padx=5, pady=3)
        ttk.Label(grid, text="最小值").grid(row=0, column=1, padx=5, pady=3)
        ttk.Label(grid, text="最大值").grid(row=0, column=2, padx=5, pady=3)
        ttk.Label(grid, text="原始边界").grid(row=0, column=3, padx=10, pady=3)

        self.spatial_range_vars = {}
        self.spatial_range_entries = {}
        self.spatial_range_labels = {}
        for row, axis in enumerate(("x", "y", "z"), start=1):
            low_var = tk.StringVar()
            high_var = tk.StringVar()
            low_entry = ttk.Entry(grid, textvariable=low_var, width=16)
            high_entry = ttk.Entry(grid, textvariable=high_var, width=16)
            ttk.Label(grid, text=axis.upper()).grid(
                row=row, column=0, sticky="w", padx=5, pady=3
            )
            low_entry.grid(row=row, column=1, padx=5, pady=3)
            high_entry.grid(row=row, column=2, padx=5, pady=3)
            bound_label = ttk.Label(grid, text="-")
            bound_label.grid(row=row, column=3, sticky="w", padx=10, pady=3)
            self.spatial_range_vars[axis] = (low_var, high_var)
            self.spatial_range_entries[axis] = (low_entry, high_entry)
            self.spatial_range_labels[axis] = bound_label

        ttk.Button(
            grid,
            text="恢复完整边界",
            command=self._populate_spatial_ranges,
        ).grid(row=1, column=4, rowspan=3, padx=12, pady=3)
        ttk.Label(
            section,
            text="三维切片时，法向范围用于约束切片坐标，平面内两个方向用于裁剪输出区域。",
        ).pack(anchor="w", pady=(5, 0))
        self._update_spatial_range_controls()

    def _build_level_section(self):
        section = self._section("5. AMR 层级（可多选，细层覆盖粗层）")
        self.level_frame = ttk.Frame(section)
        self.level_frame.pack(fill="x")
        ttk.Label(self.level_frame, text="请先加载数据目录").pack(anchor="w")

    def _build_timestep_section(self):
        section = self._section("6. 时间步范围与断点续作")
        grid = ttk.Frame(section)
        grid.pack(fill="x")
        ttk.Label(grid, text="选择方式:").grid(row=0, column=0, sticky="w", pady=4)
        self.timestep_mode_var = tk.StringVar(value="全部时间步")
        ttk.Combobox(
            grid,
            textvariable=self.timestep_mode_var,
            values=["全部时间步", "按时间步范围", "前 N 个时间步"],
            state="readonly",
            width=16,
        ).grid(row=0, column=1, sticky="w", padx=5, pady=4)
        self.timestep_mode_var.trace_add("write", lambda *_: self._update_timestep_controls())

        ttk.Label(grid, text="起始步:").grid(row=1, column=0, sticky="w", pady=4)
        self.timestep_start_var = tk.StringVar()
        self.timestep_start_entry = ttk.Entry(
            grid, textvariable=self.timestep_start_var, width=12
        )
        self.timestep_start_entry.grid(row=1, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text="结束步:").grid(row=1, column=2, sticky="w", padx=(16, 0), pady=4)
        self.timestep_end_var = tk.StringVar()
        self.timestep_end_entry = ttk.Entry(
            grid, textvariable=self.timestep_end_var, width=12
        )
        self.timestep_end_entry.grid(row=1, column=3, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text="数量:").grid(row=2, column=0, sticky="w", pady=4)
        self.timestep_count_var = tk.StringVar()
        self.timestep_count_entry = ttk.Entry(
            grid, textvariable=self.timestep_count_var, width=12
        )
        self.timestep_count_entry.grid(row=2, column=1, sticky="w", padx=5, pady=4)
        self.timestep_hint_var = tk.StringVar(value="请先加载数据目录")
        ttk.Label(
            grid, textvariable=self.timestep_hint_var, justify="left"
        ).grid(row=2, column=2, columnspan=2, sticky="w", padx=(16, 0), pady=4)

        self.resume_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            section,
            text="断点续作：跳过已有有效结果（绘图参数不变时使用）",
            variable=self.resume_var,
        ).pack(anchor="w", pady=(5, 0))
        self._update_timestep_controls()

    def _update_timestep_controls(self):
        if not hasattr(self, "timestep_mode_var"):
            return
        mode = self.timestep_mode_var.get()
        range_state = "normal" if mode == "按时间步范围" else "disabled"
        count_state = "normal" if mode == "前 N 个时间步" else "disabled"
        self.timestep_start_entry.config(state=range_state)
        self.timestep_end_entry.config(state=range_state)
        self.timestep_count_entry.config(state=count_state)

    def _populate_timestep_controls(self):
        if not self.metadata or not self.metadata.timesteps:
            self.timestep_mode_var.set("全部时间步")
            self.timestep_start_var.set("")
            self.timestep_end_var.set("")
            self.timestep_count_var.set("")
            self.timestep_hint_var.set("请先加载数据目录")
            return
        steps = self.metadata.timesteps
        self.timestep_mode_var.set("全部时间步")
        self.timestep_start_var.set(str(steps[0]))
        self.timestep_end_var.set(str(steps[-1]))
        self.timestep_count_var.set(str(len(steps)))
        self.timestep_hint_var.set(
            f"可用范围: {steps[0]} - {steps[-1]}，共 {len(steps)} 个时间步"
        )
        self._update_timestep_controls()

    def _selected_timesteps(self):
        if not self.metadata or not self.metadata.timesteps:
            raise ValueError("没有可用时间步")
        available = list(self.metadata.timesteps)
        mode = self.timestep_mode_var.get()
        if mode == "全部时间步":
            return available
        if mode == "按时间步范围":
            try:
                start = int(self.timestep_start_var.get().strip())
                end = int(self.timestep_end_var.get().strip())
            except ValueError as exc:
                raise ValueError("起始步和结束步必须为整数") from exc
            if start > end:
                raise ValueError("起始步不能大于结束步")
            selected = [step for step in available if start <= step <= end]
            if not selected:
                raise ValueError("指定范围内没有可用时间步")
            return selected
        try:
            count = int(self.timestep_count_var.get().strip())
        except ValueError as exc:
            raise ValueError("时间步数量必须为正整数") from exc
        if count <= 0:
            raise ValueError("时间步数量必须为正整数")
        return available[:count]

    def _build_hardware_section(self):
        section = self._section("硬件与数据规模")
        self.array_backend = create_backend(prefer_gpu=True)
        self.gpu_enabled_var = tk.BooleanVar(
            value=self.array_backend.status.is_gpu
        )
        self.hardware_summary_var = tk.StringVar(
            value=format_hardware_summary(self.hardware_info)
        )
        ttk.Label(
            section,
            textvariable=self.hardware_summary_var,
            justify="left",
            anchor="w",
        ).pack(fill="x")
        self.data_scale_var = tk.StringVar(value="Data: load a dataset to estimate scale")
        ttk.Label(
            section,
            textvariable=self.data_scale_var,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(5, 0))
        self.worker_hint_var = tk.StringVar(
            value="Automatic workers: load a dataset to calculate"
        )
        ttk.Label(
            section,
            textvariable=self.worker_hint_var,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(5, 0))
        self.gpu_status_var = tk.StringVar(
            value=(
                f"GPU数组后端：{self.array_backend.status.device}"
                if self.array_backend.status.is_gpu
                else f"GPU数组后端：CPU回退（{self.array_backend.status.reason}）"
            )
        )
        ttk.Label(
            section,
            textvariable=self.gpu_status_var,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(5, 0))
        ttk.Checkbutton(
            section,
            text="启用 GPU 数组加速（CuPy）",
            variable=self.gpu_enabled_var,
            command=self._update_worker_hint,
        ).pack(anchor="w", pady=(5, 0))
        self.ascii_cache_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            section,
            text="启用 Tecplot ASCII 二进制缓存（首次运行转换）",
            variable=self.ascii_cache_var,
        ).pack(anchor="w", pady=(5, 0))
        ttk.Button(
            section,
            text="刷新硬件信息",
            command=self._refresh_hardware,
        ).pack(anchor="w", pady=(6, 0))

    def _refresh_hardware(self):
        path = normalize_input_path(self.folder_var.get()) if hasattr(self, "folder_var") else ""
        self.hardware_info = collect_hardware_info(path)
        self.hardware_summary_var.set(format_hardware_summary(self.hardware_info))
        if self.metadata:
            self.data_scale = estimate_data_scale(
                self.metadata,
                storage_kind=self.hardware_info.storage_kind,
            )
            self.data_scale_var.set(format_data_scale(self.data_scale))
        self._update_worker_hint()

    def _update_worker_hint(self):
        if not self.data_scale:
            self.worker_hint_var.set("Automatic workers: load a dataset to calculate")
            if hasattr(self, "worker_spinbox"):
                use_gpu = self.gpu_enabled_var.get()
                self.worker_spinbox.configure(state="disabled" if use_gpu else "normal")
            return
        use_gpu = self.gpu_enabled_var.get()
        workers = recommend_workers(
            self.hardware_info,
            self.data_scale,
            requested=0,
            use_gpu=use_gpu,
        )
        if use_gpu:
            text = "GPU模式：1个进程（变量批量上传；避免重复占用显存）"
        else:
            text = f"CPU模式：自动使用 {workers} 个进程（按时间步并行）"
        self.worker_hint_var.set(text)
        if hasattr(self, "worker_spinbox"):
            self.worker_spinbox.configure(state="disabled" if use_gpu else "normal")

    def _build_plot_section(self):
        section = self._section("7. 绘图设置")
        grid = ttk.Frame(section)
        grid.pack(fill="x")
        for column in range(4):
            grid.columnconfigure(column, weight=1 if column in (1, 3) else 0)

        ttk.Label(grid, text="出图预设:").grid(row=0, column=0, sticky="w", pady=4)
        self.preset_var = tk.StringVar(value="自定义")
        preset_combo = ttk.Combobox(
            grid,
            textvariable=self.preset_var,
            values=["自定义", "快速预览", "论文图片", "视频输出", "Schlieren/梯度图"],
            width=15,
            state="readonly",
        )
        preset_combo.grid(row=0, column=1, sticky="w", padx=5, pady=4)
        preset_combo.bind("<<ComboboxSelected>>", self._apply_preset)

        ttk.Label(grid, text="绘图模式:").grid(row=0, column=2, sticky="w", padx=(20, 0), pady=4)
        self.plot_mode_var = tk.StringVar(value="云图")
        ttk.Combobox(
            grid,
            textvariable=self.plot_mode_var,
            values=["云图", "等值线", "云图 + 等值线"],
            width=14,
            state="readonly",
        ).grid(row=0, column=3, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text="色标:").grid(row=1, column=0, sticky="w", pady=4)
        self.cmap_var = tk.StringVar(value="redblue")
        ttk.Combobox(
            grid, textvariable=self.cmap_var,
            values=COLORMAPS, width=18, state="readonly",
        ).grid(row=1, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text="色标尺度:").grid(row=1, column=2, sticky="w", padx=(20, 0), pady=4)
        self.norm_var = tk.StringVar(value="linear")
        ttk.Combobox(
            grid, textvariable=self.norm_var,
            values=["linear", "log"], width=10, state="readonly",
        ).grid(row=1, column=3, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text="DPI:").grid(row=2, column=0, sticky="w", pady=4)
        self.dpi_var = tk.IntVar(value=600)
        ttk.Spinbox(
            grid, textvariable=self.dpi_var,
            from_=72, to=600, width=8,
        ).grid(row=2, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text="等值线数量:").grid(row=2, column=2, sticky="w", padx=(20, 0), pady=4)
        self.contour_levels_var = tk.IntVar(value=20)
        ttk.Spinbox(
            grid, textvariable=self.contour_levels_var,
            from_=5, to=100, width=8,
        ).grid(row=2, column=3, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text="字体:").grid(row=3, column=0, sticky="w", pady=4)
        self.font_family_var = tk.StringVar(value="Times New Roman")
        ttk.Combobox(
            grid,
            textvariable=self.font_family_var,
            values=["Times New Roman", "Microsoft YaHei", "SimHei", "Arial", "sans-serif", "serif"],
            width=18,
        ).grid(row=3, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text="字号:").grid(row=3, column=2, sticky="w", padx=(20, 0), pady=4)
        self.font_size_var = tk.IntVar(value=12)
        ttk.Spinbox(
            grid, textvariable=self.font_size_var,
            from_=8, to=32, width=8,
        ).grid(row=3, column=3, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text="线宽:").grid(row=4, column=0, sticky="w", pady=4)
        self.contour_linewidth_var = tk.DoubleVar(value=0.5)
        ttk.Spinbox(
            grid, textvariable=self.contour_linewidth_var,
            from_=0.1, to=5.0, increment=0.1, width=8,
        ).grid(row=4, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text="线颜色:").grid(row=4, column=2, sticky="w", padx=(20, 0), pady=4)
        self.contour_color_var = tk.StringVar(value="black")
        ttk.Entry(grid, textvariable=self.contour_color_var, width=14).grid(
            row=4, column=3, sticky="w", padx=5, pady=4
        )

        ttk.Label(grid, text="图片宽高:").grid(row=5, column=0, sticky="w", pady=4)
        size_box = ttk.Frame(grid)
        size_box.grid(row=5, column=1, sticky="w", padx=5, pady=4)
        self.fig_width_var = tk.DoubleVar(value=12.0)
        self.fig_height_var = tk.DoubleVar(value=6.0)
        ttk.Spinbox(size_box, textvariable=self.fig_width_var, from_=3.0, to=30.0, increment=0.5, width=6).pack(side="left")
        ttk.Label(size_box, text=" x ").pack(side="left")
        ttk.Spinbox(size_box, textvariable=self.fig_height_var, from_=3.0, to=30.0, increment=0.5, width=6).pack(side="left")

        ttk.Label(grid, text="进程数（CPU模式）:").grid(row=5, column=2, sticky="w", padx=(20, 0), pady=4)
        self.workers_var = tk.IntVar(value=0)
        self.worker_spinbox = ttk.Spinbox(
            grid, textvariable=self.workers_var,
            from_=0, to=max(1, os.cpu_count() or 1), width=8,
        )
        self.worker_spinbox.grid(row=5, column=3, sticky="w", padx=5, pady=4)
        self._update_worker_hint()

        self.auto_range_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            grid, text="颜色范围自动",
            variable=self.auto_range_var,
            command=self._toggle_range,
        ).grid(row=6, column=0, sticky="w", pady=4)
        self.vmin_var = tk.StringVar()
        self.vmax_var = tk.StringVar()
        self.vmin_entry = ttk.Entry(grid, textvariable=self.vmin_var, width=10)
        self.vmax_entry = ttk.Entry(grid, textvariable=self.vmax_var, width=10)
        self.vmin_entry.grid(row=6, column=1, sticky="w", padx=5, pady=4)
        self.vmax_entry.grid(row=6, column=2, sticky="w", padx=5, pady=4)
        self._toggle_range()

        self.symmetric_range_var = tk.BooleanVar(value=False)
        self.global_range_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            grid, text="正负对称色标", variable=self.symmetric_range_var
        ).grid(row=6, column=3, sticky="w", pady=4)
        ttk.Checkbutton(
            grid, text="全部时间步统一色标", variable=self.global_range_var
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=4)

        self.colorbar_var = tk.BooleanVar(value=True)
        self.frame_var = tk.BooleanVar(value=True)
        self.title_var = tk.BooleanVar(value=True)
        self.patch_edges_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            grid, text="显示颜色条", variable=self.colorbar_var
        ).grid(row=7, column=2, sticky="w", pady=4)
        ttk.Checkbutton(
            grid, text="显示坐标框", variable=self.frame_var
        ).grid(row=7, column=3, sticky="w", pady=4)
        ttk.Checkbutton(
            grid, text="显示顶部字段", variable=self.title_var
        ).grid(row=8, column=0, sticky="w", pady=4)
        ttk.Checkbutton(
            grid, text="显示 AMR patch 边界", variable=self.patch_edges_var
        ).grid(row=8, column=1, columnspan=2, sticky="w", pady=4)

        ttk.Label(grid, text="X轴标签:").grid(row=9, column=0, sticky="w", pady=4)
        self.x_label_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self.x_label_var, width=16).grid(
            row=9, column=1, sticky="w", padx=5, pady=4
        )
        ttk.Label(grid, text="Y轴标签:").grid(row=9, column=2, sticky="w", padx=(20, 0), pady=4)
        self.y_label_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self.y_label_var, width=16).grid(
            row=9, column=3, sticky="w", padx=5, pady=4
        )

    def _apply_preset(self, _event=None):
        preset = self.preset_var.get()
        if preset == "快速预览":
            self.dpi_var.set(100)
            self.fig_width_var.set(8.0)
            self.fig_height_var.set(4.5)
            self.font_size_var.set(10)
            self.contour_levels_var.set(12)
            self.contour_linewidth_var.set(0.4)
            self.cmap_var.set("turbo")
            self.image_format_var.set("png")
            self.global_range_var.set(False)
        elif preset == "论文图片":
            self.dpi_var.set(300)
            self.fig_width_var.set(7.0)
            self.fig_height_var.set(4.2)
            self.font_size_var.set(14)
            self.contour_levels_var.set(24)
            self.contour_linewidth_var.set(0.8)
            self.cmap_var.set("viridis")
            self.image_format_var.set("png")
            self.colorbar_var.set(True)
            self.frame_var.set(True)
        elif preset == "视频输出":
            self.dpi_var.set(150)
            self.fig_width_var.set(9.0)
            self.fig_height_var.set(5.0)
            self.font_size_var.set(12)
            self.contour_levels_var.set(18)
            self.contour_linewidth_var.set(0.5)
            self.cmap_var.set("turbo")
            self.output_type_var.set("video")
            self.image_format_var.set("png")
            self.video_mp4_var.set(True)
            self.video_gif_var.set(False)
            self.global_range_var.set(True)
            self.frame_var.set(False)
        elif preset == "Schlieren/梯度图":
            self.dpi_var.set(300)
            self.fig_width_var.set(8.0)
            self.fig_height_var.set(4.0)
            self.font_size_var.set(12)
            self.contour_levels_var.set(30)
            self.contour_linewidth_var.set(0.4)
            self.cmap_var.set("gray_r")
            self.colorbar_var.set(False)
            self.norm_var.set("linear")

    def _build_output_section(self):
        section = self._section("8. 输出")
        type_row = ttk.Frame(section)
        type_row.pack(fill="x")
        self.output_type_var = tk.StringVar(value="image")
        ttk.Radiobutton(
            type_row, text="图片", value="image",
            variable=self.output_type_var,
        ).pack(side="left", padx=5)
        ttk.Radiobutton(
            type_row, text="视频", value="video",
            variable=self.output_type_var,
        ).pack(side="left", padx=5)

        ttk.Label(type_row, text="图片格式:").pack(side="left", padx=(20, 4))
        self.image_format_var = tk.StringVar(value="png")
        ttk.Combobox(
            type_row,
            textvariable=self.image_format_var,
            values=["png", "jpg", "tiff", "svg", "pdf"],
            width=8,
            state="readonly",
        ).pack(side="left")
        ttk.Label(type_row, text="视频帧率:").pack(side="left", padx=(20, 4))
        self.fps_var = tk.IntVar(value=10)
        ttk.Spinbox(
            type_row, textvariable=self.fps_var,
            from_=1, to=120, width=7,
        ).pack(side="left")

        video_row = ttk.Frame(section)
        video_row.pack(fill="x", pady=(8, 0))
        ttk.Label(video_row, text="视频格式:").pack(side="left", padx=(5, 4))
        self.video_mp4_var = tk.BooleanVar(value=True)
        self.video_gif_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            video_row, text="MP4", variable=self.video_mp4_var
        ).pack(side="left", padx=5)
        ttk.Checkbutton(
            video_row, text="GIF/JIF", variable=self.video_gif_var
        ).pack(side="left", padx=5)

        path_row = ttk.Frame(section)
        path_row.pack(fill="x", pady=(8, 0))
        self.output_dir_var = tk.StringVar()
        ttk.Entry(path_row, textvariable=self.output_dir_var).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            path_row, text="选择输出目录...",
            command=self._select_output_dir,
        ).pack(side="left", padx=(6, 0))
        self.open_output_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            section, text="完成后打开输出目录", variable=self.open_output_var
        ).pack(anchor="w", pady=(8, 0))

    def _build_action_section(self, parent=None):
        if parent is None:
            section = self._section("9. 执行")
        else:
            section = ttk.LabelFrame(parent, text="9. 执行", padding=8)
            section.pack(fill="x")
        row = ttk.Frame(section)
        row.pack(fill="x")
        self.run_button = ttk.Button(row, text="开始生成", command=self._run)
        self.run_button.pack(side="left")
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(
            row, variable=self.progress_var,
            maximum=100, mode="determinate",
        ).pack(side="left", fill="x", expand=True, padx=12)
        self.status_label = ttk.Label(row, text="就绪", width=12)
        self.status_label.pack(side="left")
        self.elapsed_label = ttk.Label(row, text="用时 00:00", width=14)
        self.elapsed_label.pack(side="left", padx=(10, 0))

    def _select_folder(self):
        current = normalize_input_path(self.folder_var.get())
        initial_dir = current if current and os.path.isdir(current) else str(Path.home())
        folder = filedialog.askdirectory(
            title="选择具体计算结果目录（例如 Results-3Dformat0）",
            initialdir=initial_dir,
        )
        if not folder:
            return
        self.folder_var.set(folder)
        self._load_folder(folder)

    def _load_entered_folder(self):
        folder = normalize_input_path(self.folder_var.get())
        if not folder:
            messagebox.showwarning("路径为空", "请输入或选择计算结果目录")
            return
        self.folder_var.set(folder)
        self._load_folder(folder)

    def _load_folder(self, folder):
        folder = normalize_input_path(folder)
        self.folder_var.set(folder)
        self.source_info.config(text="正在扫描...")
        self.root.update_idletasks()
        try:
            self.metadata = scan_dataset(folder)
        except (DatasetError, UnsupportedFormatError) as exc:
            self.metadata = None
            self._clear_dataset_controls()
            self.source_info.config(text="扫描失败")
            messagebox.showerror("数据读取错误", str(exc))
            return

        meta = self.metadata
        if meta.source_format == "tecplot_binary":
            self.metadata = None
            self._clear_dataset_controls()
            self.source_info.config(text="已识别 Tecplot Binary，但当前版本不读取该格式")
            messagebox.showerror(
                "数据格式不支持",
                "数据路径可以正常访问，但其中是 plot_format=2 的 Tecplot TDV112 二进制文件。\n"
                "当前软件仅支持 plot_format=0 的 AMReX Plotfile 和 "
                "plot_format=1 的 Tecplot ASCII。",
            )
            return

        self.hardware_info = collect_hardware_info(folder)
        self.data_scale = estimate_data_scale(
            meta,
            storage_kind=self.hardware_info.storage_kind,
        )
        self.hardware_summary_var.set(format_hardware_summary(self.hardware_info))
        self.data_scale_var.set(format_data_scale(self.data_scale))
        self._update_worker_hint()
        self.dimension_var.set("auto")
        self.source_info.config(
            text=(
                f"格式: {meta.format_label}；维度: {meta.dimension}D；"
                f"时间步: {len(meta.timesteps)}；层级: {meta.levels}；"
                f"变量: {len(meta.variables)}"
            )
        )
        self._populate_variables()
        self._populate_levels()
        self._populate_timestep_controls()
        self._populate_spatial_ranges()
        self._update_slice_controls()
        if not self.output_dir_var.get():
            self.output_dir_var.set(os.path.join(folder, "post_output"))

    def _clear_dataset_controls(self):
        self.data_scale = None
        if hasattr(self, "timestep_mode_var"):
            self._populate_timestep_controls()
        if hasattr(self, "data_scale_var"):
            self.data_scale_var.set("Data: load a dataset to estimate scale")
            self._update_worker_hint()
        for frame, text in (
            (self.variable_frame, "请先加载受支持的数据目录"),
            (self.level_frame, "请先加载受支持的数据目录"),
        ):
            for child in frame.winfo_children():
                child.destroy()
            ttk.Label(frame, text=text).pack(anchor="w")
        self.var_checks.clear()
        self.level_checks.clear()
        for axis in ("x", "y", "z"):
            low_var, high_var = self.spatial_range_vars[axis]
            low_var.set("")
            high_var.set("")
            self.spatial_range_labels[axis].config(text="-")
        self._update_spatial_range_controls()
        self._update_slice_controls()

    def _populate_variables(self):
        for child in self.variable_frame.winfo_children():
            child.destroy()
        self.var_checks.clear()
        variables = [
            name for name in self.metadata.variables
            if name.lower() not in {"x", "y", "z"}
        ]
        for index, name in enumerate(variables):
            flag = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                self.variable_frame, text=name, variable=flag
            ).grid(row=index // 6, column=index % 6, sticky="w", padx=8, pady=2)
            self.var_checks[name] = flag
        button_row = len(variables) // 6 + 1
        ttk.Button(
            self.variable_frame, text="全选",
            command=lambda: [flag.set(True) for flag in self.var_checks.values()],
        ).grid(row=button_row, column=0, padx=8, pady=5)
        ttk.Button(
            self.variable_frame, text="全不选",
            command=lambda: [flag.set(False) for flag in self.var_checks.values()],
        ).grid(row=button_row, column=1, padx=8, pady=5)

    def _populate_levels(self):
        for child in self.level_frame.winfo_children():
            child.destroy()
        self.level_checks.clear()
        for index, level in enumerate(self.metadata.levels):
            flag = tk.BooleanVar(value=True)
            ttk.Checkbutton(
                self.level_frame, text=f"lev{level}", variable=flag
            ).grid(row=0, column=index, sticky="w", padx=10)
            self.level_checks[level] = flag
        ttk.Button(
            self.level_frame, text="全选",
            command=lambda: [flag.set(True) for flag in self.level_checks.values()],
        ).grid(row=1, column=0, padx=8, pady=5)
        ttk.Button(
            self.level_frame, text="全不选",
            command=lambda: [flag.set(False) for flag in self.level_checks.values()],
        ).grid(row=1, column=1, padx=8, pady=5)

    def _effective_dimension(self):
        value = self.dimension_var.get()
        if value == "auto":
            return self.metadata.dimension if self.metadata else 2
        return int(value)

    def _update_slice_controls(self):
        enabled = bool(self.metadata and self._effective_dimension() == 3)
        if hasattr(self, "spatial_range_entries"):
            self._update_spatial_range_controls()
        state = "readonly" if enabled else "disabled"
        self.slice_axis_combo.config(state=state)
        self.slice_position_entry.config(state="normal" if enabled else "disabled")
        if enabled:
            self._axis_changed()
        else:
            self.slice_range_label.config(text="二维数据无需切片")

    def _update_spatial_range_controls(self):
        dimension = self._effective_dimension() if self.metadata else 0
        for axis in ("x", "y", "z"):
            enabled = bool(self.metadata and (axis != "z" or dimension == 3))
            state = "normal" if enabled else "disabled"
            for entry in self.spatial_range_entries[axis]:
                entry.config(state=state)

    def _populate_spatial_ranges(self):
        if not self.metadata:
            return
        bounds = compute_bounds(self.metadata)
        for index, axis in enumerate(("x", "y", "z")):
            low_var, high_var = self.spatial_range_vars[axis]
            if index < len(bounds):
                low, high = bounds[index]
                low_var.set(f"{low:g}")
                high_var.set(f"{high:g}")
                self.spatial_range_labels[axis].config(
                    text=f"[{low:g}, {high:g}]"
                )
            else:
                low_var.set("")
                high_var.set("")
                self.spatial_range_labels[axis].config(text="二维数据无Z范围")
        self._update_spatial_range_controls()

    def _selected_axis_bounds(self, axis):
        if self.metadata:
            low_var, high_var = self.spatial_range_vars[axis]
            try:
                return float(low_var.get()), float(high_var.get())
            except ValueError:
                pass
        return axis_bounds(self.metadata, axis)

    def _axis_changed(self, _event=None):
        if not self.metadata or self._effective_dimension() != 3:
            return
        try:
            low, high = self._selected_axis_bounds(self.slice_axis_var.get())
        except DatasetError as exc:
            self.slice_range_label.config(text=str(exc))
            return
        self.slice_range_label.config(text=f"范围: [{low:g}, {high:g}]")
        self.slice_position_var.set(f"{0.5 * (low + high):g}")

    def _toggle_range(self):
        state = "disabled" if self.auto_range_var.get() else "normal"
        self.vmin_entry.config(state=state)
        self.vmax_entry.config(state=state)

    def _select_output_dir(self):
        folder = filedialog.askdirectory(title="选择输出目录")
        if folder:
            self.output_dir_var.set(folder)

    @staticmethod
    def _format_duration(seconds):
        seconds = max(0, int(round(seconds)))
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _elapsed_text(self):
        if self._run_started_at is None:
            return "用时 00:00"
        return f"用时 {self._format_duration(time.perf_counter() - self._run_started_at)}"

    def _update_progress_status(self, percent):
        self.progress_var.set(percent)
        self.status_label.config(text=f"{percent:5.1f}%")
        self.elapsed_label.config(text=self._elapsed_text())

    def _build_config(self):
        if not self.metadata:
            raise ValueError("请先选择数据目录")
        variables = [name for name, flag in self.var_checks.items() if flag.get()]
        if not variables:
            raise ValueError("请至少选择一个绘图变量")
        levels = [level for level, flag in self.level_checks.items() if flag.get()]
        if not levels:
            raise ValueError("请至少选择一个 AMR 层级")
        output_dir = self.output_dir_var.get().strip()
        if not output_dir:
            raise ValueError("请选择输出目录")

        config = PlotConfig()
        config.variables = variables
        config.selected_levels = levels
        config.dimension = self._effective_dimension()
        if config.dimension != self.metadata.dimension:
            raise ValueError(
                f"所选 {config.dimension}D 模式与数据实际维度 "
                f"{self.metadata.dimension}D 不一致"
            )
        config.slice_axis = self.slice_axis_var.get()
        domain_bounds = compute_bounds(self.metadata)
        axis_count = 3 if config.dimension == 3 else 2
        for index, axis in enumerate(("x", "y", "z")[:axis_count]):
            low_var, high_var = self.spatial_range_vars[axis]
            try:
                low = float(low_var.get())
                high = float(high_var.get())
            except ValueError as exc:
                raise ValueError(f"{axis.upper()}输出范围必须为数字") from exc
            domain_low, domain_high = domain_bounds[index]
            if low >= high:
                raise ValueError(
                    f"{axis.upper()}输出范围最小值必须小于最大值"
                )
            tolerance = max(1.0, abs(domain_low), abs(domain_high)) * 1.0e-10
            if low < domain_low - tolerance or high > domain_high + tolerance:
                raise ValueError(
                    f"{axis.upper()}输出范围必须位于 "
                    f"[{domain_low:g}, {domain_high:g}]"
                )
            config.spatial_bounds[axis] = (low, high)
        if config.dimension == 3:
            try:
                config.slice_position = float(self.slice_position_var.get())
            except ValueError as exc:
                raise ValueError("三维切片坐标必须为数字") from exc
            low, high = config.spatial_bounds[config.slice_axis]
            if not low <= config.slice_position <= high:
                raise ValueError(
                    f"切片坐标必须位于所设 {config.slice_axis.upper()} "
                    f"输出范围 [{low:g}, {high:g}]"
                )

        mode_map = {
            "云图": "contourf",
            "等值线": "contour",
            "云图 + 等值线": "both",
        }
        config.plot_mode = mode_map.get(self.plot_mode_var.get(), self.plot_mode_var.get())
        config.colormap = self.cmap_var.get()
        config.norm_type = self.norm_var.get()
        config.symmetric_color_range = self.symmetric_range_var.get()
        config.global_color_range = self.global_range_var.get()
        config.contour_levels = self.contour_levels_var.get()
        config.contour_linewidth = float(self.contour_linewidth_var.get())
        config.contour_color = self.contour_color_var.get().strip() or "black"
        config.dpi = self.dpi_var.get()
        config.font_family = self.font_family_var.get().strip() or "sans-serif"
        config.font_size = self.font_size_var.get()
        config.figsize = (
            float(self.fig_width_var.get()),
            float(self.fig_height_var.get()),
        )
        if config.figsize[0] <= 0 or config.figsize[1] <= 0:
            raise ValueError("图片宽高必须为正数")
        config.x_label = self.x_label_var.get().strip()
        config.y_label = self.y_label_var.get().strip()
        config.show_patch_edges = self.patch_edges_var.get()
        config.use_gpu = self.gpu_enabled_var.get()
        config.use_ascii_cache = self.ascii_cache_var.get()
        config.num_workers = recommend_workers(
            self.hardware_info,
            self.data_scale or estimate_data_scale(
                self.metadata,
                storage_kind=self.hardware_info.storage_kind,
            ),
            requested=self.workers_var.get(),
            use_gpu=config.use_gpu,
        )
        config.show_colorbar = self.colorbar_var.get()
        config.show_frame = self.frame_var.get()
        config.show_title = self.title_var.get()
        if not self.auto_range_var.get():
            if self.vmin_var.get().strip():
                config.vmin = float(self.vmin_var.get())
            if self.vmax_var.get().strip():
                config.vmax = float(self.vmax_var.get())
        if config.norm_type == "log":
            if config.symmetric_color_range:
                raise ValueError("log 色标不能同时使用正负对称色标")
            if config.vmin is not None and config.vmin <= 0:
                raise ValueError("log 色标的最小值必须大于 0")
        config.output_type = self.output_type_var.get()
        config.image_format = self.image_format_var.get()
        config.resume = self.resume_var.get()
        config.fps = self.fps_var.get()
        config.video_formats = []
        if self.video_mp4_var.get():
            config.video_formats.append("mp4")
        if self.video_gif_var.get():
            config.video_formats.append("gif")
        if config.output_type == "video" and not config.video_formats:
            raise ValueError("请至少勾选一种视频格式（MP4 或 GIF/JIF）")
        config.video_format = config.video_formats[0] if config.video_formats else "mp4"
        config.output_dir = output_dir
        return config

    def _run(self):
        try:
            config = self._build_config()
            selected_timesteps = self._selected_timesteps()
        except (ValueError, DatasetError) as exc:
            messagebox.showwarning("参数错误", str(exc))
            return
        os.makedirs(config.output_dir, exist_ok=True)
        self.run_button.config(state="disabled")
        self._run_active = True
        self._run_started_at = time.perf_counter()
        self.progress_var.set(0)
        self.status_label.config(text="0.0%")
        self.elapsed_label.config(text="用时 00:00")

        def progress(value):
            percent = max(0.0, min(100.0, value * 100))
            self._post_to_ui(lambda: self._update_progress_status(percent))

        def worker():
            try:
                result = generate_all(
                    self.metadata,
                    selected_timesteps,
                    config,
                    progress_callback=progress,
                )
                elapsed = time.perf_counter() - self._run_started_at
                self._post_to_ui(
                    lambda: self._done(result, config.output_dir, elapsed)
                )
            except Exception as exc:
                elapsed = (
                    time.perf_counter() - self._run_started_at
                    if self._run_started_at is not None else 0
                )
                self._post_to_ui(lambda: self._failed(str(exc), elapsed))

        threading.Thread(target=worker, daemon=True).start()

    def _done(self, result, output_dir, elapsed):
        self.run_button.config(state="normal")
        self._run_active = False
        self.progress_var.set(100)
        elapsed_text = self._format_duration(elapsed)
        self.status_label.config(text="完成")
        self.elapsed_label.config(text=f"用时 {elapsed_text}")
        self._run_started_at = None
        if self.open_output_var.get() and os.path.isdir(output_dir):
            os.startfile(output_dir)
        messagebox.showinfo(
            "完成",
            f"已生成 {len(result)} 个变量的结果。\n"
            f"完成用时: {elapsed_text}\n"
            f"输出目录: {output_dir}",
        )

    def _failed(self, message, elapsed=0):
        self.run_button.config(state="normal")
        self._run_active = False
        self.status_label.config(text="失败")
        self.elapsed_label.config(text=f"用时 {self._format_duration(elapsed)}")
        self._run_started_at = None
        messagebox.showerror("生成失败", message)


def run_app():
    root = tk.Tk()
    AMRVisualizerApp(root)
    root.mainloop()
