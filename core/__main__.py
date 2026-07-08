import argparse
import time
import json
import csv
import sys
from .detect import Detector
from .mtr import run_mtr
from .analyzer import analyze_path

def main_with_result(args_list=None):
    if args_list is None:
        args_list = sys.argv[1:]
    
    parser = argparse.ArgumentParser(description="NMPL: Packet Loss Detector")
    parser.add_argument("target", help="Target IP")
    parser.add_argument("--watch", action="store_true", help="Live monitoring")
    parser.add_argument("--mtr", action="store_true", help="per hop analysis")
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--json", help="Export JSON analysis")
    parser.add_argument("--csv", help="Export CSV hops")
    args = parser.parse_args(args_list)
    
    result = {
        "target": args.target,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": "",
        "bottleneck": None,
        "hops": [],
        "analyzer": None,
        "csv": "",
        "metrics": {"loss_pct": 0.0, "latency_ms": 0.0, "jitter_ms": 0.0}
    }
    
    if args.mtr:
        hops = run_mtr(args.target)
        result["hops"] = hops
        
        if hops:
            summary_lines = [f"Traceroute to {args.target}:"]
            for hop in hops:
                status = "BAD" if hop["loss"] > 10 else "OK"
                summary_lines.append(f"{status} Hop {hop['hop']}: {hop['loss']:.1f}% -> {hop['host']} (avg: {hop['avg']} ms)")
            
            analysis = analyze_path(hops)
            result["analyzer"] = analysis
            result["bottleneck"] = analysis["bottleneck"]
            
            summary_lines.append("\nAnalysis:")
            summary_lines.append(f"Total Hops: {analysis['total_hops']}")
            summary_lines.append(f"Total Loss: {analysis['total_loss']:.1f}%")
            summary_lines.append(f"Average Latency: {analysis['average_latency']:.1f} ms")
            if analysis["bottleneck"]:
                summary_lines.append(f"Bottleneck: Hop {analysis['bottleneck']['hop']} ({analysis['bottleneck']['host']}) with {analysis['bottleneck']['loss']:.1f}% loss")
            summary_lines.append(f"Next: {analysis['suggestion']}")
            
            result["summary"] = "\n".join(summary_lines)
            result["metrics"]["loss_pct"] = analysis["total_loss"]
            result["metrics"]["latency_ms"] = analysis["average_latency"]
            
            if args.csv:
                with open(args.csv, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=hops[0].keys())
                    writer.writeheader()
                    writer.writerows(hops)
                result["csv"] = f"CSV saved: {args.csv}"
            
            if args.json:
                data = {"hops": hops, "analysis": analysis, "timestamp": result["timestamp"]}
                with open(args.json, "w") as f:
                    json.dump(data, f, indent=2)
                result["summary"] += f"\n\nJSON saved: {args.json}"
        else:
            result["summary"] = f"No hops data for {args.target}"
    else:
        d = Detector()
        if args.watch:
            d.probe(args.target)
            result["summary"] = d.status()
            recent_losses = list(d.alert_losses)
            result["metrics"]["loss_pct"] = recent_losses[-1] * 100.0 if recent_losses else 0.0
        else:
            for _ in range(30):
                d.probe(args.target)
            result["summary"] = d.status()
            if d.baseline_established and d.baseline is not None:
                result["metrics"]["loss_pct"] = d.baseline * 100.0
            else:
                recent_losses = list(d.alert_losses)
                result["metrics"]["loss_pct"] = (sum(recent_losses) / len(recent_losses) * 100.0) if recent_losses else 0.0
                
    return result

def main():
    args_list = sys.argv[1:]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=3.0)
    args, unknown = parser.parse_known_args(args_list)
    
    if args.watch and not any(arg in args_list for arg in ["--mtr", "--once"]):
        parser_full = argparse.ArgumentParser()
        parser_full.add_argument("target", help="Target IP")
        full_args, _ = parser_full.parse_known_args(args_list)
        
        d = Detector()
        print(f"Monitoring loss {full_args.target}...")
        try:
            while True:
                d.probe(full_args.target)
                print(f"\r{d.status()}", end="")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nExiting...")
            return

    result = main_with_result(args_list)
    print(result["summary"])

if __name__ == "__main__":
    main()
