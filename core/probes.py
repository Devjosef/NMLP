import subprocess
import platform
import socket
import random

def icmp_probe(target, packet_size=64, timeout=1):
    param = "-n" if platform.system().lower() == "windows" else "-c"
    cmd = ["ping", param, "1", "-s", str(packet_size - 28), "-W", str(timeout * 1000), target]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0

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
