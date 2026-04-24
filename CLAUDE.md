# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HelloEDA is an educational Verilog (Hardware Description Language) project. It currently contains a single simulation module demonstrating basic Verilog syntax.

## Simulating Verilog

The standard open-source tool for this project is **Icarus Verilog**:

```bash
# Compile
iverilog -o hello HelloWorld.v

# Run simulation
vvp hello
```

Expected output: `Hello, World`

Alternatively, use [EDAPlayground](https://www.edaplayground.com/x/rux) to simulate in-browser without local tooling.

## Code Structure

- `HelloWorld.v` — single top-level module (`hello`) using an `initial` block for simulation-only logic; not synthesizable
- `Reference` — links to Verilog learning resources

## Verilog Conventions

- Simulation-only constructs (`$display`, `$finish`, `initial` blocks) are acceptable here since the project targets simulation, not FPGA/ASIC synthesis.
- Testbenches follow the convention of a separate module (e.g., `module hello_tb`) instantiating the design under test (DUT).
