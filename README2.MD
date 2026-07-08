# NMPL
**Continuous packet-loss detection, route localization, and ISP-ready proof.**

NMPL helps you catch flaky connectivity, localize where loss happens, and keep historical evidence so you can resolve problems faster. It is designed for gamers with bad routing, remote workers, sysadmins, and small businesses dealing with unstable ISP links.

## Overview
Networks fail in annoying ways: packet loss is intermittent, hard to reproduce, and often impossible to explain from a single ping test.

NMPL focuses on **outcome over tooling**. It continuously monitors targets, detects degradation, stores history locally, and generates incident records you can use as proof when escalating to an ISP or internal network team.

## What NMPL Does
- **Detect** — Continuous multi-threaded probes catch sporadic loss over ICMP and UDP (Port 53).
- **Localize** — Automated `mtr` parsing and forward-loss analysis identify the exact bottleneck.
- **Document** — Local SQLite3 storage keeps a permanent timeline of heartbeats and incidents.
- **Report** — Generates clean, ISP-ready incident files from your stored history.
- **Suggest** — Provides plain-language next steps instead of raw, confusing telemetry.

## Current Status
- **Multi-Target Daemon:** Parallel endpoint monitoring via `ThreadPoolExecutor`.
- **Local Persistence:** Structured SQLite3 storage (`metrics_timeline` & `network_incidents`) with transaction safety via Python 3.13 context managers.
- **Smart Path Analysis:** Bottleneck detection engine aware of forward-loss inheritance and ICMP rate-limiting.
- **Web Dashboard:** Native FastAPI interface with dynamic, real-time HTMX row streaming.
- **Automated Snapshots:** Autonomous traceroute capture when a target breaks past its baseline.
- **Evidence Export:** JSON/CSV report generation ready for ISP support tickets.

## Quick Start

### 1. Standalone CLI Probes
Run a single, immediate check against an endpoint:
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

### 3. Launch Web Dashboard
Start the local FastAPI app to view diagnostic timelines and path diagnostics live in your browser:
```bash
python3 api.py
```
Open **`http://localhost:8000`** to access the user interface.

## Installation
```bash
pip install nmpl

# Or install from source
git clone https://github.com
cd NMPL
pip install -r requirements.txt
```

## Production Architecture

### Cloud & Multi-Target Engine
The core execution engine handles scale across arbitrary cloud boundaries with cooldown guards to prevent alert floods:
```bash
python3 cli.py --targets "://amazonaws.com,github.com,1.1.1.1" --interval 3.0
```

### Automated Diagnostic Records
When a path degrades, the daemon triggers an autonomous trace snapshot and saves a production-ready entry into your local history file for later ISP escalation:
```bash
python3 cli.py --report 1
```

## Storage Architecture
NMPL maintains a lightweight footprint using a local SQLite database (`nmpl_analytics.db`) managed via `core/storage.py`.
- `metrics_timeline` stores every heartbeat, latency metric, and status flag (`OK` / `ALERT`).
- `network_incidents` houses complete, raw traceroute structures and bottleneck telemetry for historical evidence.

## Project Focus
NMPL is built for users who need:
- Continuous monitoring, not a one-off CLI run.
- Clear interpretation, not raw `mtr` output.
- Historical proof, not just ephemeral transient data.
- Faster resolution for urgent connectivity problems.

## License
This project is licensed under the MIT License - see the LICENSE file for details.

## Contact
Maintained by Josef.
