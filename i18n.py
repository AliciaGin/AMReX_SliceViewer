"""Small built-in localization layer for the GUI, CLI, and backend messages."""

from __future__ import annotations

import json
import locale
import os
import platform
from pathlib import Path


SUPPORTED_LANGUAGES = ("en", "zh_CN")
LANGUAGE_PREFERENCES = ("auto",) + SUPPORTED_LANGUAGES


_ZH = {
    "AMR post-processing": "AMR 后处理",
    "Generate 2D fields, 3D slices, publication figures, and animations": "批量生成二维云图、三维切片、论文图片和视频结果",
    "Language:": "语言:",
    "System default": "跟随系统",
    "English": "English",
    "Simplified Chinese": "简体中文",
    "A task is running": "任务正在运行",
    "Image or video generation is still running. Closing the window will stop the GUI and unfinished work may be lost.\n\nClose anyway?": "当前仍在生成图片或视频。关闭窗口将结束 GUI，未完成任务不会保证继续运行。\n\n确认关闭吗？",
    "Language cannot be changed while a task is running.": "任务运行期间不能切换语言。",
    "1. Data source": "1. 数据源",
    "Load": "加载",
    "Browse...": "浏览...",
    "Select an AMReX plotfile (plot_format=0) or Tecplot ASCII (plot_format=1) directory": "请选择 AMReX plotfile（plot_format=0）或 Tecplot ASCII（plot_format=1）数据目录",
    "2. Variables (multiple selection)": "2. 绘图变量（可多选）",
    "Load a dataset first": "请先加载数据目录",
    "Load a supported dataset first": "请先加载受支持的数据目录",
    "3. Dimension and 3D slice": "3. 数据维度与三维切片",
    "Dimension:": "维度:",
    "Auto": "自动",
    "2D": "二维",
    "3D": "三维",
    "Slice normal:": "切片法向:",
    "Slice position:": "切片坐标:",
    "Range: -": "范围: -",
    "Range: [{low:g}, {high:g}]": "范围: [{low:g}, {high:g}]",
    "2D data does not require a slice": "二维数据无需切片",
    "2D data has no Z range": "二维数据无Z范围",
    "4. Spatial output bounds (full domain by default)": "4. 空间输出范围（默认完整计算域）",
    "Axis": "方向",
    "Minimum": "最小值",
    "Maximum": "最大值",
    "Domain bounds": "原始边界",
    "Restore full bounds": "恢复完整边界",
    "For 3D slices, the normal range constrains the slice position; the other two ranges crop the output plane.": "三维切片时，法向范围用于约束切片坐标，平面内两个方向用于裁剪输出区域。",
    "5. AMR levels (fine levels cover coarse levels)": "5. AMR 层级（可多选，细层覆盖粗层）",
    "6. Timesteps and resume": "6. 时间步范围与断点续作",
    "Selection:": "选择方式:",
    "All timesteps": "全部时间步",
    "Timestep range": "按时间步范围",
    "First N timesteps": "前 N 个时间步",
    "Start:": "起始步:",
    "End:": "结束步:",
    "Count:": "数量:",
    "Available: {first} - {last}, {count} timesteps": "可用范围: {first} - {last}，共 {count} 个时间步",
    "Resume: skip valid existing results when plot settings are unchanged": "断点续作：跳过已有有效结果（绘图参数不变时使用）",
    "No timesteps are available": "没有可用时间步",
    "Start and end timesteps must be integers": "起始步和结束步必须为整数",
    "The start timestep cannot be greater than the end timestep": "起始步不能大于结束步",
    "No available timesteps are inside the requested range": "指定范围内没有可用时间步",
    "The timestep count must be a positive integer": "时间步数量必须为正整数",
    "Hardware and data scale": "硬件与数据规模",
    "Data: load a dataset to estimate scale": "数据：加载数据集后估算规模",
    "Automatic workers: load a dataset to calculate": "自动进程数：加载数据集后计算",
    "GPU array backend: {device}": "GPU数组后端：{device}",
    "GPU array backend: CPU fallback ({reason})": "GPU数组后端：CPU回退（{reason}）",
    "Enable GPU array acceleration (CuPy)": "启用 GPU 数组加速（CuPy）",
    "Enable Tecplot ASCII binary cache (converted on first use)": "启用 Tecplot ASCII 二进制缓存（首次运行转换）",
    "Refresh hardware information": "刷新硬件信息",
    "GPU mode: 1 process (variables are uploaded in batches to avoid duplicated GPU memory)": "GPU模式：1个进程（变量批量上传；避免重复占用显存）",
    "CPU mode: automatically use {workers} processes (parallel by timestep)": "CPU模式：自动使用 {workers} 个进程（按时间步并行）",
    "7. Plot settings": "7. 绘图设置",
    "Preset:": "出图预设:",
    "Custom": "自定义",
    "Quick preview": "快速预览",
    "Publication figure": "论文图片",
    "Video output": "视频输出",
    "Schlieren / gradient": "Schlieren/梯度图",
    "Plot mode:": "绘图模式:",
    "Filled contour": "云图",
    "Contour lines": "等值线",
    "Filled contour + lines": "云图 + 等值线",
    "Colormap:": "色标:",
    "Color scale:": "色标尺度:",
    "Contour levels:": "等值线数量:",
    "Font:": "字体:",
    "Font size:": "字号:",
    "Line width:": "线宽:",
    "Line color:": "线颜色:",
    "Figure size:": "图片宽高:",
    "Processes (CPU mode):": "进程数（CPU模式）:",
    "Automatic color range": "颜色范围自动",
    "Symmetric color range": "正负对称色标",
    "Global color range across all timesteps": "全部时间步统一色标",
    "Show colorbar": "显示颜色条",
    "Show axes frame": "显示坐标框",
    "Show title field": "显示顶部字段",
    "Show AMR patch boundaries": "显示 AMR patch 边界",
    "X-axis label:": "X轴标签:",
    "Y-axis label:": "Y轴标签:",
    "8. Output": "8. 输出",
    "Image": "图片",
    "Video": "视频",
    "Image format:": "图片格式:",
    "Video FPS:": "视频帧率:",
    "Video format:": "视频格式:",
    "Select output directory...": "选择输出目录...",
    "Open output directory when finished": "完成后打开输出目录",
    "9. Run": "9. 执行",
    "Start": "开始生成",
    "Ready": "就绪",
    "Elapsed {duration}": "用时 {duration}",
    "Select a specific result directory (for example Results-3Dformat0)": "选择具体计算结果目录（例如 Results-3Dformat0）",
    "Empty path": "路径为空",
    "Enter or select a result directory": "请输入或选择计算结果目录",
    "Scanning...": "正在扫描...",
    "Scan failed": "扫描失败",
    "Dataset error": "数据读取错误",
    "Tecplot Binary detected, but this version cannot read it": "已识别 Tecplot Binary，但当前版本不读取该格式",
    "Unsupported data format": "数据格式不支持",
    "The directory is accessible but contains plot_format=2 Tecplot TDV112 binary data.\nThis application supports plot_format=0 AMReX plotfiles and plot_format=1 Tecplot ASCII.": "数据路径可以正常访问，但其中是 plot_format=2 的 Tecplot TDV112 二进制文件。\n当前软件仅支持 plot_format=0 的 AMReX Plotfile 和 plot_format=1 的 Tecplot ASCII。",
    "Format: {format}; dimension: {dimension}D; timesteps: {timesteps}; levels: {levels}; variables: {variables}": "格式: {format}；维度: {dimension}D；时间步: {timesteps}；层级: {levels}；变量: {variables}",
    "Select all": "全选",
    "Select none": "全不选",
    "Select output directory": "选择输出目录",
    "Select a dataset first": "请先选择数据目录",
    "Select at least one variable": "请至少选择一个绘图变量",
    "Select at least one AMR level": "请至少选择一个 AMR 层级",
    "Select an output directory": "请选择输出目录",
    "Selected {selected}D mode does not match the dataset dimension {actual}D": "所选 {selected}D 模式与数据实际维度 {actual}D 不一致",
    "The {axis} output bounds must be numeric": "{axis}输出范围必须为数字",
    "The {axis} output minimum must be less than the maximum": "{axis}输出范围最小值必须小于最大值",
    "The {axis} output range must be inside [{low:g}, {high:g}]": "{axis}输出范围必须位于 [{low:g}, {high:g}]",
    "The 3D slice position must be numeric": "三维切片坐标必须为数字",
    "The slice position must be inside the selected {axis} range [{low:g}, {high:g}]": "切片坐标必须位于所设 {axis} 输出范围 [{low:g}, {high:g}]",
    "Figure width and height must be positive": "图片宽高必须为正数",
    "Log color scale cannot use a symmetric color range": "log 色标不能同时使用正负对称色标",
    "The minimum value for a log color scale must be greater than zero": "log 色标的最小值必须大于 0",
    "Select at least one video format (MP4 or GIF/JIF)": "请至少勾选一种视频格式（MP4 或 GIF/JIF）",
    "Invalid settings": "参数错误",
    "Completed": "完成",
    "Generated results for {count} variables.\nElapsed: {duration}\nOutput directory: {output}": "已生成 {count} 个变量的结果。\n完成用时: {duration}\n输出目录: {output}",
    "Failed": "失败",
    "Generation failed": "生成失败",
    "CPU": "CPU",
    "CPU fallback": "CPU回退",
    "enabled": "启用",
    "disabled": "关闭",
    "Progress: {percent:6.2f}%": "进度: {percent:6.2f}%",
    "Processing completed": "处理完成",
    "Error: {message}": "错误: {message}",
    "Unknown": "未知",
    "Not detected": "未检测到",
    "No usable NVIDIA GPU was detected; the renderer will use the CPU fallback.": "未检测到可用 NVIDIA GPU；GPU 绘图后端将回退 CPU。",
    "Could not read disk information: {error}": "磁盘信息读取失败: {error}",
    "Memory: {available} available / {total} total": "内存: {available} 可用 / {total} 总计",
    "Disk: {device} ({kind}, {free} free / {total} total)": "磁盘: {device}（{kind}，{free} 可用 / {total} 总计）",
    "GPU backend: detected; array acceleration selectable": "GPU后端: 已检测，可选择数组加速",
    "GPU backend: CPU fallback": "GPU后端: CPU回退",
    "Data: {files} files, {size:.1f} MB, {timesteps} timesteps, {levels} levels, {variables} variables, {dimension}D": "数据: {files} 个文件，{size:.1f} MB，{timesteps} 个时间步，{levels} 个层级，{variables} 个变量，{dimension}D",
    "Estimated timestep: {size:.1f} MB; storage: {storage}": "预计每时间步: {size:.1f} MB；存储类型: {storage}",
    "Slice position {position:g} is outside the selected {axis} range [{low:g}, {high:g}]": "切片坐标 {position:g} 不在所设 {axis} 范围 [{low:g}, {high:g}] 内",
    "No drawable data exists inside the selected spatial bounds": "所选空间输出范围内没有可绘制数据",
    "Timestep {timestep}: {error}": "时间步 {timestep}: {error}",
    "{count} additional errors omitted": "其余 {count} 项错误已省略",
    "Global color scan failed:\n{details}": "统一色标扫描失败:\n{details}",
    "Timestep {timestep}, variable {variable}: {error}": "时间步 {timestep}，变量 {variable}: {error}",
    "Timestep {timestep}, variable {variable}: no result was generated": "时间步 {timestep}，变量 {variable}: 未生成结果",
    "{count} items failed during generation:\n{details}": "生成过程中有 {count} 项失败:\n{details}",
    "Unsupported video format: {format}": "不支持的视频格式: {format}",
    "Select at least one video format": "请至少选择一种视频格式",
    "Could not read the first frame: {path}": "无法读取首帧图片: {path}",
    "Could not open the MP4 writer. Check opencv-python and the local codecs.": "MP4 视频写入器打开失败，请检查 opencv-python 安装和本机编码器支持",
    "MP4 generation failed because no valid output was written": "MP4 视频写入失败，没有生成有效文件",
    "GIF/JIF generation requires Pillow. Run: pip install Pillow": "生成 GIF/JIF 需要安装 Pillow，请先运行: pip install Pillow",
    "GIF/JIF generation failed because no usable frames were found": "GIF/JIF 生成失败，没有可用帧",
    "GIF/JIF generation failed because no valid output was written": "GIF/JIF 写入失败，没有生成有效文件",
    "No image frames are available for video generation": "没有可用于生成视频的图片帧",
    "Dataset path does not exist: {path}": "数据路径不存在: {path}",
    "No AMReX plotfile, .dat, or .plt data was recognized in: {path}": "未在目录中识别到 AMReX plotfile、.dat 或 .plt 数据: {path}",
    "No files matching plt_<timestep>_<CPU>_lev<level>.dat were found": "未找到名称符合 plt_时间步_CPU_lev层级.dat 的文件",
    "Reading AMReX plotfiles requires yt. Run: python -m pip install yt": "读取 AMReX plotfile 需要 yt。请执行: python -m pip install yt",
    "No AMReX plotfile directory was found": "未找到 AMReX plotfile 目录",
    "timestep-number": "时间步数字",
    "The selected directory contains multiple AMReX output series. Select one series directory:\n{candidates}": "所选目录中包含多个 AMReX 输出名称系列，请选择其中一个系列的目录：\n{candidates}",
    "Could not extract a timestep from the directory name; make it end with digits: {name}": "无法从目录名提取时间步，请使名称以数字结尾: {name}",
    "Duplicate timestep {timestep}: {first} and {second}": "发现重复时间步 {timestep}: {first} 和 {second}",
    "Not a Tecplot TDV112 file: {path}": "不是 Tecplot TDV112 文件: {path}",
    "Incomplete Tecplot header: {path}": "Tecplot 文件头不完整: {path}",
    "The selected directory contains multiple independent results. Select one result directory:\n{candidates}": "所选目录中包含多套独立计算结果，请选择其中一个具体结果目录：\n{candidates}",
    "Incomplete data block: expected {expected} values, found {actual}": "数据块不完整: 需要 {expected} 个值，实际 {actual} 个",
    "VARIABLES was not found before ZONE: {path}": "ZONE 前未找到 VARIABLES: {path}",
    "Incomplete data block: {path} Zone {zone} expected {expected} values, found {actual}": "数据块不完整: {path} Zone {zone} 需要 {expected} 个值，实际 {actual} 个",
    "Variable does not exist in the AMReX plotfile: {variable}": "AMReX plotfile 中不存在变量: {variable}",
    "Tecplot TDV112 binary field reading is not maintained. Use plot_format=0 or plot_format=1.": "当前软件不维护 Tecplot TDV112 二进制场数据读取；请改用 plot_format=0 或 plot_format=1。",
    "A slice position is required for 3D data": "三维数据必须指定切片坐标",
    "Unknown slice axis: {axis}": "未知切片方向: {axis}",
    "Could not compute dataset coordinate bounds": "无法计算数据坐标范围",
    "{dimension}D data has no {axis} bounds": "{dimension}D 数据没有 {axis} 方向范围",
    "2D AMR fields and parallel X/Y/Z slices for 3D data": "二维 AMR 云图与三维 X/Y/Z 平行切片批处理工具",
    "Language: auto, en, or zh_CN": "语言：auto、en 或 zh_CN",
    "Inspect dataset format and metadata": "检查数据格式和元数据",
    "Dataset directory": "计算结果目录",
    "Render images or videos in batches": "批量生成图片或视频",
    "Output directory": "输出目录",
    "Comma-separated variables, for example rho,p,T": "变量列表，例如 rho,p,T",
    "AMR levels, for example 0,1,2; default: all": "层级列表，例如 0,1,2；默认 all",
    "Timesteps, for example 0,500,1000-3000; default: all": "时间步，例如 0,500,1000-3000；默认 all",
    "3D slice normal": "三维切片法向",
    "3D slice position; defaults to the midpoint": "三维切片坐标；未给出时使用该方向中点",
    "X output range; defaults to full bounds": "X方向输出范围；默认完整边界",
    "Y output range; defaults to full bounds": "Y方向输出范围；默认完整边界",
    "Z output range; defaults to full bounds for 3D data": "Z方向输出范围；三维数据默认完整边界",
    "Use a symmetric color range": "使用正负对称色标",
    "Use one color range for all timesteps": "所有时间步使用统一色标",
    "Worker processes; 0 selects automatically": "并行进程数；0 表示根据硬件和数据规模自动选择",
    "Force regeneration instead of skipping existing output": "不跳过已有结果，强制重新生成",
    "Disable GPU acceleration and force NumPy/CPU": "禁用 GPU 数组加速，强制使用 NumPy/CPU",
    "Disable the Tecplot ASCII binary cache": "禁用 Tecplot ASCII 二进制缓存",
    "Video format; both writes MP4 and GIF": "视频格式；gif 与 jif 等价，both 表示同时生成 MP4 和 GIF",
    "Hide the title field above the plot": "隐藏图像顶部字段",
    "Dataset: {path}": "数据目录: {path}",
    "Input format: {format}": "输入格式: {format}",
    "Dimension: {dimension}D": "数据维度: {dimension}D",
    "Timesteps: {timesteps}": "时间步: {timesteps}",
    "AMR levels: {levels}": "AMR层级: {levels}",
    "Variables: {variables}": "变量: {variables}",
    "not read": "未读取",
    "Coordinate bounds:": "坐标范围:",
    "Dataset does not contain variables: {variables}": "数据中不存在变量: {variables}",
    "Dataset does not contain levels: {levels}": "数据中不存在层级: {levels}",
    "No valid timesteps were selected": "没有选中有效时间步",
    "2D data cannot use a Z output range": "二维数据不能设置Z输出范围",
    "Schedule: {workers} processes; data about {size:.1f} MB; GPU backend={backend}; ASCII cache={cache}": "调度: {workers} 个进程；数据约 {size:.1f} MB；GPU数组后端={backend}；ASCII缓存={cache}",
    "{variable}: {count} results": "{variable}: {count} 个结果",
    "usage: ": "用法: ",
    "positional arguments": "位置参数",
    "options": "选项",
    "show this help message and exit": "显示此帮助信息并退出",
    "the following arguments are required: %s": "缺少必需参数: %s",
    "argument %(argument_name)s: %(message)s": "参数 %(argument_name)s: %(message)s",
    "invalid choice: %(value)r (choose from %(choices)s)": "无效选择: %(value)r（可选值: %(choices)s）",
}


def _settings_path() -> Path:
    system = platform.system()
    if system == "Windows":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return root / "AMReX_SliceViewer" / "settings.json"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "AMReX_SliceViewer" / "settings.json"
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "amrex_sliceviewer" / "settings.json"


def detect_system_language() -> str:
    candidates = [
        os.environ.get("AMREX_VIEWER_LANG"),
        os.environ.get("LANGUAGE"),
        os.environ.get("LC_ALL"),
        os.environ.get("LC_MESSAGES"),
        os.environ.get("LANG"),
    ]
    try:
        candidates.append(locale.getlocale()[0])
    except (ValueError, TypeError):
        pass
    for value in candidates:
        normalized = str(value or "").strip().lower().replace("-", "_")
        if not normalized or normalized in {"auto", "system", "default"}:
            continue
        if normalized.startswith("zh"):
            return "zh_CN"
        return "en"
    return "en"


def normalize_language(value: str | None, resolve_auto: bool = True) -> str:
    normalized = (value or "auto").strip().lower().replace("-", "_")
    if normalized in {"auto", "system", "default"}:
        return detect_system_language() if resolve_auto else "auto"
    if normalized.startswith("zh"):
        return "zh_CN"
    return "en"


def load_language_preference() -> str:
    environment = os.environ.get("AMREX_VIEWER_LANG")
    if environment:
        return normalize_language(environment, resolve_auto=False)
    try:
        payload = json.loads(_settings_path().read_text(encoding="utf-8"))
        return normalize_language(payload.get("language"), resolve_auto=False)
    except (OSError, ValueError, TypeError, AttributeError):
        return "auto"


def save_language_preference(preference: str) -> None:
    preference = normalize_language(preference, resolve_auto=False)
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"language": preference}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


_language_preference = load_language_preference()
_current_language = normalize_language(_language_preference)


def set_language(preference: str | None) -> str:
    global _language_preference, _current_language
    _language_preference = normalize_language(preference, resolve_auto=False)
    os.environ["AMREX_VIEWER_LANG"] = _language_preference
    _current_language = normalize_language(_language_preference)
    return _current_language


def get_language() -> str:
    return _current_language


def get_language_preference() -> str:
    return _language_preference


def tr(message: str, **values) -> str:
    template = _ZH.get(message, message) if _current_language == "zh_CN" else message
    return template.format(**values) if values else template


def tr_for(language: str | None, message: str, **values) -> str:
    selected = normalize_language(language) if language else _current_language
    template = _ZH.get(message, message) if selected == "zh_CN" else message
    return template.format(**values) if values else template


__all__ = [
    "LANGUAGE_PREFERENCES",
    "SUPPORTED_LANGUAGES",
    "detect_system_language",
    "get_language",
    "get_language_preference",
    "load_language_preference",
    "normalize_language",
    "save_language_preference",
    "set_language",
    "tr",
    "tr_for",
]
