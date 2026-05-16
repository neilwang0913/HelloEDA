# airport-queue-planner

Airport customs/immigration queue optimiser using M/M/c (Erlang-C) queuing theory.

Given a flight arrival schedule, it computes the minimum number of open border-control windows per 30-minute slot to keep average passenger wait time under a configurable threshold, and generates stagger advisories for scheduling staff.

**Primary target airport:** Milan Malpensa (MXP / LIMC)

## Quick Start

```bash
# Demo with HTML + Markdown output
python3 mxp_flight_scraper.py --demo --html --md

# Real data via CSV
python3 mxp_flight_scraper.py --input flights_input.csv --html --md

# Standalone window planner
python3 peak_window_planner.py
```

## Output

- **Terminal** — colour-coded per-slot table with pressure ratings
- **HTML** — self-contained Chart.js dashboard (`arrivals_mxp.html`)
- **Markdown** — emoji-annotated report (`arrivals_mxp.md`)
- **CSV** — raw flight data export (`arrivals_mxp.csv`)

## Model

- Queuing model: **M/M/c** with Erlang-C formula
- Default service time: **1.5 min/passenger**
- Default wait target: **≤ 5 min average**
- Passenger spread from gate to queue: **20 min**
