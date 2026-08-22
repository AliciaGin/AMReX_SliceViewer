# AMReX_sliceViewer

A lightweight scientific visualization and post-processing tool for
**AMReX adaptive mesh refinement (AMR)** simulation data.

AMReX_sliceViewer provides an interactive graphical interface for
exploring AMR simulation results, visualizing physical fields, and
analyzing multi-level mesh structures.

The software is designed for researchers working on:

- Computational Fluid Dynamics (CFD)
- Compressible flows
- Multiphase flows
- Shock-wave dynamics
- High-speed and hypersonic simulations
- Large-scale AMR simulations


---

# Features

## AMR Data Visualization

AMReX_sliceViewer supports visualization of hierarchical AMR datasets:

- Multi-level AMR mesh inspection
- Level 0 / Level 1 / Level 2 visualization
- Adaptive refinement region display
- Patch-based mesh analysis
- Large-scale simulation data exploration


---

## Scalar Field Visualization

The visualization module supports slice-based rendering of physical
variables.

Supported rendering modes:

- Filled contour maps
- Scalar cloud maps
- Isoline contour overlays
- Combined contour + isoline visualization


Example:

<p align="center">
<img src="docs/images/slice_example.png" width="850">
</p>


---

## Color Mapping

AMReX_sliceViewer provides flexible scalar field mapping:

### Linear Scale

Suitable for variables with relatively uniform distributions.

Examples:

- Temperature
- Pressure
- Velocity


### Logarithmic Scale

Suitable for variables spanning multiple orders of magnitude.

Examples:

- Density variation
- Turbulence-related quantities
- Interface-related scalar fields


Supported colorbar modes:

- Linear normalization
- Logarithmic normalization
- Custom range adjustment
- User-defined color limits


Example:

<p align="center">
<img src="docs/images/colorbar_example.png" width="850">
</p>


---

## Contour Visualization

The software supports:

- User-defined contour levels
- Automatic contour generation
- Filled contour + line contour overlay
- Adjustable contour intervals


Example:

<p align="center">
<img src="docs/images/contour_example.png" width="850">
</p>


---

# Performance Features

## Efficient Data Processing

AMReX_sliceViewer provides:

- Optimized Tecplot ASCII BLOCK parsing
- Binary cache mechanism
- Cache invalidation detection
- Batch visualization workflow
- Checkpoint and resume support


## GPU Acceleration

Optional GPU acceleration is supported for large-scale visualization.

Features:

- GPU array processing
- CUDA-based computation pipeline
- CPU fallback mechanism


---

# User Interface

The graphical interface provides:

- Simulation data selection
- Variable selection
- AMR level control
- Slice configuration
- Colormap adjustment
- Contour control
- Image export


Example:

<p align="center">
<img src="docs/images/gui_main.png" width="850">
</p>


---

# Installation

## Requirements

Recommended:
