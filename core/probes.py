import subprocess
import platform
import socket
import random
import re

def icmp_probe(target, packet_size=64, timeout=1):
    is_windows = platform.system().lower() == "windows"
    if is_windows:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), target]
    else:
        cmd = ["ping", "-c", "1", "-s", str(packet_size - 28), "-W", str(timeout * 1000), target]
    result = subprocess.run(cmd, capture_output=True, text=True)
    success = (result.returncode == 0)
    latency_ms = 0.0
    if success:
        match = re.search(r"time[=:<]\s*([\d.]+)\s*(ms)?", result.stdout, re.IGNORECASE)
        if match:
            try:
                latency_ms = float(match.group(1))
            except ValueError:
                latency_ms = 0.0
    return success, latency_ms

def udp_probe(target, timeout=1, jitter=True):
    payload_size = random.randint(32, 512)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        payload = b"A" * payload_size
        sock.sendto(payload, (target, 53))
        sock.recvfrom(1024)
        return True
    except socket.timeout:
        return False
    except Exception:
        return False
    finally:
        sock.close()