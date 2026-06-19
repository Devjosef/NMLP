from collections import deque
from statistics import mean, stdev, variance
import time
from .probes import icmp_probe, udp_probe

class Detector:
    def __init__(self, baseline_window=30, alert_window=150, adaptive=True):
        self.baseline_window = baseline_window
        self.alert_window = alert_window
        self.baseline_losses = deque(maxlen=baseline_window)
        self.alert_losses = deque(maxlen=alert_window)
        self.start_time = time.time()
        self.baseline_established = False
        self.baseline = None
        self.baseline_std = None
        self.adaptive = adaptive
        self.icmp_rate_limited = False
        self.udp_rate_limited = False 
        
        self.icmp_failures = 0
        self.udp_failures = 0
        self.icmp_successes = 0
        self.udp_successes = 0
        self.icmp_loss_history = deque(maxlen=baseline_window)
        self.udp_loss_history = deque(maxlen=baseline_window)
        self.icmp_baseline = None
        self.udp_baseline = None

    def probe(self, target):
        icmp_small = icmp_probe(target, 64)
        icmp_large = icmp_probe(target, 512)

        icmp_success = int(icmp_small and icmp_large)
        self.icmp_successes += icmp_success
        self.icmp_failures += (1 - icmp_success)
        self.icmp_loss_history.append(1 - icmp_success)

        udp_ok = udp_probe(target)
        self.udp_successes += int(udp_ok)
        self.udp_failures += int(not udp_ok)
        self.udp_loss_history.append(int(not udp_ok))

        combined_loss = ((1 - icmp_success) + int(not udp_ok)) / 2.0
        self.baseline_losses.append(combined_loss)
        self.alert_losses.append(combined_loss)

        if icmp_success == 0 and udp_ok:
            self.icmp_rate_limited = True

        return combined_loss
    
    def status(self):
        total_probes = (self.icmp_successes + self.icmp_failures + 
                        self.udp_successes + self.udp_failures)
        
        if len(self.icmp_loss_history) < self.baseline_window:
            return f"Learning... ICMP:{len(self.icmp_loss_history)}/{self.baseline_window}"
        
        if not self.baseline_established:
            self.icmp_baseline = mean(self.icmp_loss_history)
            self.udp_baseline = mean(self.udp_loss_history)
            self.baseline = mean(self.baseline_losses)
            self.baseline_std = stdev(self.baseline_losses)
            self.baseline_established = True
            total_probes = self.icmp_successes + self.icmp_failures + self.udp_successes + self.udp_failures
            return (f"ICMP base: {self.icmp_baseline*100:.1f}% "
                    f"UDP base: {self.udp_baseline*100:.1f}% "
                    f"(S:{self.icmp_successes+self.udp_successes}/{total_probes})")

        recent_losses = list(self.alert_losses)[-30:]
        recent_var = variance(recent_losses) if len(recent_losses) > 1 else 0
        alert_size = 75 if self.adaptive and recent_var > 0.01 else self.alert_window
        current_losses = list(self.alert_losses)[-alert_size:]
        current_avg = mean(current_losses)

        icmp_recent = mean(list(self.icmp_loss_history)[-10:])
        udp_recent = mean(list(self.udp_loss_history)[-10:])
    
        if self.icmp_rate_limited:
            return "ICMP rate-limited (UDP OK)"
    
        if (icmp_recent > 0.1 or udp_recent > 0.1 or current_avg > 0.02):
            return (f"Loss: {current_avg*100:.1f}% "
                    f"ICMP: {icmp_recent*100:.1f}%  UDP: {udp_recent*100:.0f}%) "
                    f"S:{self.icmp_successes+self.udp_successes}/ {total_probes}")

        return f"OK {current_avg*100:.1f}% (ICMP: {icmp_recent*100:.0f}%, UDP: {udp_recent*100:.0f}%)"

    def get_stats(self):
        return { 
            "icmp": {"succ": self.icmp_successes, "fail": self.icmp_failures},
            "udp": {"succ": self.udp_successes, "fail": self.udp_failures}
        }
