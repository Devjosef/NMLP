import subprocess
import re
from typing import List, Dict

def parse_mtr_output(output: str) -> List[Dict]:
    hops = []
    lines = output.strip().split("\n")[2:]

    for line in lines:
        parts = re.split(r"\s+", line.strip())
        if len(parts) >= 8:
            hop_raw = parts[0].rstrip(".|").split("|")[0].strip(".")
            if hop_raw.isdigit():
                hop = {
                    "hop": int(hop_raw),
                    "host": parts[1],
                    "loss": float(parts[2].rstrip("%")),
                    "sent": int(parts[3]),
                    "last": float(parts[4]),
                    "avg": float(parts[5]),
                    "best": float(parts[6]),
                    "worst": float(parts[7])
                }
                hops.append(hop)
    return hops

def run_mtr(target: str, count: int = 10) -> List[Dict]:
    cmd = ["mtr", "-r", "-c", str(count), "-w", "--no-dns", target]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return parse_mtr_output(result.stdout)
        else:
            print(f"Error running mtr: {result.stderr}")
            return []
    except subprocess.TimeoutExpired:
        print("MTR command timed out")
        return []
