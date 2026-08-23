"""Cross-platform display, font, and desktop integration helpers."""

from __future__ import annotations

import ctypes
import os
import platform
import subprocess
from pathlib import Path


def configure_high_dpi() -> None:
    """Enable native DPI handling on Windows before Tk creates a window."""

    if platform.system() != "Windows":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _font_candidates(language: str) -> list[str]:
    system = platform.system()
    if language.startswith("zh"):
        if system == "Windows":
            return ["Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "Segoe UI"]
        if system == "Darwin":
            return ["PingFang SC", "Hiragino Sans GB", "Heiti SC", "Helvetica Neue"]
        return ["Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "DejaVu Sans"]
    if system == "Windows":
        return ["Segoe UI", "Arial", "Microsoft YaHei UI"]
    if system == "Darwin":
        return ["SF Pro Text", "Helvetica Neue", "Arial", "PingFang SC"]
    return ["Noto Sans", "DejaVu Sans", "Liberation Sans"]


def choose_tk_font(root, language: str) -> str:
    import tkinter.font as tkfont

    installed = {name.casefold(): name for name in tkfont.families(root)}
    for candidate in _font_candidates(language):
        match = installed.get(candidate.casefold())
        if match:
            return match
    return "TkDefaultFont"


def configure_tk_fonts(root, language: str) -> str:
    import tkinter.font as tkfont

    family = choose_tk_font(root, language)
    if family == "TkDefaultFont":
        family = tkfont.nametofont("TkDefaultFont", root=root).cget("family")
    for name in (
        "TkDefaultFont",
        "TkTextFont",
        "TkFixedFont",
        "TkMenuFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkSmallCaptionFont",
        "TkIconFont",
        "TkTooltipFont",
    ):
        try:
            tkfont.nametofont(name, root=root).configure(family=family)
        except Exception:
            continue
    return str(family)


def available_plot_fonts(language: str) -> list[str]:
    try:
        from matplotlib import font_manager

        installed = {font.name.casefold(): font.name for font in font_manager.fontManager.ttflist}
    except ImportError:
        installed = {}
    candidates = _font_candidates(language) + [
        "Noto Sans CJK SC",
        "Microsoft YaHei",
        "PingFang SC",
        "DejaVu Sans",
        "Arial",
        "Times New Roman",
        "Liberation Sans",
        "sans-serif",
        "serif",
    ]
    result: list[str] = []
    for candidate in candidates:
        value = installed.get(candidate.casefold(), candidate if candidate in {"sans-serif", "serif"} else None)
        if value and value not in result:
            result.append(value)
    return result or ["DejaVu Sans", "sans-serif"]


def default_plot_font(language: str = "en") -> str:
    return available_plot_fonts(language)[0]


def resolve_plot_font(requested: str, language: str = "en") -> str:
    available = available_plot_fonts(language)
    if requested in {"sans-serif", "serif"}:
        return requested
    try:
        from matplotlib import font_manager

        installed = {font.name.casefold(): font.name for font in font_manager.fontManager.ttflist}
        match = installed.get(requested.casefold())
        if match:
            return match
    except ImportError:
        pass
    return available[0]


def open_in_file_manager(path: str | os.PathLike[str]) -> None:
    target = str(Path(path).resolve())
    system = platform.system()
    if system == "Windows":
        os.startfile(target)
    elif system == "Darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


__all__ = [
    "available_plot_fonts",
    "choose_tk_font",
    "configure_high_dpi",
    "configure_tk_fonts",
    "default_plot_font",
    "open_in_file_manager",
    "resolve_plot_font",
]
