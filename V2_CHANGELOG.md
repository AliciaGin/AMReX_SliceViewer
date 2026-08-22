# AMReX_SlideViewer V2 Changelog

## Core Changes

### 1. Time-step based task scheduling

- Task scheduling is now organized by simulation time step.
- One task processes:
  - multiple AMR levels
  - multiple physical variables

Variables are no longer separated into independent processes, reducing repeated file access and improving memory efficiency.

---

### 2. Optimized Tecplot ASCII BLOCK Zone parsing

- Structured Tecplot ASCII BLOCK Zones are now processed using a two-stage workflow:
  1. Read the numerical region of each Zone.
  2. Convert the data into `float32` arrays using a single `numpy.fromstring` operation.

This significantly improves parsing efficiency for large structured datasets.

---

### 3. Adaptive variable slicing

Variable slicing is determined dynamically based on:

- Zone `VARLOCATION`
- Zone `I/J/K` dimensions

The implementation no longer assumes a fixed number of node variables, improving compatibility with different Tecplot datasets.

---

### 4. Improved garbage collection strategy

Previous versions performed a full:

```python
gc.collect()
