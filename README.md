# AMR 计算结果后处理软件 v2

v2 是独立测试版本，保留 v1 的 GUI、GPU 数组处理、缓存和断点续作，新增：

- 按时间步提交并行任务；不按变量拆分进程；
- Tecplot ASCII `DATAPACKING=BLOCK` Zone 单次 `numpy.fromstring` 数值解析；
- 动态读取 `VARLOCATION`，不硬编码节点变量和单元变量数量；
- 非 BLOCK 或异常 Zone 自动回退到兼容解析器。

## 程序入口

- 图形前端：`python main.py`
- 后端命令行：`python backend_cli.py ...`
- 核心读取与切片：`amr_backend.py`
- 绘图与并行任务：`visualizer.py`
- 硬件识别：`hardware_info.py`
- 动态调度：`runtime_policy.py`
- GPU 数组后端：`gpu_backend.py`

`hardware_info.py` 负责读取 CPU、内存、GPU 和数据所在磁盘的只读运行信息；
`runtime_policy.py` 根据数据规模和硬件条件推荐进程数。任务仍按时间步调度，
同一时间步的多个变量在同一个任务中处理，不采用“一个变量一个进程”。

当前版本的 GPU 加速覆盖可迁移的数组操作、AMR 合成、色标范围计算和颜色映射；
Matplotlib 最终 PNG/视频编码仍使用 CPU。GPU 模式默认由一个进程持有 CUDA 上下文，
同一时间步的多个变量批量处理，避免多个进程重复占用显存；CPU 模式才按时间步使用多进程。
GPU 依赖见 `requirements-gpu.txt`，GUI 默认启用 GPU 数组加速；命令行可使用
`--no-gpu` 关闭。

Tecplot ASCII 默认会在数据目录下创建 `.amrex_viewer_cache` 二进制缓存。
第一次运行会解析并缓存，后续运行按源文件大小和修改时间校验后直接读取；
缓存命中时只读取当前选择的变量。GPU 首次运行且缓存目录为空时，会先用最多 2 个
CPU 进程准备缓存，再由单 GPU 进程批量处理；命令行可使用 `--no-ascii-cache` 关闭。
缓存属于可删除的性能文件，不改变原始 `.dat`。

`singlefile.py` 为早期独立脚本，保留用于算法参考，不是新版主入口。

## 支持范围

- Tecplot ASCII（`plot_format=1`）：二维云图和三维轴向切片；
- AMReX Plotfile（`plot_format=0`）：二维云图和三维轴向切片；
- AMReX 输出目录前缀可自定义，例如 `plt00000`、`plt_xxxx00000`
  或 `shock_case_00120`；程序从目录名末尾数字提取时间步；
- Tecplot Binary（`plot_format=2`）：不作为维护格式，请改用 `plot_format=0` 或 `plot_format=1`；
- AMR 层级任意多选，按粗层到细层覆盖；
- X、Y、Z 空间输出范围可自定义，默认采用完整计算域；
- 多变量图片和 MP4/GIF 视频；
- 按时间步多进程并行。
- 支持按时间步范围或前 N 个时间步出图；
- 支持断点续作，默认跳过已有有效图片帧；

## 安装

```bash
python -m pip install -r requirements.txt
```

Linux 图形前端还需要 Tk：

```bash
sudo apt install python3-tk
```

## 后端用法

检查数据：

```bash
python backend_cli.py inspect /path/to/Results-3Dformat1
```

二维绘图：

```bash
python backend_cli.py render /path/to/Results-2Dformat1 \
  -o output_2d -v rho,p,T -l 0,1,2
```

三维 Z 法向切片：

```bash
python backend_cli.py render /path/to/Results-3Dformat1 \
  -o output_z -v rho,p,T -l 0,1,2 \
  --dimension 3 --slice-axis z --slice-position 0.05 \
  --x-range 0 0.6 --y-range 0 0.3 --z-range 0 0.1
```

只生成指定时间步范围：

```bash
python backend_cli.py render /path/to/Results-2Dformat1 \
  -o output_range -v rho \
  --timesteps 1000-2000
```

默认会跳过输出目录中已经存在且非空的结果。如需强制重算：

```bash
python backend_cli.py render /path/to/Results-2Dformat1 \
  -o output_range -v rho \
  --timesteps 1000-2000 --no-resume
```

GPU 和 ASCII 缓存可以分别关闭：

```bash
python backend_cli.py render /path/to/Results \
  -o output_gpu -v rho,u,p,T --no-gpu --no-ascii-cache
```

GUI 加载数据后会自动填入完整 X/Y/Z 边界，可直接修改最小值和最大值，
也可点击“恢复完整边界”重置。

直接读取 AMReX plotfile：

```bash
python backend_cli.py render /path/to/Results-3Dformat0 \
  -o output_plotfile -v rho,p -l 0,1,2 \
  --slice-axis y --slice-position 0.3
```

同时生成 MP4 和 GIF/JIF 动图：

```bash
python backend_cli.py render /path/to/Results-2Dformat1 \
  -o output_video -v rho -l 0,1,2 \
  --output-type video --video-format both
```

## 切片定义

- `--slice-axis x`：`x=常数`，绘制 YZ 平面；
- `--slice-axis y`：`y=常数`，绘制 XZ 平面；
- `--slice-axis z`：`z=常数`，绘制 XY 平面。
