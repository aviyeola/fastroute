from __future__ import annotations
from typing import Any


def compare_runs(runs: list[dict]) -> dict:
    # runs: list of {job_id, report: NormalizedReport dict or object, board_sha256, config_hash}
    metrics = ["unrouted", "violations", "vias", "trace_length_mm", "route_time_s", "router_score", "optimizer_score"]
    # normalize each run to metric dict
    normalized = []
    for r in runs:
        rep = r.get("report")
        # support both dict and object
        if hasattr(rep, "model_dump"):
            rep = rep.model_dump()
        elif hasattr(rep, "__dict__") and not isinstance(rep, dict):
            rep = rep.__dict__
        rep = rep or {}
        board_stats = rep.get("board_stats", {}) or {}
        timings = rep.get("timings", {}) or {}
        m = {
            "job_id": r.get("job_id"),
            "unrouted": rep.get("unrouted_count", board_stats.get("unrouted", 0)),
            "violations": rep.get("violations_count", board_stats.get("violations", 0)),
            "vias": rep.get("vias_count", board_stats.get("vias")),
            "trace_length_mm": rep.get("trace_length_mm", board_stats.get("trace_length")),
            "route_time_s": timings.get("route_time", timings.get("route_time_s")),
            "router_score": rep.get("router_score"),
            "optimizer_score": rep.get("optimizer_score"),
            "board_sha256": r.get("board_sha256"),
            "config_hash": r.get("config_hash"),
        }
        normalized.append(m)
    # best per metric (lower better for unrouted/violations/vias/trace? trace not necessarily; keep min for counts)
    best = {}
    for metric in ("unrouted", "violations"):
        vals = [(n["job_id"], n[metric]) for n in normalized if n[metric] is not None]
        if vals:
            best[metric] = min(vals, key=lambda x: x[1])[0]
    # quality warning if board hashes or rule snapshots differ
    hashes = {n["board_sha256"] for n in normalized}
    configs = {n["config_hash"] for n in normalized}
    quality_warning = None
    if len(hashes) > 1:
        quality_warning = "Board hashes differ; runs are not directly comparable."
    elif len(configs) > 1:
        quality_warning = "Config hashes differ; compare with caution."
    deltas = {}
    if len(normalized) >= 2:
        a, b = normalized[0], normalized[1]
        for metric in metrics:
            if a.get(metric) is not None and b.get(metric) is not None:
                try:
                    deltas[metric] = b[metric] - a[metric]
                except Exception:
                    pass
    return {
        "runs": normalized,
        "best_per_metric": best,
        "deltas": deltas,
        "quality_warning": quality_warning,
    }
