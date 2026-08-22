"""Unified readers and slice extraction for AMR post-processing."""

from __future__ import annotations

import os
import hashlib
import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


DTYPE = np.float32
ASCII_CACHE_VERSION = 1
ASCII_NAME_RE = re.compile(
    r"plt_(\d+)_(\d+)_lev(\d+)\.dat$", re.IGNORECASE
)
PLOTFILE_NAME_RE = re.compile(r"^(.*?)(\d+)$")


class DatasetError(RuntimeError):
    """Base exception for dataset discovery and reading failures."""


class UnsupportedFormatError(DatasetError):
    """Raised when a dataset format is known but unavailable."""


def normalize_input_path(path: str) -> str:
    """Accept Linux, WSL UNC, and Windows drive-letter paths."""

    value = str(path).strip().strip("\"'")
    value = value.replace("\\", "/")
    match = re.match(r"^//wsl(?:\.localhost|\$)/[^/]+(/.*)$", value, re.I)
    if match:
        value = match.group(1)
    elif os.name != "nt":
        drive_match = re.match(r"^([a-z]):(?:/(.*))?$", value, re.I)
        if drive_match:
            drive = drive_match.group(1).lower()
            remainder = drive_match.group(2) or ""
            value = f"/mnt/{drive}/{remainder}"
    return str(Path(value).expanduser())


@dataclass
class ZoneData:
    """One rectilinear AMR patch using cell-centered physical variables."""

    zone_name: str
    level: int
    solutiontime: float
    x_edges: np.ndarray
    y_edges: np.ndarray
    z_edges: Optional[np.ndarray]
    data: Dict[str, np.ndarray]

    @property
    def dimension(self) -> int:
        return 3 if self.z_edges is not None and len(self.z_edges) > 2 else 2


@dataclass
class Patch2D:
    """A two-dimensional patch ready for pcolormesh rendering."""

    level: int
    x_edges: np.ndarray
    y_edges: np.ndarray
    values: np.ndarray
    x_label: str
    y_label: str


@dataclass
class DatasetMetadata:
    """Pickle-friendly metadata shared by the GUI, CLI and workers."""

    root: str
    source_format: str
    dimension: int
    variables: List[str]
    timesteps: List[int]
    levels: List[int]
    sources: Dict[int, Dict[int, List[str]]] = field(default_factory=dict)
    plotfiles: Dict[int, str] = field(default_factory=dict)
    bounds: Optional[Tuple[Tuple[float, float], ...]] = None

    @property
    def format_label(self) -> str:
        return {
            "tecplot_ascii": "Tecplot ASCII",
            "amrex_plotfile": "AMReX Plotfile",
            "tecplot_binary": "Tecplot Binary (TDV112)",
        }.get(self.source_format, self.source_format)


def _is_amrex_plotfile(path: Path) -> bool:
    return (
        (path / "Header").is_file()
        and (path / "Level_0" / "Cell_H").is_file()
    )


def _plotfile_name_parts(path: Path) -> Tuple[str, Optional[int]]:
    match = PLOTFILE_NAME_RE.match(path.name)
    if not match:
        return path.name, None
    return match.group(1), int(match.group(2))


def _find_plotfile_dirs(root: Path) -> List[Path]:
    candidates: List[Path] = []
    if _is_amrex_plotfile(root):
        candidates.append(root)
    for header in root.rglob("Header"):
        parent = header.parent
        if _is_amrex_plotfile(parent):
            candidates.append(parent)
    return sorted(
        set(candidates),
        key=lambda path: (
            str(path.parent),
            _plotfile_name_parts(path)[0],
            _plotfile_name_parts(path)[1] if _plotfile_name_parts(path)[1] is not None else -1,
        ),
    )


def detect_dataset_format(path: str) -> str:
    root = Path(normalize_input_path(path)).resolve()
    if not root.exists():
        raise DatasetError(f"数据路径不存在: {root}")

    if _find_plotfile_dirs(root):
        return "amrex_plotfile"
    if any(root.rglob("*.dat")):
        return "tecplot_ascii"
    binary_files = list(root.rglob("*.plt"))
    if binary_files:
        try:
            if binary_files[0].read_bytes()[:8].startswith(b"#!TDV"):
                return "tecplot_binary"
        except OSError:
            pass
    raise DatasetError(f"未在目录中识别到 AMReX plotfile、.dat 或 .plt 数据: {root}")


def _parse_variables_line(line: str) -> List[str]:
    if "=" not in line:
        return []
    return re.findall(r'"([^"]+)"', line.split("=", 1)[1])


def _read_ascii_header(path: Path) -> Tuple[List[str], int, float]:
    variables: List[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            lower = stripped.lower()
            if lower.startswith("variables"):
                variables = _parse_variables_line(stripped)
            elif lower.startswith("zone"):
                k_match = re.search(r"(?<![a-z])k\s*=\s*(\d+)", stripped, re.I)
                time_match = re.search(r"solutiontime\s*=\s*([0-9eE.+\-]+)", stripped, re.I)
                k = int(k_match.group(1)) if k_match else 1
                time = float(time_match.group(1)) if time_match else 0.0
                return variables, 3 if k > 1 else 2, time
    return variables, 2, 0.0


def _scan_ascii(root: Path) -> DatasetMetadata:
    sources: Dict[int, Dict[int, List[str]]] = {}
    matched_files: List[Path] = []
    for path in root.rglob("*.dat"):
        match = ASCII_NAME_RE.match(path.name)
        if not match:
            continue
        timestep, _, level = map(int, match.groups())
        sources.setdefault(timestep, {}).setdefault(level, []).append(str(path))
        matched_files.append(path)

    if not matched_files:
        raise DatasetError("未找到名称符合 plt_时间步_CPU_lev层级.dat 的文件")

    for timestep_data in sources.values():
        for paths in timestep_data.values():
            paths.sort()

    variables, dimension, _ = _read_ascii_header(sorted(matched_files)[0])
    levels = sorted({level for item in sources.values() for level in item})
    return DatasetMetadata(
        root=str(root),
        source_format="tecplot_ascii",
        dimension=dimension,
        variables=variables,
        timesteps=sorted(sources),
        levels=levels,
        sources=sources,
    )


def _import_yt():
    try:
        import yt
    except ImportError as exc:
        raise UnsupportedFormatError(
            "读取 AMReX plotfile 需要 yt。请执行: python -m pip install yt"
        ) from exc
    return yt


def _scan_plotfiles(root: Path) -> DatasetMetadata:
    yt = _import_yt()
    plot_dirs = _find_plotfile_dirs(root)
    if not plot_dirs:
        raise DatasetError("未找到 AMReX plotfile 目录")

    series = {
        (path.parent, _plotfile_name_parts(path)[0])
        for path in plot_dirs
        if _plotfile_name_parts(path)[1] is not None
    }
    if len(series) > 1:
        names = "\n".join(
            f"  - {parent / (prefix + '时间步数字')}"
            for parent, prefix in sorted(series, key=lambda item: (str(item[0]), item[1]))
        )
        raise DatasetError(
            "所选目录中包含多个 AMReX 输出名称系列，请选择其中一个系列的目录：\n"
            f"{names}"
        )

    plotfiles: Dict[int, str] = {}
    for path in plot_dirs:
        _, timestep = _plotfile_name_parts(path)
        if timestep is None:
            if len(plot_dirs) == 1:
                timestep = 0
            else:
                raise DatasetError(
                    f"无法从目录名提取时间步，请使名称以数字结尾: {path.name}"
                )
        if timestep in plotfiles:
            raise DatasetError(
                f"发现重复时间步 {timestep}: {plotfiles[timestep]} 和 {path}"
            )
        plotfiles[timestep] = str(path)
    first = plot_dirs[0]
    ds = yt.load(str(first))
    variables = sorted(
        field_name for field_type, field_name in ds.field_list
        if field_type in ("boxlib", "amrex")
    )
    dimension = int(ds.dimensionality)
    levels = list(range(int(ds.max_level) + 1))
    edges = [
        (float(ds.domain_left_edge[i]), float(ds.domain_right_edge[i]))
        for i in range(dimension)
    ]
    return DatasetMetadata(
        root=str(root),
        source_format="amrex_plotfile",
        dimension=dimension,
        variables=variables,
        timesteps=sorted(plotfiles),
        levels=levels,
        plotfiles=plotfiles,
        bounds=tuple(edges),
    )


def _scan_binary(root: Path) -> DatasetMetadata:
    sources: Dict[int, Dict[int, List[str]]] = {}
    variables: List[str] = []
    dimension = 2
    first_file: Optional[Path] = None
    for path in root.rglob("*.plt"):
        match = re.match(r"plt_(\d+)_(\d+)_lev(\d+)\.plt$", path.name, re.I)
        if not match:
            continue
        if first_file is None:
            first_file = path
        timestep, _, level = map(int, match.groups())
        sources.setdefault(timestep, {}).setdefault(level, []).append(str(path))
    for timestep_data in sources.values():
        for paths in timestep_data.values():
            paths.sort()
    if first_file is not None:
        variables, dimension = _read_binary_header(first_file)
    levels = sorted({level for item in sources.values() for level in item})
    return DatasetMetadata(
        root=str(root),
        source_format="tecplot_binary",
        dimension=dimension,
        variables=variables,
        timesteps=sorted(sources),
        levels=levels,
        sources=sources,
    )


def _read_binary_header(path: Path) -> Tuple[List[str], int]:
    """Read variable names and ordered-zone dimensions from TDV112 metadata."""

    with path.open("rb") as handle:
        if handle.read(8) != b"#!TDV112":
            raise DatasetError(f"不是 Tecplot TDV112 文件: {path}")

        def read_int() -> int:
            raw = handle.read(4)
            if len(raw) != 4:
                raise DatasetError(f"Tecplot 文件头不完整: {path}")
            return struct.unpack("<i", raw)[0]

        def read_double() -> float:
            raw = handle.read(8)
            if len(raw) != 8:
                raise DatasetError(f"Tecplot 文件头不完整: {path}")
            return struct.unpack("<d", raw)[0]

        def read_string() -> str:
            characters = []
            while True:
                value = read_int()
                if value == 0:
                    return "".join(characters)
                characters.append(chr(value))

        read_int()  # Byte order.
        read_int()  # File type.
        read_string()  # Dataset title.
        variable_count = read_int()
        variables = [read_string() for _ in range(variable_count)]

        marker_raw = handle.read(4)
        if len(marker_raw) != 4 or not np.isclose(struct.unpack("<f", marker_raw)[0], 299.0):
            return variables, 2

        read_string()  # Zone title.
        read_int()  # Parent zone.
        read_int()  # Strand ID.
        read_double()  # Solution time.
        read_int()  # Zone color.
        zone_type = read_int()
        specify_locations = read_int()
        if specify_locations:
            for _ in range(variable_count):
                read_int()
        read_int()  # Raw local face-neighbor flag.
        read_int()  # User-defined face-neighbor count.

        if zone_type != 0:
            return variables, 2
        read_int()  # IMax
        read_int()  # JMax
        k_max = read_int()
        has_3d_components = bool({"w", "qz"} & {name.lower() for name in variables})
        # CAPEX 2D TDV files use two nodal K planes for one cell layer.
        return variables, 3 if k_max > 2 or has_3d_components else 2


def scan_dataset(path: str) -> DatasetMetadata:
    root = Path(normalize_input_path(path)).resolve()
    source_format = detect_dataset_format(str(root))
    _validate_single_dataset_root(root, source_format)
    if source_format == "tecplot_ascii":
        return _scan_ascii(root)
    if source_format == "amrex_plotfile":
        return _scan_plotfiles(root)
    return _scan_binary(root)


def _validate_single_dataset_root(root: Path, source_format: str) -> None:
    """Reject a parent directory containing several independent result sets."""

    containers = set()
    if source_format == "amrex_plotfile":
        for path in _find_plotfile_dirs(root):
            container = path.parent
            if container.name.lower() == "fluid":
                container = container.parent
            containers.add(container)
    else:
        suffix = ".dat" if source_format == "tecplot_ascii" else ".plt"
        pattern = ASCII_NAME_RE if suffix == ".dat" else re.compile(
            r"plt_(\d+)_(\d+)_lev(\d+)\.plt$", re.I
        )
        for path in root.rglob(f"*{suffix}"):
            if not pattern.match(path.name):
                continue
            container = path.parent
            if re.fullmatch(r"lev\d+", container.name, re.I):
                container = container.parent
            containers.add(container)

    if len(containers) <= 1:
        return
    choices = "\n".join(f"  - {path}" for path in sorted(containers))
    raise DatasetError(
        "所选目录中包含多套独立计算结果，请选择其中一个具体结果目录：\n"
        f"{choices}"
    )


def _parse_zone_header(line: str) -> Dict[str, object]:
    result: Dict[str, object] = {}
    title_match = re.search(r"\bt\s*=\s*([^\s,]+)", line, re.I)
    if title_match:
        result["title"] = title_match.group(1)
    for dim in ("i", "j", "k"):
        match = re.search(rf"(?<![a-z]){dim}\s*=\s*(\d+)", line, re.I)
        result[dim] = int(match.group(1)) if match else 1
    time_match = re.search(r"solutiontime\s*=\s*([0-9eE.+\-]+)", line, re.I)
    result["time"] = float(time_match.group(1)) if time_match else 0.0
    packing_match = re.search(r"datapacking\s*=\s*([a-z]+)", line, re.I)
    result["datapacking"] = packing_match.group(1).lower() if packing_match else "block"
    locations = set()
    location_match = re.search(r"VARLOCATION\s*=\s*\((.+?)\)", line, re.I)
    if location_match:
        for part in re.finditer(
            r"\[(\d+)(?:-(\d+))?\]\s*=\s*CELLCENTERED",
            location_match.group(1),
            re.I,
        ):
            start = int(part.group(1))
            end = int(part.group(2) or start)
            locations.update(range(start, end + 1))
    result["cellcentered"] = locations
    return result


def _read_float_block(handle, count: int) -> np.ndarray:
    chunks: List[str] = []
    found = 0
    while found < count:
        line = handle.readline()
        if not line:
            break
        stripped = line.strip()
        if not stripped:
            continue
        chunks.append(stripped)
        found += len(stripped.split())
    if not chunks:
        return np.empty(0, dtype=DTYPE)
    values = np.fromstring(" ".join(chunks), sep=" ", dtype=DTYPE)
    if values.size < count:
        raise DatasetError(f"数据块不完整: 需要 {count} 个值，实际 {values.size} 个")
    return values[:count]


def _skip_float_block(handle, count: int) -> None:
    found = 0
    while found < count:
        line = handle.readline()
        if not line:
            break
        found += len(line.split())


def _edge_axis(array: np.ndarray, axis: str) -> np.ndarray:
    if array.ndim == 2:
        return array[0, :] if axis == "x" else array[:, 0]
    if axis == "x":
        return array[0, 0, :]
    if axis == "y":
        return array[0, :, 0]
    return array[:, 0, 0]


def _ascii_cache_dir(root: str) -> Path:
    return Path(root) / ".amrex_viewer_cache"


def _ascii_cache_path(filepath: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(str(Path(filepath).resolve()).encode("utf-8")).hexdigest()
    return cache_dir / f"{digest[:32]}.npz"


def _select_cached_zones(
    zones: List[ZoneData], needed_vars: Sequence[str]
) -> List[ZoneData]:
    needed = set(needed_vars)
    return [
        ZoneData(
            zone_name=zone.zone_name,
            level=zone.level,
            solutiontime=zone.solutiontime,
            x_edges=zone.x_edges,
            y_edges=zone.y_edges,
            z_edges=zone.z_edges,
            data={name: values for name, values in zone.data.items() if name in needed},
        )
        for zone in zones
    ]


def _load_ascii_cache(
    filepath: str,
    level: int,
    cache_dir: Path,
    needed_vars: Optional[Sequence[str]] = None,
) -> Optional[List[ZoneData]]:
    cache_path = _ascii_cache_path(filepath, cache_dir)
    if not cache_path.is_file():
        return None
    try:
        source = Path(filepath)
        stat = source.stat()
        with np.load(cache_path, allow_pickle=False) as archive:
            metadata = json.loads(str(archive["metadata"].item()))
            if (
                metadata.get("version") != ASCII_CACHE_VERSION
                or metadata.get("level") != level
                or metadata.get("size") != stat.st_size
                or metadata.get("mtime_ns") != stat.st_mtime_ns
            ):
                return None
            wanted = set(needed_vars or ())
            zones = []
            for index, item in enumerate(metadata["zones"]):
                prefix = f"z{index}_"
                data = {
                    name: archive[f"{prefix}var_{name}"].copy()
                    for name in item["variables"]
                    if not wanted or name in wanted
                }
                zones.append(
                    ZoneData(
                        zone_name=item["zone_name"],
                        level=level,
                        solutiontime=float(item["solutiontime"]),
                        x_edges=archive[f"{prefix}x"].copy(),
                        y_edges=archive[f"{prefix}y"].copy(),
                        z_edges=(
                            archive[f"{prefix}z"].copy()
                            if item["has_z"]
                            else None
                        ),
                        data=data,
                    )
                )
            return zones
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return None


def ascii_cache_is_valid(filepath: str, level: int, cache_dir: Path) -> bool:
    """Check cache metadata without loading field arrays."""

    cache_path = _ascii_cache_path(filepath, cache_dir)
    if not cache_path.is_file():
        return False
    try:
        source = Path(filepath)
        stat = source.stat()
        with np.load(cache_path, allow_pickle=False) as archive:
            metadata = json.loads(str(archive["metadata"].item()))
        return (
            metadata.get("version") == ASCII_CACHE_VERSION
            and metadata.get("level") == level
            and metadata.get("size") == stat.st_size
            and metadata.get("mtime_ns") == stat.st_mtime_ns
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return False


def _write_ascii_cache(filepath: str, level: int, cache_dir: Path, zones: List[ZoneData]) -> None:
    try:
        source = Path(filepath)
        stat = source.stat()
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = _ascii_cache_path(filepath, cache_dir)
        metadata = {
            "version": ASCII_CACHE_VERSION,
            "source": str(source.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "level": level,
            "zones": [],
        }
        arrays = {}
        for index, zone in enumerate(zones):
            prefix = f"z{index}_"
            metadata["zones"].append(
                {
                    "zone_name": zone.zone_name,
                    "solutiontime": zone.solutiontime,
                    "has_z": zone.z_edges is not None,
                    "variables": sorted(zone.data),
                }
            )
            arrays[f"{prefix}x"] = np.asarray(zone.x_edges, dtype=DTYPE)
            arrays[f"{prefix}y"] = np.asarray(zone.y_edges, dtype=DTYPE)
            if zone.z_edges is not None:
                arrays[f"{prefix}z"] = np.asarray(zone.z_edges, dtype=DTYPE)
            for name, values in zone.data.items():
                arrays[f"{prefix}var_{name}"] = np.asarray(values, dtype=DTYPE)
        arrays["metadata"] = np.asarray(json.dumps(metadata, ensure_ascii=False))
        temporary = cache_path.with_name(cache_path.name + ".tmp")
        np.savez(temporary, **arrays)
        temporary_npz = Path(str(temporary) + ".npz")
        os.replace(temporary_npz, cache_path)
    except (OSError, ValueError):
        # Caching is an optimization; a read must still succeed when the cache
        # directory is read-only or the disk runs out of space.
        return


def _read_ascii_file_linewise(
    filepath: str,
    needed_vars: Sequence[str],
    level: int,
    keep_all: bool = False,
) -> List[ZoneData]:
    keep = set(needed_vars) | {"x", "y", "z"}
    zones: List[ZoneData] = []
    variables: List[str] = []

    with open(
        filepath,
        "r",
        encoding="utf-8",
        errors="replace",
        buffering=8 * 1024 * 1024,
    ) as handle:
        while True:
            line = handle.readline()
            if not line:
                break
            stripped = line.strip()
            lower = stripped.lower()
            if lower.startswith("variables"):
                variables = _parse_variables_line(stripped)
                if keep_all:
                    keep = set(variables) | {"x", "y", "z"}
                continue
            if not lower.startswith("zone"):
                continue
            if not variables:
                raise DatasetError(f"ZONE 前未找到 VARIABLES: {filepath}")

            header = _parse_zone_header(stripped)
            ni, nj, nk = int(header["i"]), int(header["j"]), int(header["k"])
            cellcentered = header["cellcentered"]
            loaded: Dict[str, np.ndarray] = {}

            for index, name in enumerate(variables, start=1):
                is_cell = index in cellcentered
                if is_cell:
                    nci, ncj = max(ni - 1, 1), max(nj - 1, 1)
                    nck = max(nk - 1, 1) if nk > 1 else 1
                    count = nci * ncj * nck
                else:
                    count = ni * nj * nk

                if name not in keep:
                    _skip_float_block(handle, count)
                    continue
                values = _read_float_block(handle, count)
                if is_cell:
                    if nk > 1:
                        loaded[name] = values.reshape((nck, ncj, nci))
                    else:
                        loaded[name] = values.reshape((ncj, nci))
                else:
                    if nk > 1:
                        loaded[name] = values.reshape((nk, nj, ni))
                    else:
                        loaded[name] = values.reshape((nj, ni))

            if "x" not in loaded or "y" not in loaded:
                continue
            x_edges = _edge_axis(loaded.pop("x"), "x").astype(DTYPE, copy=False)
            y_edges = _edge_axis(loaded.pop("y"), "y").astype(DTYPE, copy=False)
            z_array = loaded.pop("z", None)
            z_edges = None
            if nk > 1 and z_array is not None:
                z_edges = _edge_axis(z_array, "z").astype(DTYPE, copy=False)
            zones.append(
                ZoneData(
                    zone_name=str(header.get("title", "")),
                    level=level,
                    solutiontime=float(header["time"]),
                    x_edges=x_edges,
                    y_edges=y_edges,
                    z_edges=z_edges,
                    data=loaded,
                )
            )
    return zones


def _read_ascii_file_zone_fast(
    filepath: str,
    needed_vars: Sequence[str],
    level: int,
    keep_all: bool = False,
) -> Optional[List[ZoneData]]:
    """Parse Tecplot BLOCK zones with one NumPy conversion per Zone.

    The parser is intentionally limited to structured BLOCK data. POINT or
    malformed zones return None so the established line-wise parser can take
    over without changing compatibility behavior.
    """

    keep = set(needed_vars) | {"x", "y", "z"}
    with open(
        filepath,
        "r",
        encoding="utf-8",
        errors="replace",
        buffering=8 * 1024 * 1024,
    ) as handle:
        content = handle.read()

    variable_match = re.search(
        r"(?im)^[ \t]*variables\s*=\s*[^\r\n]*",
        content,
    )
    if variable_match is None:
        return None
    variables = _parse_variables_line(variable_match.group(0).strip())
    if not variables:
        return None
    if keep_all:
        keep = set(variables) | {"x", "y", "z"}

    zone_matches = list(
        re.finditer(
            r"(?im)^[ \t]*zone\b[^\r\n]*(?:\r\n|\n|\r|$)",
            content,
        )
    )
    if not zone_matches:
        return None

    zones: List[ZoneData] = []
    for index, match in enumerate(zone_matches):
        header_line = match.group(0).strip()
        header = _parse_zone_header(header_line)
        if header.get("datapacking", "block") != "block":
            return None

        ni, nj, nk = int(header["i"]), int(header["j"]), int(header["k"])
        cellcentered = header["cellcentered"]
        next_start = (
            zone_matches[index + 1].start()
            if index + 1 < len(zone_matches)
            else len(content)
        )
        counts = []
        total_count = 0
        for variable_index, name in enumerate(variables, start=1):
            is_cell = variable_index in cellcentered
            if is_cell:
                nci, ncj = max(ni - 1, 1), max(nj - 1, 1)
                nck = max(nk - 1, 1) if nk > 1 else 1
                count = nci * ncj * nck
            else:
                count = ni * nj * nk
            counts.append((name, is_cell, count))
            total_count += count

        # count stops conversion before optional non-numeric Tecplot footer
        # text, while still converting the complete Zone in one NumPy call.
        values = np.fromstring(
            content[match.end():next_start],
            sep=" ",
            dtype=DTYPE,
            count=total_count,
        )

        if values.size < total_count:
            raise DatasetError(
                f"数据块不完整: {filepath} Zone {index} 需要 "
                f"{total_count} 个值，实际 {values.size} 个"
            )

        loaded: Dict[str, np.ndarray] = {}
        offset = 0
        for name, is_cell, count in counts:
            block = values[offset:offset + count]
            offset += count
            if name not in keep:
                continue
            if is_cell:
                if nk > 1:
                    shape = (max(nk - 1, 1), max(nj - 1, 1), max(ni - 1, 1))
                else:
                    shape = (max(nj - 1, 1), max(ni - 1, 1))
            else:
                shape = (nk, nj, ni) if nk > 1 else (nj, ni)
            array = block.reshape(shape)
            # Keep one conversion buffer for full-cache builds. For direct
            # reads, copy selected arrays so unused variables are released.
            loaded[name] = array if keep_all else array.copy()

        if "x" not in loaded or "y" not in loaded:
            continue
        x_edges = _edge_axis(loaded.pop("x"), "x").astype(DTYPE, copy=False)
        y_edges = _edge_axis(loaded.pop("y"), "y").astype(DTYPE, copy=False)
        z_array = loaded.pop("z", None)
        z_edges = None
        if nk > 1 and z_array is not None:
            z_edges = _edge_axis(z_array, "z").astype(DTYPE, copy=False)
        zones.append(
            ZoneData(
                zone_name=str(header.get("title", "")),
                level=level,
                solutiontime=float(header["time"]),
                x_edges=x_edges,
                y_edges=y_edges,
                z_edges=z_edges,
                data=loaded,
            )
        )
    return zones


def _read_ascii_file_uncached(
    filepath: str,
    needed_vars: Sequence[str],
    level: int,
    keep_all: bool = False,
) -> List[ZoneData]:
    if not keep_all:
        # For a small subset of variables, avoid converting every field just
        # to discard most of it. Cache builds still use the fast full-Zone path.
        try:
            with open(
                filepath,
                "r",
                encoding="utf-8",
                errors="replace",
                buffering=64 * 1024,
            ) as handle:
                header = handle.read(8192)
            variable_match = re.search(
                r"(?im)^[ \t]*variables\s*=\s*[^\r\n]*",
                header,
            )
            if variable_match:
                all_variables = _parse_variables_line(variable_match.group(0).strip())
                selected = set(needed_vars) | {"x", "y", "z"}
                if all_variables and len(selected) * 2 < len(all_variables):
                    return _read_ascii_file_linewise(
                        filepath,
                        needed_vars,
                        level,
                        keep_all=keep_all,
                    )
        except OSError:
            pass
    fast_zones = _read_ascii_file_zone_fast(
        filepath,
        needed_vars,
        level,
        keep_all=keep_all,
    )
    if fast_zones is not None:
        return fast_zones
    return _read_ascii_file_linewise(filepath, needed_vars, level, keep_all=keep_all)


def read_ascii_file(
    filepath: str,
    needed_vars: Sequence[str],
    level: int,
    cache_dir: Optional[Path] = None,
) -> List[ZoneData]:
    if cache_dir is None:
        return _read_ascii_file_uncached(filepath, needed_vars, level)

    cached = _load_ascii_cache(filepath, level, cache_dir, needed_vars)
    if cached is not None:
        return _select_cached_zones(cached, needed_vars)

    zones = _read_ascii_file_uncached(
        filepath,
        needed_vars,
        level,
        keep_all=True,
    )
    _write_ascii_cache(filepath, level, cache_dir, zones)
    return _select_cached_zones(zones, needed_vars)


def _yt_field(ds, variable: str):
    for candidate in (("boxlib", variable), ("amrex", variable), ("gas", variable)):
        if candidate in ds.field_list or candidate in ds.derived_field_list:
            return candidate
    raise DatasetError(f"AMReX plotfile 中不存在变量: {variable}")


def _load_plotfile(
    metadata: DatasetMetadata,
    timestep: int,
    variables: Sequence[str],
    levels: Sequence[int],
) -> Dict[int, List[ZoneData]]:
    yt = _import_yt()
    path = metadata.plotfiles.get(timestep)
    if not path:
        return {}
    ds = yt.load(path)
    selected = set(levels)
    fields = {name: _yt_field(ds, name) for name in variables}
    result: Dict[int, List[ZoneData]] = {level: [] for level in levels}

    for grid in ds.index.grids:
        level = int(grid.Level)
        if level not in selected:
            continue
        dims = np.asarray(grid.ActiveDimensions, dtype=int)
        left = np.asarray(grid.LeftEdge, dtype=float)
        right = np.asarray(grid.RightEdge, dtype=float)
        x_edges = np.linspace(left[0], right[0], dims[0] + 1, dtype=DTYPE)
        y_edges = np.linspace(left[1], right[1], dims[1] + 1, dtype=DTYPE)
        z_edges = None
        if metadata.dimension == 3:
            z_edges = np.linspace(left[2], right[2], dims[2] + 1, dtype=DTYPE)

        data: Dict[str, np.ndarray] = {}
        for name, field_name in fields.items():
            array = np.asarray(grid[field_name], dtype=DTYPE)
            if metadata.dimension == 3:
                data[name] = np.transpose(array, (2, 1, 0))
            else:
                data[name] = np.transpose(array[:, :, 0], (1, 0))
        result.setdefault(level, []).append(
            ZoneData(
                zone_name=f"Level_{level}",
                level=level,
                solutiontime=float(ds.current_time),
                x_edges=x_edges,
                y_edges=y_edges,
                z_edges=z_edges,
                data=data,
            )
        )
    return {level: zones for level, zones in result.items() if zones}


def load_timestep(
    metadata: DatasetMetadata,
    timestep: int,
    variables: Sequence[str],
    levels: Optional[Sequence[int]] = None,
    use_cache: bool = False,
) -> Dict[int, List[ZoneData]]:
    selected_levels = sorted(set(levels if levels is not None else metadata.levels))
    if metadata.source_format == "amrex_plotfile":
        return _load_plotfile(metadata, timestep, variables, selected_levels)
    if metadata.source_format == "tecplot_binary":
        raise UnsupportedFormatError(
            "当前软件不维护 Tecplot TDV112 二进制场数据读取；"
            "请改用 plot_format=0 或 plot_format=1。"
        )

    result: Dict[int, List[ZoneData]] = {}
    cache_dir = (
        _ascii_cache_dir(metadata.root)
        if use_cache and metadata.source_format == "tecplot_ascii"
        else None
    )
    timestep_sources = metadata.sources.get(timestep, {})
    for level in selected_levels:
        paths = timestep_sources.get(level, [])
        zones: List[ZoneData] = []
        for path in paths:
            zones.extend(read_ascii_file(path, variables, level, cache_dir=cache_dir))
        if zones:
            result[level] = zones
    return result


def _slice_index(edges: np.ndarray, position: float) -> Optional[int]:
    if position < float(edges[0]) or position > float(edges[-1]):
        return None
    if np.isclose(position, edges[-1]):
        return len(edges) - 2
    index = int(np.searchsorted(edges, position, side="right") - 1)
    if 0 <= index < len(edges) - 1:
        return index
    return None


def extract_patch(
    zone: ZoneData,
    variable: str,
    dimension: int,
    slice_axis: str = "z",
    slice_position: Optional[float] = None,
    value_override=None,
) -> Optional[Patch2D]:
    values = value_override if value_override is not None else zone.data.get(variable)
    if values is None:
        return None

    if dimension == 2 or values.ndim == 2:
        return Patch2D(
            level=zone.level,
            x_edges=zone.x_edges,
            y_edges=zone.y_edges,
            values=values,
            x_label="x",
            y_label="y",
        )

    if slice_position is None:
        raise DatasetError("三维数据必须指定切片坐标")
    axis = slice_axis.lower()
    if axis == "z":
        if zone.z_edges is None:
            return None
        index = _slice_index(zone.z_edges, slice_position)
        if index is None:
            return None
        return Patch2D(zone.level, zone.x_edges, zone.y_edges, values[index, :, :], "x", "y")
    if axis == "y":
        index = _slice_index(zone.y_edges, slice_position)
        if index is None or zone.z_edges is None:
            return None
        return Patch2D(zone.level, zone.x_edges, zone.z_edges, values[:, index, :], "x", "z")
    if axis == "x":
        index = _slice_index(zone.x_edges, slice_position)
        if index is None or zone.z_edges is None:
            return None
        return Patch2D(zone.level, zone.y_edges, zone.z_edges, values[:, :, index], "y", "z")
    raise DatasetError(f"未知切片方向: {slice_axis}")


def build_patches(
    level_data: Dict[int, List[ZoneData]],
    variable: str,
    dimension: int,
    slice_axis: str = "z",
    slice_position: Optional[float] = None,
    value_overrides: Optional[Dict[Tuple[int, str], object]] = None,
) -> List[Patch2D]:
    patches: List[Patch2D] = []
    for level in sorted(level_data):
        for zone in level_data[level]:
            override = (
                value_overrides.get((id(zone), variable))
                if value_overrides is not None
                else None
            )
            patch = extract_patch(
                zone,
                variable,
                dimension,
                slice_axis,
                slice_position,
                value_override=override,
            )
            if patch is not None and patch.values.size:
                patches.append(patch)
    return patches


def compute_bounds(metadata: DatasetMetadata) -> Tuple[Tuple[float, float], ...]:
    if metadata.bounds is not None:
        return metadata.bounds
    first_timestep = metadata.timesteps[0]
    level_data = load_timestep(
        metadata,
        first_timestep,
        variables=[],
        levels=metadata.levels,
    )
    xs, ys, zs = [], [], []
    for zones in level_data.values():
        for zone in zones:
            xs.extend((float(zone.x_edges[0]), float(zone.x_edges[-1])))
            ys.extend((float(zone.y_edges[0]), float(zone.y_edges[-1])))
            if zone.z_edges is not None:
                zs.extend((float(zone.z_edges[0]), float(zone.z_edges[-1])))
    if not xs or not ys:
        raise DatasetError("无法计算数据坐标范围")
    bounds: List[Tuple[float, float]] = [(min(xs), max(xs)), (min(ys), max(ys))]
    if metadata.dimension == 3:
        bounds.append((min(zs), max(zs)))
    metadata.bounds = tuple(bounds)
    return metadata.bounds


def axis_bounds(metadata: DatasetMetadata, axis: str) -> Tuple[float, float]:
    bounds = compute_bounds(metadata)
    index = {"x": 0, "y": 1, "z": 2}[axis.lower()]
    if index >= len(bounds):
        raise DatasetError(f"{metadata.dimension}D 数据没有 {axis.upper()} 方向范围")
    return bounds[index]
