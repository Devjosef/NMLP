# NMPL
**No more packet loss**

## Overview
Networks are unreliable. Packets get lost in transmission. Debugging the *where* and *why* is hard and convoluted.

NMPL detects packet loss, localizes it to specific hops/segments, then suggests next steps. When you know exactly *where* it went wrong, fixing it becomes much easier.

## The Problem
Packet loss is sporadic and hard to reproduce:

- Loss only happens under certain conditions (like load)
- Could be one bad router, cable, or ISP segment
- Simple pings miss bursty or path-specific issues

## What NMPL Does

- **Detect** — Continuous probes catch sporadic loss over ICMP and UDP, adapting to each target's own baseline
- **Localize** — Per-hop visibility via `mtr` shows exactly where packets drop
- **Document** — JSON/text exports for ISP support tickets, tied to a specific tracked incident
- **Suggest** — Practical next steps based on automated bottleneck and rate-limiting pattern analysis

## Two Ways to Run It

### 1. Web Dashboard (recommended)

A self-contained FastAPI + htmx dashboard. Type a target into the search bar and it starts monitoring immediately — no separate daemon process required.

```bash
python3 api.py
```

Then open `http://localhost:8000`. Targets you search for are remembered and monitored continuously in the background for as long as the process runs.

### 2. CLI

For scripting, headless boxes, or running monitoring independent of the web UI.

**One-off probes:**

```bash
python3 -m core 8.8.8.8                # Basic loss test
python3 -m core 8.8.8.8 --watch        # Live single-target monitoring
python3 -m core 8.8.8.8 --mtr          # Traceroute + bottleneck analysis
python3 -m core 8.8.8.8 --mtr --json=proof.json --csv=hops.csv  # Export raw hop data
```

**Multi-target daemon mode** — monitors several targets concurrently via a thread pool:

```bash
python3 cli.py --targets "1.1.1.1,8.8.8.8,github.com" --interval 3.0
```

> Targets must be real hostnames or IP addresses (no `://` scheme prefix — this isn't a URL, it's what gets passed straight to `ping`/`mtr`).

**Generate an ISP evidence report for a specific incident:**

```bash
python3 cli.py --report 1
```

You can run the API and the CLI daemon at the same time against the same database if you want — each maintains its own detector state, so there's no conflict, just some redundant probing if they're both watching the same target.

## Current Status

- Per-target adaptive baseline detection over ICMP and UDP, with absolute-ceiling and rate-limit-aware alerting
- Per-hop loss localization via automated `mtr` parsing, including unresponsive (`???`) hops
- Bottleneck analysis with forward-loss inheritance and elevated-loss tiers
- Incident lifecycle tracking — an ongoing outage stays as one open incident and gets marked resolved automatically once the target recovers, instead of spawning a new record every probe cycle
- Multi-threaded CLI daemon mode, and a self-sufficient in-process monitoring loop built into the web dashboard
- SQLite storage with automatic rolling cleanup of routine metrics (configurable, on by default) — incident records are kept permanently until resolved
- Live web dashboard (FastAPI + HTMX) with automatic per-target monitoring on first search
- JSON/text evidence export for ISP support tickets, per-incident or as a point-in-time snapshot

## Platform Support

- **ICMP/UDP detection**: Windows, macOS, and Linux (including WSL)
- **Hop-level tracing (`--mtr`, incident evidence, dashboard route view)**: requires the `mtr` binary, which is Linux/macOS-only. Windows users can get this via WSL. Without `mtr` installed, detection still works, but incidents won't include hop-by-hop trace data.

## Installation

```bash
git clone <this-repo-url>
cd NMPL
pip install -r requirements.txt
```

```text
# Optional, for hop-level tracing:
# Debian/Ubuntu: sudo apt install mtr
# macOS:         brew install mtr
```

## Configuration

All optional, set via environment variables or a `.env` file in the project root:

| Variable | Default | Purpose |
|---|---:|---|
| `NMPL_DB_FILE` | `nmpl_analytics.db` | SQLite database path |
| `NMPL_EPHEMERAL_METRICS` | `true` | Auto-trim routine metric rows past the retention cap |
| `NMPL_METRICS_RETENTION` | `200` | Rows to keep when ephemeral cleanup is on |
| `NMPL_PROBE_INTERVAL` | `3.0` | Seconds between probes per target |
| `NMPL_API_HOST` | `0.0.0.0` | Dashboard bind address |
| `NMPL_API_PORT` | `8000` | Dashboard port |
| `NMPL_LOG_LEVEL` | `INFO` | Log verbosity |
| `NMPL_REGISTRATION_RATE_LIMIT` | `20/minute` | Rate limit for registration/metrics partials |
| `NMPL_REPORT_RATE_LIMIT` | `10/minute` | Rate limit for report generation |
| `NMPL_DELETE_RATE_LIMIT` | `20/minute` | Rate limit for target deletion |

## Runtime Notes

- Rate limits are IP-based and shared across browser tabs or users behind the same NAT.
- The dashboard polls `/partials/metrics` every 5 seconds by default; that is about 12 requests per minute, which is below the default registration cap of `20/minute`.
- If you increase frontend polling frequency, open multiple tabs against the same target, or lower the rate-limit values, you may see rate-limit responses in the UI.
- The application provides a custom rate-limit handler that returns an HTML fragment for HTMX requests and JSON for programmatic clients; this improves UI degradation when the dashboard hits a limit.
- Rate-limit settings are configurable via:
  - `NMPL_REGISTRATION_RATE_LIMIT`
  - `NMPL_REPORT_RATE_LIMIT`
  - `NMPL_DELETE_RATE_LIMIT`

## Production Architecture

### Detection Engine

Each target gets its own persistent detector that learns a baseline over its first ~30 probes, then alerts on meaningful deviation from *that target's* normal behavior — not a fixed global threshold. This avoids false alarms on targets that are naturally lossy or filter certain probe types (e.g. providers that rate-limit unsolicited UDP).

### Incident Lifecycle

When a target degrades past its baseline, an MTR snapshot is captured and an incident record opens. Further snapshots while the outage continues update that same record rather than creating duplicates. Once the target recovers, the incident is automatically marked resolved — giving you a clean history of distinct events rather than a flood of near-identical rows.