from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from ..models.report import NormalizedReport, UnroutedConnection, ClearanceViolation


def parse_report(raw: dict[str, Any], raw_path: str | None = None) -> NormalizedReport:
    r = NormalizedReport(raw_path=raw_path, raw=raw)
    r.fastroute_version = raw.get("fastroute") or raw.get("version") or raw.get("fastroute_version")
    r.input_path = raw.get("input") or raw.get("input_path") or raw.get("dsn")
    # timings
    ts = raw.get("time_s") or raw.get("timings") or {}
    if isinstance(ts, dict):
        r.timings = ts
    # board stats
    stats = raw.get("stats") or raw.get("board") or raw.get("statistics") or raw.get("board_statistics") or {}
    if isinstance(stats, dict):
        r.board_stats = stats
        r.unrouted_count = stats.get("unrouted", stats.get("unrouted_connections", r.unrouted_count)) or 0
        r.ignored_unrouted_count = stats.get("unrouted_ignored_classes", stats.get("ignored_unrouted", 0)) or 0
        r.violations_count = stats.get("violations", stats.get("clearance_violations", r.violations_count)) or 0
        r.unfixable_violations_count = stats.get("violations_unfixable", stats.get("unfixable_violations", 0)) or 0
        r.vias_count = stats.get("vias", stats.get("via_count"))
        r.blind_buried_vias_count = (stats.get("vias_blind", 0) or 0) + (stats.get("vias_buried", 0) or 0)
        r.traces_count = stats.get("traces", stats.get("trace_count"))
        r.trace_length_mm = stats.get("trace_length_mm", stats.get("trace_length"))
        r.router_score = stats.get("router_score", r.router_score)
        r.optimizer_score = stats.get("optimizer_score", r.optimizer_score)
    # top-level scores fallback
    if r.router_score is None:
        r.router_score = raw.get("router_score")
    if r.optimizer_score is None:
        r.optimizer_score = raw.get("optimizer_score")
    # unrouted connections
    unrouted_raw = raw.get("unrouted") or raw.get("unrouted_connections") or []
    if isinstance(unrouted_raw, list):
        for u in unrouted_raw:
            if not isinstance(u, dict):
                continue
            diag = u.get("diagnosis")
            if isinstance(diag, dict):
                diag_class = diag.get("class")
                diag_details = diag
            else:
                diag_class = diag or u.get("class")
                diag_details = u.get("diagnosis_details") or u.get("details")
            uc = UnroutedConnection(
                net=u.get("net") or u.get("net_name"),
                item1=u.get("from"),
                item2=u.get("to"),
                start={"xy": u.get("from_xy")} if u.get("from_xy") else None,
                end={"xy": u.get("to_xy")} if u.get("to_xy") else None,
                diagnosis=diag_class,
                diagnosis_details=diag_details,
                raw=u,
            )
            r.unrouted_connections.append(uc)
    # clearance violations
    viol_raw = raw.get("clearance_violations") or raw.get("violations") or []
    if isinstance(viol_raw, list):
        for v in viol_raw:
            if not isinstance(v, dict):
                continue
            if not ("clearance_mm" in v or "required" in v or "actual_mm" in v or "unfixable" in v or "xy" in v):
                continue
            cv = ClearanceViolation(
                layer=v.get("layer"),
                location={"xy": v.get("xy")} if v.get("xy") else v.get("location"),
                required_mm=v.get("clearance_mm", v.get("required")),
                actual_mm=v.get("actual_mm", v.get("actual")),
                unfixable=bool(v.get("unfixable", False)),
                items=[v.get("first"), v.get("second")] if v.get("first") or v.get("second") else v.get("items", []) or [],
                raw=v,
            )
            r.clearance_violations.append(cv)
    if r.unrouted_connections and r.unrouted_count == 0:
        r.unrouted_count = len(r.unrouted_connections)
    if r.clearance_violations and r.violations_count == 0:
        r.violations_count = len(r.clearance_violations)
    return r


def load_report_file(path: Path) -> NormalizedReport:
    raw = json.loads(path.read_text())
    return parse_report(raw, str(path))
