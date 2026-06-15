#!/usr/bin/env python3

import argparse
import json
import time
import random
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from core import __main__
from core.storage import StorageManager

db = StorageManager()
_active_snapshots = set()

def evaluate_and_route_incident(engine_args, target):
    global _active_snapshots
    if target in _active_snapshots:
        return
        
    try:
        _active_snapshots.add(target)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Target {target} degraded. Starting trace snapshot.")
        
        time.sleep(random.uniform(0.1, 0.5))
        
        mtr_args = [arg for arg in engine_args if arg != target] + ["--mtr", target]
        incident_payload = __main__.main_with_result(mtr_args)
        
        db.log_incident(target, incident_payload)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Saved diagnostic record to local history.")
    finally:
        _active_snapshots.discard(target)

def run_once(engine_args):
    result = __main__.main_with_result(engine_args)
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="Export JSON")
    parser.add_argument("--csv", help="Export CSV")
    parser.add_argument("--quiet", action="store_true")
    args, _ = parser.parse_known_args()
    
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2))
    
    if args.csv:
        Path(args.csv).write_text(result.get('csv', ''))
    
    if not args.quiet:
        print(result.get('summary', ''))

def run_daemon(targets, interval=3.0, base_args=None):
    db.init_storage()
    if base_args is None:
        base_args = []
        
    last_incident_times = {target: None for target in targets}
    INCIDENT_COOLDOWN = 15.0
    
    print(f"Monitoring targets: {', '.join(targets)} with {interval}s intervals.")
    print("Use Ctrl+C to stop.\n")
    
    def monitor_target_worker(target):
        engine_args = base_args + [target]
        
        while True:
            worker_start = time.time()
            try:
                result = __main__.main_with_result(engine_args)
                
                raw_loss = result.get('loss_pct', 0.0)
                if isinstance(raw_loss, str):
                    raw_loss = float(raw_loss.replace('%', '').strip())
                elif raw_loss is None:
                    raw_loss = 0.0
                    
                metrics = {
                    'loss_pct': raw_loss,
                    'latency_ms': result.get('latency_ms', 0.0) or 0.0,
                    'jitter_ms': result.get('jitter_ms', 0.0) or 0.0
                }
                summary = result.get('summary', '') or ""
                
                is_total_loss = "(S:0/" in summary or "100.0%" in summary
                is_alert = metrics['loss_pct'] > 5.0 or "BAD" in summary or is_total_loss
                
                if "ICMP: 0%" in summary or "ICMP base: 0.0%" in summary:
                    is_alert = False
                    
                status_flag = 'ALERT' if is_alert else 'OK'
                
                db.log_heartbeat(target, summary, status_flag, metrics)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [{target}] {status_flag}: {summary}")
                
                if is_alert:
                    now = time.time()
                    if last_incident_times[target] is None or (now - last_incident_times[target]) >= INCIDENT_COOLDOWN:
                        evaluate_and_route_incident(engine_args, target)
                        last_incident_times[target] = now
                        
            except Exception as exc:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [{target}] Probe execution error: {exc}")
            
            elapsed = time.time() - worker_start
            sleep_time = max(0.1, interval - elapsed)
            time.sleep(sleep_time)

    try:
        with ThreadPoolExecutor(max_workers=len(targets)) as executor:
            executor.map(monitor_target_worker, targets)
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")

def generate_isp_report(incident_id: int):
    row = db.get_incident(incident_id)
    
    if not row:
        print(f"Error: Incident {incident_id} not found.")
        return
    
    report_data = {
        "id": row[0],
        "timestamp": row[1],
        "target": row[2],
        "summary": row[3],
        "evidence": {
            "bottleneck_hop": row[4],
            "bottleneck_host": row[5],
            "bottleneck_loss_pct": row[6]
        },
        "raw_runlog": json.loads(row[7])
    }
    
    report_path = f"ploss_report_incident_{incident_id}.json"
    Path(report_path).write_text(json.dumps(report_data, indent=2))
    print(f"[REPORT] ISP-ready report saved to: {report_path}")

def main():
    parser = argparse.ArgumentParser(
        description="Ploss - Continuous Network Diagnostics Daemon"
    )
    parser.add_argument("--targets", required=False, help="Comma-separated target endpoints to monitor")
    parser.add_argument("--interval", type=float, default=3.0, help="Probe interval in seconds")
    parser.add_argument("--once", action="store_true", help="Single CLI run")
    parser.add_argument("--report", type=int, help="Generate report for incident ID")
    parser.add_argument("--json", help="Export JSON")
    parser.add_argument("--csv", help="Export CSV")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    
    args, unknown = parser.parse_known_args()
    
    if args.report:
        generate_isp_report(args.report)
        return
        
    target_list = [t.strip() for t in args.targets.split(",")] if args.targets else []
    
    if args.once or not target_list:
        cli_args = unknown + target_list
        cli_args = [arg for arg in cli_args if arg not in ["--once"]]
        run_once(cli_args)
        return
    
    run_daemon(target_list, args.interval, unknown)

if __name__ == "__main__":
    main()