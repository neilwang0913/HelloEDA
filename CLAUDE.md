# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**airport-queue-planner** is a queuing-theory tool for optimising customs/immigration staffing at airports. It models passenger arrival curves from flight schedules and uses the M/M/c (Erlang-C) formula to recommend the minimum number of open border-control windows per 30-minute slot, together with stagger advisories.

Primary airport target: **Milan Malpensa (MXP / LIMC)**.

## Key Files

| File | Purpose |
|------|--------|
| `mxp_flight_scraper.py` | Main CLI — fetches flights, runs M/M/c model, outputs terminal / HTML / Markdown / CSV reports |
| `peak_window_planner.py` | Standalone M/M/c window planner (example flight schedule built-in) |
| `flights_input.csv` | User-editable flight data template (upload real MXP data here) |
| `arrivals_mxp.html` | Generated HTML dashboard (Chart.js) |
| `arrivals_mxp.md` | Generated Markdown report |

## Running the Tool

```bash
# Demo mode (built-in 52-flight sample)
python3 mxp_flight_scraper.py --demo --html --md

# Real data via local CSV
python3 mxp_flight_scraper.py --input flights_input.csv --html --md

# Standalone window planner
python3 peak_window_planner.py
```

## Data Sources (priority order)

1. `--input <file>` — local CSV (highest priority)
2. GitHub `flights_input.csv` via API (`GITHUB_REPO` config)
3. OpenSky Network API (requires free account credentials)
4. AviationStack API (requires free API key)
5. MXP official website — static scrape (blocked, 403)
6. MXP official website — Selenium (requires chromedriver)
7. Built-in demo data (fallback)

## CSV Format

```
callsign,origin,arr_hour,arr_min,pax,status
LH1234,FRA,8,30,180,landed
```

## Model Parameters

- `AVG_SERVICE_MIN = 1.5` — average border-check time per passenger (minutes)
- `WAIT_TARGET_MIN = 5.0` — maximum acceptable average wait (minutes)
- `DEPLANE_SPREAD_MIN = 20` — passenger walk-time spread from gate to queue
- `SLOT_MIN = 30` — analysis time-slot width
