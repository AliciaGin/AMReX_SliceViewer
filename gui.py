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
from i18n import (
    get_language,
    get_language_preference,
    save_language_preference,
    set_language,
    tr,
)
from platform_support import (
    available_plot_fonts,
    configure_high_dpi,
    configure_tk_fonts,
    default_plot_font,
    open_in_file_manager,
)
from visualizer import COLORMAPS, PlotConfig, generate_all


class AMRVisualizerApp:
    def __init__(self, root):
        self.root = root
        self.language_preference = get_language_preference()
        self.language = set_language(self.language_preference)
        self._ = tr
        self.ui_font_family = configure_tk_fonts(self.root, self.language)
        self.root.title(f"amrex_Viewer v2 - {self._('AMR post-processing')}")
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
            font=(self.ui_font_family, 9, "bold"),
            foreground="#1f2937",
            background="#eef2f7",
        )
        style.configure("TLabel", background="#eef2f7", foreground="#334155")
        style.configure("Section.TLabel", background="#ffffff", foreground="#334155")
        style.configure("Hint.TLabel", background="#ffffff", foreground="#64748b")
        style.configure("Title.TLabel", font=(self.ui_font_family, 15, "bold"), foreground="#0f172a")
        style.configure("Subtitle.TLabel", font=(self.ui_font_family, 9), foreground="#64748b")
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
        self.root.bind("<Configure>", self._schedule_font_resize)

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
        self.scroll_canvas.bind_all("<Button-4>", self._on_mousewheel, add="+")
        self.scroll_canvas.bind_all("<Button-5>", self._on_mousewheel, add="+")

        header = ttk.Frame(self.main)
        header.pack(fill="x", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        title_box = ttk.Frame(header)
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, text="amrex_Viewer v2", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_box,
            text=self._("Generate 2D fields, 3D slices, publication figures, and animations"),
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(1, 0))
        language_box = ttk.Frame(header)
        language_box.grid(row=0, column=1, sticky="ne")
        ttk.Label(language_box, text=self._("Language:")).pack(side="left", padx=(0, 5))
        self.language_choices = {
            self._("System default"): "auto",
            self._("English"): "en",
            self._("Simplified Chinese"): "zh_CN",
        }
        selected_language = next(
            label
            for label, code in self.language_choices.items()
            if code == self.language_preference
        )
        self.language_var = tk.StringVar(value=selected_language)
        self.language_combo = ttk.Combobox(
            language_box,
            textvariable=self.language_var,
            values=list(self.language_choices),
            width=16,
            state="readonly",
        )
        self.language_combo.pack(side="left")
        self.language_combo.bind("<<ComboboxSelected>>", self._language_changed)

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
        if getattr(event, "num", None) == 4:
            self.scroll_canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            self.scroll_canvas.yview_scroll(1, "units")
        elif event.delta:
            self.scroll_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _language_changed(self, _event=None):
        preference = self.language_choices.get(self.language_var.get(), "auto")
        if preference == self.language_preference:
            return
        if self._run_active:
            messagebox.showwarning(
                self._("A task is running"),
                self._("Language cannot be changed while a task is running."),
            )
            current = next(
                label
                for label, code in self.language_choices.items()
                if code == self.language_preference
            )
            self.language_var.set(current)
            return

        folder = self.folder_var.get() if hasattr(self, "folder_var") else ""
        output_dir = self.output_dir_var.get() if hasattr(self, "output_dir_var") else ""
        had_dataset = self.metadata is not None
        self.language_preference = preference
        self.language = set_language(preference)
        try:
            save_language_preference(preference)
        except OSError:
            pass

        self.root.unbind_all("<MouseWheel>")
        self.root.unbind_all("<Button-4>")
        self.root.unbind_all("<Button-5>")
        for child in self.root.winfo_children():
            child.destroy()
        if hasattr(self, "spatial_range_entries"):
            del self.spatial_range_entries
        self.__init__(self.root)
        if had_dataset and folder:
            self.folder_var.set(folder)
            self._load_folder(folder)
            if output_dir:
                self.output_dir_var.set(output_dir)

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
                self._("A task is running"),
                self._("Image or video generation is still running. Closing the window will stop the GUI and unfinished work may be lost.\n\nClose anyway?"),
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
        section = self._section(self._("1. Data source"))
        row = ttk.Frame(section)
        row.pack(fill="x")
        self.folder_var = tk.StringVar()
        self.folder_entry = ttk.Entry(row, textvariable=self.folder_var)
        self.folder_entry.pack(
            side="left", fill="x", expand=True
        )
        self.folder_entry.bind("<Return>", lambda _: self._load_entered_folder())
        ttk.Button(row, text=self._("Load"), command=self._load_entered_folder).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(row, text=self._("Browse..."), command=self._select_folder).pack(
            side="left", padx=(6, 0)
        )
        self.source_info = ttk.Label(
            section,
            text=self._("Select an AMReX plotfile (plot_format=0) or Tecplot ASCII (plot_format=1) directory"),
        )
        self.source_info.pack(anchor="w", pady=(6, 0))

    def _build_variable_section(self):
        section = self._section(self._("2. Variables (multiple selection)"))
        self.variable_frame = ttk.Frame(section)
        self.variable_frame.pack(fill="x")
        ttk.Label(self.variable_frame, text=self._("Load a dataset first")).pack(anchor="w")

    def _build_dimension_section(self):
        section = self._section(self._("3. Dimension and 3D slice"))
        row = ttk.Frame(section)
        row.pack(fill="x")
        ttk.Label(row, text=self._("Dimension:")).pack(side="left")
        self.dimension_var = tk.StringVar(value="auto")
        for label, value in ((self._("Auto"), "auto"), (self._("2D"), "2"), (self._("3D"), "3")):
            ttk.Radiobutton(
                row, text=label, value=value,
                variable=self.dimension_var,
                command=self._update_slice_controls,
            ).pack(side="left", padx=6)

        self.slice_frame = ttk.Frame(section)
        self.slice_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(self.slice_frame, text=self._("Slice normal:")).pack(side="left")
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
        ttk.Label(self.slice_frame, text=self._("Slice position:")).pack(side="left", padx=(12, 0))
        self.slice_position_var = tk.StringVar()
        self.slice_position_entry = ttk.Entry(
            self.slice_frame, textvariable=self.slice_position_var, width=14
        )
        self.slice_position_entry.pack(side="left", padx=5)
        self.slice_range_label = ttk.Label(self.slice_frame, text=self._("Range: -"))
        self.slice_range_label.pack(side="left", padx=10)
        self._update_slice_controls()

    def _build_spatial_range_section(self):
        section = self._section(self._("4. Spatial output bounds (full domain by default)"))
        grid = ttk.Frame(section)
        grid.pack(fill="x")
        ttk.Label(grid, text=self._("Axis")).grid(row=0, column=0, padx=5, pady=3)
        ttk.Label(grid, text=self._("Minimum")).grid(row=0, column=1, padx=5, pady=3)
        ttk.Label(grid, text=self._("Maximum")).grid(row=0, column=2, padx=5, pady=3)
        ttk.Label(grid, text=self._("Domain bounds")).grid(row=0, column=3, padx=10, pady=3)

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
            text=self._("Restore full bounds"),
            command=self._populate_spatial_ranges,
        ).grid(row=1, column=4, rowspan=3, padx=12, pady=3)
        ttk.Label(
            section,
            text=self._("For 3D slices, the normal range constrains the slice position; the other two ranges crop the output plane."),
        ).pack(anchor="w", pady=(5, 0))
        self._update_spatial_range_controls()

    def _build_level_section(self):
        section = self._section(self._("5. AMR levels (fine levels cover coarse levels)"))
        self.level_frame = ttk.Frame(section)
        self.level_frame.pack(fill="x")
        ttk.Label(self.level_frame, text=self._("Load a dataset first")).pack(anchor="w")

    def _build_timestep_section(self):
        section = self._section(self._("6. Timesteps and resume"))
        grid = ttk.Frame(section)
        grid.pack(fill="x")
        ttk.Label(grid, text=self._("Selection:")).grid(row=0, column=0, sticky="w", pady=4)
        self.timestep_mode_var = tk.StringVar(value=self._("All timesteps"))
        ttk.Combobox(
            grid,
            textvariable=self.timestep_mode_var,
            values=[self._("All timesteps"), self._("Timestep range"), self._("First N timesteps")],
            state="readonly",
            width=16,
        ).grid(row=0, column=1, sticky="w", padx=5, pady=4)
        self.timestep_mode_var.trace_add("write", lambda *_: self._update_timestep_controls())

        ttk.Label(grid, text=self._("Start:")).grid(row=1, column=0, sticky="w", pady=4)
        self.timestep_start_var = tk.StringVar()
        self.timestep_start_entry = ttk.Entry(
            grid, textvariable=self.timestep_start_var, width=12
        )
        self.timestep_start_entry.grid(row=1, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text=self._("End:")).grid(row=1, column=2, sticky="w", padx=(16, 0), pady=4)
        self.timestep_end_var = tk.StringVar()
        self.timestep_end_entry = ttk.Entry(
            grid, textvariable=self.timestep_end_var, width=12
        )
        self.timestep_end_entry.grid(row=1, column=3, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text=self._("Count:")).grid(row=2, column=0, sticky="w", pady=4)
        self.timestep_count_var = tk.StringVar()
        self.timestep_count_entry = ttk.Entry(
            grid, textvariable=self.timestep_count_var, width=12
        )
        self.timestep_count_entry.grid(row=2, column=1, sticky="w", padx=5, pady=4)
        self.timestep_hint_var = tk.StringVar(value=self._("Load a dataset first"))
        ttk.Label(
            grid, textvariable=self.timestep_hint_var, justify="left"
        ).grid(row=2, column=2, columnspan=2, sticky="w", padx=(16, 0), pady=4)

        self.resume_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            section,
            text=self._("Resume: skip valid existing results when plot settings are unchanged"),
            variable=self.resume_var,
        ).pack(anchor="w", pady=(5, 0))
        self._update_timestep_controls()

    def _update_timestep_controls(self):
        if not hasattr(self, "timestep_mode_var"):
            return
        mode = self.timestep_mode_var.get()
        range_state = "normal" if mode == self._("Timestep range") else "disabled"
        count_state = "normal" if mode == self._("First N timesteps") else "disabled"
        self.timestep_start_entry.config(state=range_state)
        self.timestep_end_entry.config(state=range_state)
        self.timestep_count_entry.config(state=count_state)

    def _populate_timestep_controls(self):
        if not self.metadata or not self.metadata.timesteps:
            self.timestep_mode_var.set(self._("All timesteps"))
            self.timestep_start_var.set("")
            self.timestep_end_var.set("")
            self.timestep_count_var.set("")
            self.timestep_hint_var.set(self._("Load a dataset first"))
            return
        steps = self.metadata.timesteps
        self.timestep_mode_var.set(self._("All timesteps"))
        self.timestep_start_var.set(str(steps[0]))
        self.timestep_end_var.set(str(steps[-1]))
        self.timestep_count_var.set(str(len(steps)))
        self.timestep_hint_var.set(self._(
            "Available: {first} - {last}, {count} timesteps",
            first=steps[0], last=steps[-1], count=len(steps),
        ))
        self._update_timestep_controls()

    def _selected_timesteps(self):
        if not self.metadata or not self.metadata.timesteps:
            raise ValueError(self._("No timesteps are available"))
        available = list(self.metadata.timesteps)
        mode = self.timestep_mode_var.get()
        if mode == self._("All timesteps"):
            return available
        if mode == self._("Timestep range"):
            try:
                start = int(self.timestep_start_var.get().strip())
                end = int(self.timestep_end_var.get().strip())
            except ValueError as exc:
                raise ValueError(self._("Start and end timesteps must be integers")) from exc
            if start > end:
                raise ValueError(self._("The start timestep cannot be greater than the end timestep"))
            selected = [step for step in available if start <= step <= end]
            if not selected:
                raise ValueError(self._("No available timesteps are inside the requested range"))
            return selected
        try:
            count = int(self.timestep_count_var.get().strip())
        except ValueError as exc:
            raise ValueError(self._("The timestep count must be a positive integer")) from exc
        if count <= 0:
            raise ValueError(self._("The timestep count must be a positive integer"))
        return available[:count]

    def _build_hardware_section(self):
        section = self._section(self._("Hardware and data scale"))
        self.array_backend = create_backend(prefer_gpu=True)
        self.gpu_enabled_var = tk.BooleanVar(
            value=self.array_backend.status.is_gpu
        )
        self.hardware_summary_var = tk.StringVar(
            value=format_hardware_summary(self.hardware_info, language=self.language)
        )
        ttk.Label(
            section,
            textvariable=self.hardware_summary_var,
            justify="left",
            anchor="w",
        ).pack(fill="x")
        self.data_scale_var = tk.StringVar(value=self._("Data: load a dataset to estimate scale"))
        ttk.Label(
            section,
            textvariable=self.data_scale_var,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(5, 0))
        self.worker_hint_var = tk.StringVar(
            value=self._("Automatic workers: load a dataset to calculate")
        )
        ttk.Label(
            section,
            textvariable=self.worker_hint_var,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(5, 0))
        self.gpu_status_var = tk.StringVar(
            value=(
                self._("GPU array backend: {device}", device=self.array_backend.status.device)
                if self.array_backend.status.is_gpu
                else self._("GPU array backend: CPU fallback ({reason})", reason=self.array_backend.status.reason)
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
            text=self._("Enable GPU array acceleration (CuPy)"),
            variable=self.gpu_enabled_var,
            command=self._update_worker_hint,
        ).pack(anchor="w", pady=(5, 0))
        self.ascii_cache_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            section,
            text=self._("Enable Tecplot ASCII binary cache (converted on first use)"),
            variable=self.ascii_cache_var,
        ).pack(anchor="w", pady=(5, 0))
        ttk.Button(
            section,
            text=self._("Refresh hardware information"),
            command=self._refresh_hardware,
        ).pack(anchor="w", pady=(6, 0))

    def _refresh_hardware(self):
        path = normalize_input_path(self.folder_var.get()) if hasattr(self, "folder_var") else ""
        self.hardware_info = collect_hardware_info(path)
        self.hardware_summary_var.set(format_hardware_summary(self.hardware_info, language=self.language))
        if self.metadata:
            self.data_scale = estimate_data_scale(
                self.metadata,
                storage_kind=self.hardware_info.storage_kind,
            )
            self.data_scale_var.set(format_data_scale(self.data_scale, language=self.language))
        self._update_worker_hint()

    def _update_worker_hint(self):
        if not self.data_scale:
            self.worker_hint_var.set(self._("Automatic workers: load a dataset to calculate"))
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
            text = self._("GPU mode: 1 process (variables are uploaded in batches to avoid duplicated GPU memory)")
        else:
            text = self._("CPU mode: automatically use {workers} processes (parallel by timestep)", workers=workers)
        self.worker_hint_var.set(text)
        if hasattr(self, "worker_spinbox"):
            self.worker_spinbox.configure(state="disabled" if use_gpu else "normal")

    def _build_plot_section(self):
        section = self._section(self._("7. Plot settings"))
        grid = ttk.Frame(section)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text=self._("Preset:")).grid(row=0, column=0, sticky="w", pady=4)
        self.preset_var = tk.StringVar(value=self._("Custom"))
        preset_combo = ttk.Combobox(
            grid,
            textvariable=self.preset_var,
            values=[self._("Custom"), self._("Quick preview"), self._("Publication figure"), self._("Video output"), self._("Schlieren / gradient")],
            width=15,
            state="readonly",
        )
        preset_combo.grid(row=0, column=1, sticky="w", padx=5, pady=4)
        preset_combo.bind("<<ComboboxSelected>>", self._apply_preset)

        ttk.Label(grid, text=self._("Plot mode:")).grid(row=1, column=0, sticky="w", pady=4)
        self.plot_mode_var = tk.StringVar(value=self._("Filled contour"))
        ttk.Combobox(
            grid,
            textvariable=self.plot_mode_var,
            values=[self._("Filled contour"), self._("Contour lines"), self._("Filled contour + lines")],
            width=14,
            state="readonly",
        ).grid(row=1, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text=self._("Colormap:")).grid(row=2, column=0, sticky="w", pady=4)
        self.cmap_var = tk.StringVar(value="redblue")
        ttk.Combobox(
            grid, textvariable=self.cmap_var,
            values=COLORMAPS, width=18, state="readonly",
        ).grid(row=2, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text=self._("Color scale:")).grid(row=3, column=0, sticky="w", pady=4)
        self.norm_var = tk.StringVar(value="linear")
        ttk.Combobox(
            grid, textvariable=self.norm_var,
            values=["linear", "log"], width=10, state="readonly",
        ).grid(row=3, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text="DPI:").grid(row=4, column=0, sticky="w", pady=4)
        self.dpi_var = tk.IntVar(value=600)
        ttk.Spinbox(
            grid, textvariable=self.dpi_var,
            from_=72, to=600, width=8,
        ).grid(row=4, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text=self._("Contour levels:")).grid(row=5, column=0, sticky="w", pady=4)
        self.contour_levels_var = tk.IntVar(value=20)
        ttk.Spinbox(
            grid, textvariable=self.contour_levels_var,
            from_=5, to=100, width=8,
        ).grid(row=5, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text=self._("Font:")).grid(row=6, column=0, sticky="w", pady=4)
        plot_fonts = available_plot_fonts(self.language)
        self.font_family_var = tk.StringVar(value=default_plot_font(self.language))
        ttk.Combobox(
            grid,
            textvariable=self.font_family_var,
            values=plot_fonts,
            width=18,
        ).grid(row=6, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text=self._("Font size:")).grid(row=7, column=0, sticky="w", pady=4)
        self.font_size_var = tk.IntVar(value=12)
        ttk.Spinbox(
            grid, textvariable=self.font_size_var,
            from_=8, to=32, width=8,
        ).grid(row=7, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text=self._("Line width:")).grid(row=8, column=0, sticky="w", pady=4)
        self.contour_linewidth_var = tk.DoubleVar(value=0.5)
        ttk.Spinbox(
            grid, textvariable=self.contour_linewidth_var,
            from_=0.1, to=5.0, increment=0.1, width=8,
        ).grid(row=8, column=1, sticky="w", padx=5, pady=4)

        ttk.Label(grid, text=self._("Line color:")).grid(row=9, column=0, sticky="w", pady=4)
        self.contour_color_var = tk.StringVar(value="black")
        ttk.Entry(grid, textvariable=self.contour_color_var, width=14).grid(
            row=9, column=1, sticky="w", padx=5, pady=4
        )

        ttk.Label(grid, text=self._("Figure size:")).grid(row=10, column=0, sticky="w", pady=4)
        size_box = ttk.Frame(grid)
        size_box.grid(row=10, column=1, sticky="w", padx=5, pady=4)
        self.fig_width_var = tk.DoubleVar(value=12.0)
        self.fig_height_var = tk.DoubleVar(value=6.0)
        ttk.Spinbox(size_box, textvariable=self.fig_width_var, from_=3.0, to=30.0, increment=0.5, width=6).pack(side="left")
        ttk.Label(size_box, text=" x ").pack(side="left")
        ttk.Spinbox(size_box, textvariable=self.fig_height_var, from_=3.0, to=30.0, increment=0.5, width=6).pack(side="left")

        ttk.Label(grid, text=self._("Processes (CPU mode):")).grid(row=11, column=0, sticky="w", pady=4)
        self.workers_var = tk.IntVar(value=0)
        self.worker_spinbox = ttk.Spinbox(
            grid, textvariable=self.workers_var,
            from_=0, to=max(1, os.cpu_count() or 1), width=8,
        )
        self.worker_spinbox.grid(row=11, column=1, sticky="w", padx=5, pady=4)
        self._update_worker_hint()

        self.auto_range_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            grid, text=self._("Automatic color range"),
            variable=self.auto_range_var,
            command=self._toggle_range,
        ).grid(row=12, column=0, sticky="w", pady=4)
        self.vmin_var = tk.StringVar()
        self.vmax_var = tk.StringVar()
        range_box = ttk.Frame(grid)
        range_box.grid(row=12, column=1, sticky="w", padx=5, pady=4)
        self.vmin_entry = ttk.Entry(range_box, textvariable=self.vmin_var, width=10)
        self.vmax_entry = ttk.Entry(range_box, textvariable=self.vmax_var, width=10)
        self.vmin_entry.pack(side="left")
        self.vmax_entry.pack(side="left", padx=(6, 0))
        self._toggle_range()

        self.symmetric_range_var = tk.BooleanVar(value=False)
        self.global_range_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            grid, text=self._("Symmetric color range"), variable=self.symmetric_range_var
        ).grid(row=13, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(
            grid, text=self._("Global color range across all timesteps"), variable=self.global_range_var
        ).grid(row=14, column=0, columnspan=2, sticky="w", pady=4)

        self.colorbar_var = tk.BooleanVar(value=True)
        self.frame_var = tk.BooleanVar(value=True)
        self.title_var = tk.BooleanVar(value=True)
        self.patch_edges_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            grid, text=self._("Show colorbar"), variable=self.colorbar_var
        ).grid(row=15, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(
            grid, text=self._("Show axes frame"), variable=self.frame_var
        ).grid(row=16, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(
            grid, text=self._("Show title field"), variable=self.title_var
        ).grid(row=17, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(
            grid, text=self._("Show AMR patch boundaries"), variable=self.patch_edges_var
        ).grid(row=18, column=0, columnspan=2, sticky="w", pady=4)

        ttk.Label(grid, text=self._("X-axis label:")).grid(row=19, column=0, sticky="w", pady=4)
        self.x_label_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self.x_label_var, width=16).grid(
            row=19, column=1, sticky="w", padx=5, pady=4
        )
        ttk.Label(grid, text=self._("Y-axis label:")).grid(row=20, column=0, sticky="w", pady=4)
        self.y_label_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self.y_label_var, width=16).grid(
            row=20, column=1, sticky="w", padx=5, pady=4
        )

    def _apply_preset(self, _event=None):
        preset = self.preset_var.get()
        if preset == self._("Quick preview"):
            self.dpi_var.set(100)
            self.fig_width_var.set(8.0)
            self.fig_height_var.set(4.5)
            self.font_size_var.set(10)
            self.contour_levels_var.set(12)
            self.contour_linewidth_var.set(0.4)
            self.cmap_var.set("turbo")
            self.image_format_var.set("png")
            self.global_range_var.set(False)
        elif preset == self._("Publication figure"):
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
        elif preset == self._("Video output"):
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
        elif preset == self._("Schlieren / gradient"):
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
        section = self._section(self._("8. Output"))
        type_row = ttk.Frame(section)
        type_row.pack(fill="x")
        self.output_type_var = tk.StringVar(value="image")
        ttk.Radiobutton(
            type_row, text=self._("Image"), value="image",
            variable=self.output_type_var,
        ).pack(side="left", padx=5)
        ttk.Radiobutton(
            type_row, text=self._("Video"), value="video",
            variable=self.output_type_var,
        ).pack(side="left", padx=5)

        ttk.Label(type_row, text=self._("Image format:")).pack(side="left", padx=(20, 4))
        self.image_format_var = tk.StringVar(value="png")
        ttk.Combobox(
            type_row,
            textvariable=self.image_format_var,
            values=["png", "jpg", "tiff", "svg", "pdf"],
            width=8,
            state="readonly",
        ).pack(side="left")
        ttk.Label(type_row, text=self._("Video FPS:")).pack(side="left", padx=(20, 4))
        self.fps_var = tk.IntVar(value=10)
        ttk.Spinbox(
            type_row, textvariable=self.fps_var,
            from_=1, to=120, width=7,
        ).pack(side="left")

        video_row = ttk.Frame(section)
        video_row.pack(fill="x", pady=(8, 0))
        ttk.Label(video_row, text=self._("Video format:")).pack(side="left", padx=(5, 4))
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
            path_row, text=self._("Select output directory..."),
            command=self._select_output_dir,
        ).pack(side="left", padx=(6, 0))
        self.open_output_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            section, text=self._("Open output directory when finished"), variable=self.open_output_var
        ).pack(anchor="w", pady=(8, 0))

    def _build_action_section(self, parent=None):
        if parent is None:
            section = self._section(self._("9. Run"))
        else:
            section = ttk.LabelFrame(parent, text=self._("9. Run"), padding=8)
            section.pack(fill="x")
        row = ttk.Frame(section)
        row.pack(fill="x")
        self.run_button = ttk.Button(row, text=self._("Start"), command=self._run)
        self.run_button.pack(side="left")
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(
            row, variable=self.progress_var,
            maximum=100, mode="determinate",
        ).pack(side="left", fill="x", expand=True, padx=12)
        self.status_label = ttk.Label(row, text=self._("Ready"), width=12)
        self.status_label.pack(side="left")
        self.elapsed_label = ttk.Label(row, text=self._("Elapsed {duration}", duration="00:00"), width=14)
        self.elapsed_label.pack(side="left", padx=(10, 0))

    def _select_folder(self):
        current = normalize_input_path(self.folder_var.get())
        initial_dir = current if current and os.path.isdir(current) else str(Path.home())
        folder = filedialog.askdirectory(
            title=self._("Select a specific result directory (for example Results-3Dformat0)"),
            initialdir=initial_dir,
        )
        if not folder:
            return
        self.folder_var.set(folder)
        self._load_folder(folder)

    def _load_entered_folder(self):
        folder = normalize_input_path(self.folder_var.get())
        if not folder:
            messagebox.showwarning(self._("Empty path"), self._("Enter or select a result directory"))
            return
        self.folder_var.set(folder)
        self._load_folder(folder)

    def _load_folder(self, folder):
        folder = normalize_input_path(folder)
        self.folder_var.set(folder)
        self.source_info.config(text=self._("Scanning..."))
        self.root.update_idletasks()
        try:
            self.metadata = scan_dataset(folder)
        except (DatasetError, UnsupportedFormatError) as exc:
            self.metadata = None
            self._clear_dataset_controls()
            self.source_info.config(text=self._("Scan failed"))
            messagebox.showerror(self._("Dataset error"), str(exc))
            return

        meta = self.metadata
        if meta.source_format == "tecplot_binary":
            self.metadata = None
            self._clear_dataset_controls()
            self.source_info.config(text=self._("Tecplot Binary detected, but this version cannot read it"))
            messagebox.showerror(
                self._("Unsupported data format"),
                self._("The directory is accessible but contains plot_format=2 Tecplot TDV112 binary data.\nThis application supports plot_format=0 AMReX plotfiles and plot_format=1 Tecplot ASCII."),
            )
            return

        self.hardware_info = collect_hardware_info(folder)
        self.data_scale = estimate_data_scale(
            meta,
            storage_kind=self.hardware_info.storage_kind,
        )
        self.hardware_summary_var.set(format_hardware_summary(self.hardware_info, language=self.language))
        self.data_scale_var.set(format_data_scale(self.data_scale, language=self.language))
        self._update_worker_hint()
        self.dimension_var.set("auto")
        self.source_info.config(
            text=self._(
                "Format: {format}; dimension: {dimension}D; timesteps: {timesteps}; levels: {levels}; variables: {variables}",
                format=meta.format_label,
                dimension=meta.dimension,
                timesteps=len(meta.timesteps),
                levels=meta.levels,
                variables=len(meta.variables),
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
            self.data_scale_var.set(self._("Data: load a dataset to estimate scale"))
            self._update_worker_hint()
        for frame, text in (
            (self.variable_frame, self._("Load a supported dataset first")),
            (self.level_frame, self._("Load a supported dataset first")),
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
            self.variable_frame, text=self._("Select all"),
            command=lambda: [flag.set(True) for flag in self.var_checks.values()],
        ).grid(row=button_row, column=0, padx=8, pady=5)
        ttk.Button(
            self.variable_frame, text=self._("Select none"),
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
            self.level_frame, text=self._("Select all"),
            command=lambda: [flag.set(True) for flag in self.level_checks.values()],
        ).grid(row=1, column=0, padx=8, pady=5)
        ttk.Button(
            self.level_frame, text=self._("Select none"),
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
            self.slice_range_label.config(text=self._("2D data does not require a slice"))

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
                self.spatial_range_labels[axis].config(text=self._("2D data has no Z range"))
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
        self.slice_range_label.config(text=self._("Range: [{low:g}, {high:g}]", low=low, high=high))
        self.slice_position_var.set(f"{0.5 * (low + high):g}")

    def _toggle_range(self):
        state = "disabled" if self.auto_range_var.get() else "normal"
        self.vmin_entry.config(state=state)
        self.vmax_entry.config(state=state)

    def _select_output_dir(self):
        folder = filedialog.askdirectory(title=self._("Select output directory"))
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
            return self._("Elapsed {duration}", duration="00:00")
        return self._("Elapsed {duration}", duration=self._format_duration(time.perf_counter() - self._run_started_at))

    def _update_progress_status(self, percent):
        self.progress_var.set(percent)
        self.status_label.config(text=f"{percent:5.1f}%")
        self.elapsed_label.config(text=self._elapsed_text())

    def _build_config(self):
        if not self.metadata:
            raise ValueError(self._("Select a dataset first"))
        variables = [name for name, flag in self.var_checks.items() if flag.get()]
        if not variables:
            raise ValueError(self._("Select at least one variable"))
        levels = [level for level, flag in self.level_checks.items() if flag.get()]
        if not levels:
            raise ValueError(self._("Select at least one AMR level"))
        output_dir = self.output_dir_var.get().strip()
        if not output_dir:
            raise ValueError(self._("Select an output directory"))

        config = PlotConfig()
        config.variables = variables
        config.selected_levels = levels
        config.dimension = self._effective_dimension()
        if config.dimension != self.metadata.dimension:
            raise ValueError(self._(
                "Selected {selected}D mode does not match the dataset dimension {actual}D",
                selected=config.dimension,
                actual=self.metadata.dimension,
            ))
        config.slice_axis = self.slice_axis_var.get()
        domain_bounds = compute_bounds(self.metadata)
        axis_count = 3 if config.dimension == 3 else 2
        for index, axis in enumerate(("x", "y", "z")[:axis_count]):
            low_var, high_var = self.spatial_range_vars[axis]
            try:
                low = float(low_var.get())
                high = float(high_var.get())
            except ValueError as exc:
                raise ValueError(self._("The {axis} output bounds must be numeric", axis=axis.upper())) from exc
            domain_low, domain_high = domain_bounds[index]
            if low >= high:
                raise ValueError(self._("The {axis} output minimum must be less than the maximum", axis=axis.upper()))
            tolerance = max(1.0, abs(domain_low), abs(domain_high)) * 1.0e-10
            if low < domain_low - tolerance or high > domain_high + tolerance:
                raise ValueError(self._(
                    "The {axis} output range must be inside [{low:g}, {high:g}]",
                    axis=axis.upper(), low=domain_low, high=domain_high,
                ))
            config.spatial_bounds[axis] = (low, high)
        if config.dimension == 3:
            try:
                config.slice_position = float(self.slice_position_var.get())
            except ValueError as exc:
                raise ValueError(self._("The 3D slice position must be numeric")) from exc
            low, high = config.spatial_bounds[config.slice_axis]
            if not low <= config.slice_position <= high:
                raise ValueError(self._(
                    "The slice position must be inside the selected {axis} range [{low:g}, {high:g}]",
                    axis=config.slice_axis.upper(), low=low, high=high,
                ))

        mode_map = {
            self._("Filled contour"): "contourf",
            self._("Contour lines"): "contour",
            self._("Filled contour + lines"): "both",
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
            raise ValueError(self._("Figure width and height must be positive"))
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
                raise ValueError(self._("Log color scale cannot use a symmetric color range"))
            if config.vmin is not None and config.vmin <= 0:
                raise ValueError(self._("The minimum value for a log color scale must be greater than zero"))
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
            raise ValueError(self._("Select at least one video format (MP4 or GIF/JIF)"))
        config.video_format = config.video_formats[0] if config.video_formats else "mp4"
        config.output_dir = output_dir
        return config

    def _run(self):
        try:
            config = self._build_config()
            selected_timesteps = self._selected_timesteps()
        except (ValueError, DatasetError) as exc:
            messagebox.showwarning(self._("Invalid settings"), str(exc))
            return
        os.makedirs(config.output_dir, exist_ok=True)
        self.run_button.config(state="disabled")
        self._run_active = True
        self._run_started_at = time.perf_counter()
        self.progress_var.set(0)
        self.status_label.config(text="0.0%")
        self.elapsed_label.config(text=self._("Elapsed {duration}", duration="00:00"))

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
        self.status_label.config(text=self._("Completed"))
        self.elapsed_label.config(text=self._("Elapsed {duration}", duration=elapsed_text))
        self._run_started_at = None
        if self.open_output_var.get() and os.path.isdir(output_dir):
            try:
                open_in_file_manager(output_dir)
            except OSError:
                pass
        messagebox.showinfo(
            self._("Completed"),
            self._(
                "Generated results for {count} variables.\nElapsed: {duration}\nOutput directory: {output}",
                count=len(result), duration=elapsed_text, output=output_dir,
            ),
        )

    def _failed(self, message, elapsed=0):
        self.run_button.config(state="normal")
        self._run_active = False
        self.status_label.config(text=self._("Failed"))
        self.elapsed_label.config(text=self._("Elapsed {duration}", duration=self._format_duration(elapsed)))
        self._run_started_at = None
        messagebox.showerror(self._("Generation failed"), message)


def run_app():
    configure_high_dpi()
    root = tk.Tk()
    AMRVisualizerApp(root)
    root.mainloop()
