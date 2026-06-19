from typing import List, Dict

SUGGESTION_RATE_LIMITED = "Hop {hop} ({host}) is likely ICMP rate-limited. Destination is reachable with low loss."
SUGGESTION_CRITICAL     = "Critical: Persistent packet loss at Hop {hop} ({host}) extending down the path."
SUGGESTION_UNSTABLE     = "Warning: Unstable connection or intermittent loss detected at Hop {hop} ({host})."
SUGGESTION_HEALTHY      = "Path healthy"

def analyze_path(hops: List[Dict]) -> Dict:
    if not hops:
        return {
            "status": "error",
            "message": "No hops data to analyze",
            "bottleneck": None
        }
    
    analysis = {
        "total_hops": len(hops),
        "total_loss": sum(hop["loss"] for hop in hops) / len(hops),
        "average_latency": sum(hop["avg"] for hop in hops) / len(hops),
        "worst_latency": max(hop["worst"] for hop in hops),
        "bottleneck": None,
        "forwardloss_inherited": False,
        "likely_rate_limited": False,
        "suggestion": ""
    }
    
    bottleneck = max(hops, key=lambda x: x["loss"])
    analysis["bottleneck"] = {
        "hop": bottleneck["hop"],
        "host": bottleneck["host"],
        "loss": bottleneck["loss"],
        "avg_latency": bottleneck["avg"]
    }
    
    bottleneck_idx = None
    for i, hop in enumerate(hops):
        if hop["hop"] == bottleneck["hop"]:
            bottleneck_idx = i
            break
            
    if bottleneck_idx is not None and bottleneck_idx < len(hops) - 1:
        loss_after_bottleneck = 0
        total_hops_after = 0
        
        for i in range(bottleneck_idx + 1, len(hops)):
            total_hops_after += 1
            if hops[i]["loss"] > 5.0:
                loss_after_bottleneck += 1
                
        if total_hops_after > 0 and (loss_after_bottleneck / total_hops_after) > 0.5:
            analysis["forwardloss_inherited"] = True
            
    final_hop = hops[-1]
    if bottleneck["loss"] > 20.0 and final_hop["loss"] < 5.0:
        analysis["likely_rate_limited"] = True
        
    if analysis["likely_rate_limited"]:
        analysis["suggestion"] = SUGGESTION_RATE_LIMITED.format(
            hop=bottleneck["hop"], 
            host=bottleneck["host"]
        )
    elif bottleneck["loss"] > 20.0 and analysis["forwardloss_inherited"]:
        analysis["suggestion"] = SUGGESTION_CRITICAL.format(
            hop=bottleneck["hop"], 
            host=bottleneck["host"]
        )
    elif bottleneck["loss"] > 20.0:
        analysis["suggestion"] = SUGGESTION_UNSTABLE.format(
            hop=bottleneck["hop"], 
            host=bottleneck["host"]
        )
    else:
        analysis["suggestion"] = SUGGESTION_HEALTHY
        
    return analysis
