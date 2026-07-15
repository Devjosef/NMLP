import logging
from typing import List, Dict

logger = logging.getLogger("nmpl.diagnostics")

SUGGESTION_RATE_LIMITED = "Hop {hop} ({host}) is likely ICMP rate-limited. Destination is reachable with low loss."
SUGGESTION_CRITICAL     = "Critical: Persistent packet loss at Hop {hop} ({host}) extending down the path."
SUGGESTION_UNSTABLE     = "Warning: Unstable connection or intermittent loss detected at Hop {hop} ({host})."
SUGGESTION_ELEVATED     = "Notice: Elevated loss ({loss:.1f}%) at Hop {hop} ({host}). Below critical, but worth monitoring."
SUGGESTION_HEALTHY      = "Path healthy"

CRITICAL_LOSS_THRESHOLD = 20.0
ELEVATED_LOSS_THRESHOLD = 5.0


def analyze_path(hops: List[Dict]) -> Dict:
    if not hops:
        logger.warning("analyze_path called with no hops data — nothing to analyze.")
        return {
            "status": "error",
            "message": "No hops data to analyze",
            "bottleneck": None
        }

    try:
        # Compute basic statistics about the path based on hop data
        analysis = {
            "total_hops": len(hops),
            "total_loss": sum(hop["loss"] for hop in hops) / len(hops),
            "average_latency": sum(hop["avg"] for hop in hops) / len(hops),
            "worst_latency": max(hop["worst"] for hop in hops),
            "bottleneck": None,
            "forwardloss_inherited": False,
            "likely_rate_limited": False,
            "elevated": False,
            "suggestion": ""
        }

        bottleneck = max(hops, key=lambda x: x["loss"])
        analysis["bottleneck"] = {
            "hop": bottleneck["hop"],
            "host": bottleneck["host"],
            "loss": bottleneck["loss"],
            "avg_latency": bottleneck["avg"]
        }
    except KeyError as e:
        logger.error(f"Malformed hop data passed to analyze_path — missing key: {e}")
        return {
            "status": "error",
            "message": f"Malformed hop data: missing key {e}",
            "bottleneck": None
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
            if hops[i]["loss"] > ELEVATED_LOSS_THRESHOLD:
                loss_after_bottleneck += 1

        if total_hops_after > 0 and (loss_after_bottleneck / total_hops_after) > 0.5:
            analysis["forwardloss_inherited"] = True
            logger.info(
                f"Forward-loss inheritance detected past hop {bottleneck['hop']} "
                f"({bottleneck['host']}) — loss persists downstream."
            )

    final_hop = hops[-1]
    if bottleneck["loss"] > CRITICAL_LOSS_THRESHOLD and final_hop["loss"] < ELEVATED_LOSS_THRESHOLD:
        analysis["likely_rate_limited"] = True

    analysis["elevated"] = ELEVATED_LOSS_THRESHOLD < bottleneck["loss"] <= CRITICAL_LOSS_THRESHOLD

    if analysis["likely_rate_limited"]:
        analysis["suggestion"] = SUGGESTION_RATE_LIMITED.format(hop=bottleneck["hop"], host=bottleneck["host"])
        logger.info(f"Hop {bottleneck['hop']} ({bottleneck['host']}) classified as ICMP rate-limited, not a real fault.")
    elif bottleneck["loss"] > CRITICAL_LOSS_THRESHOLD and analysis["forwardloss_inherited"]:
        analysis["suggestion"] = SUGGESTION_CRITICAL.format(hop=bottleneck["hop"], host=bottleneck["host"])
        logger.warning(f"CRITICAL path fault at hop {bottleneck['hop']} ({bottleneck['host']}): {bottleneck['loss']:.1f}% loss, inherited downstream.")
    elif bottleneck["loss"] > CRITICAL_LOSS_THRESHOLD:
        analysis["suggestion"] = SUGGESTION_UNSTABLE.format(hop=bottleneck["hop"], host=bottleneck["host"])
        logger.warning(f"Unstable connection at hop {bottleneck['hop']} ({bottleneck['host']}): {bottleneck['loss']:.1f}% loss.")
    elif analysis["elevated"]:
        analysis["suggestion"] = SUGGESTION_ELEVATED.format(loss=bottleneck["loss"], hop=bottleneck["hop"], host=bottleneck["host"])
        logger.info(f"Elevated loss at hop {bottleneck['hop']} ({bottleneck['host']}): {bottleneck['loss']:.1f}% — below critical threshold.")
    else:
        analysis["suggestion"] = SUGGESTION_HEALTHY

    return analysis