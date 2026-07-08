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
- **Detect** — Continuous probes catch sporadic loss over ICMP and UDP
- **Localize** — Per-hop visibility shows exactly where packets drop
- **Document** — JSON/CSV exports for ISP support tickets
- **Suggest** — Practical next steps based on automated bottleneck patterns

## Current Status
- Per-hop loss detection via automated `mtr` parsing
- Smart bottleneck analysis with forward-loss inheritance checks
- Multi-threaded daemon mode for parallel target monitoring
- Compact SQLite3 database storage for analytics timeline and incidents
- Native FastAPI web dashboard with HTMX live-reloading rows
- JSON/CSV evidence export for ISP support tickets
- Cross-platform support (macOS/Linux)

## Quick Start

### 1. Standalone CLI Probes
```bash
python3 -m core 8.8.8.8                # Basic loss test
python3 -m core 8.8.8.8 --watch        # Live single-target monitoring
python3 -m core 8.8.8.8 --mtr          # Traceroute + bottleneck analysis  
python3 -m core 8.8.8.8 --mtr --json=proof.json --csv=hops.csv  # Export ISP evidence
```

### 2. Multi-Target Daemon Mode
Monitor multiple endpoints and cloud servers concurrently over parallel execution threads:
```bash
python3 cli.py --targets "1.1.1.1,8.8.8.8,://amazonaws.com,github.com" --interval 3.0
```


## Installation
```bash
pip install nmpl

# or install from source
git clone https://github.com
cd NMPL
pip install -r requirements.txt
```

## Production Architecture

### Cloud & Multi-Target Engine
The core execution engine utilizes a `ThreadPoolExecutor` to handle scale across arbitrary cloud boundaries:
```bash
python3 cli.py --targets "://amazonaws.com,github.com,1.1.1.1" --interval 3.0
```

### Automated Diagnostic Records
When a path degrades, the daemon breaks past its baseline and captures an autonomous trace snapshot, saving a production-ready entry into your local history file for later ISP escalation:
```bash
python3 cli.py --report 1
```
