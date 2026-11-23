from __future__ import annotations
from pathlib import Path
from ..errors import FastRouteError
from ..service.report_parser import load_report_file


def register(mcp, manager):
    @mcp.tool()
    def get_routing_report(job_id: str) -> dict:
        try:
            job = manager._get_job(job_id)
        except FastRouteError as e:
            return e.to_envelope()
        rp = Path(job.report_path)
        if not rp.exists():
            return FastRouteError("REPORT_MISSING", "Report not yet available", True).to_envelope()
        try:
            rep = load_report_file(rp)
        except Exception as e:
            return FastRouteError("REPORT_PARSE_FAILED", str(e), False).to_envelope()
        return {"ok": True, "data": rep.model_dump(), "warnings": [], "error": None}

    @mcp.tool()
    def get_unrouted_connections(job_id: str, net_names: list[str] | None = None,
                                diagnosis_class: str = "all", limit: int = 500, cursor: int = 0) -> dict:
        try:
            job = manager._get_job(job_id)
        except FastRouteError as e:
            return e.to_envelope()
        rp = Path(job.report_path)
        if not rp.exists():
            return FastRouteError("REPORT_MISSING", "Report not yet available", True).to_envelope()
        try:
            rep = load_report_file(rp)
        except Exception as e:
            return FastRouteError("REPORT_PARSE_FAILED", str(e), False).to_envelope()
        conns = rep.unrouted_connections
        if net_names:
            conns = [c for c in conns if c.net in net_names]
        if diagnosis_class != "all":
            conns = [c for c in conns if (c.diagnosis or "unknown") == diagnosis_class or diagnosis_class == "all"]
        total = len(conns)
        chunk = conns[cursor:cursor+limit]
        return {"ok": True, "data": {
            "connections": [c.model_dump() for c in chunk],
            "total": total,
            "next_cursor": cursor + len(chunk) if cursor + len(chunk) < total else None,
        }, "warnings": ["For nets with more than two pins, individual diagnosis connects an endpoint to the nearest part of its net, not necessarily the displayed airline endpoint."] if any(c.diagnosis for c in chunk) else [], "error": None}

    @mcp.tool()
    def get_clearance_violations(job_id: str, include_unfixable: bool = True) -> dict:
        try:
            job = manager._get_job(job_id)
        except FastRouteError as e:
            return e.to_envelope()
        rp = Path(job.report_path)
        if not rp.exists():
            return FastRouteError("REPORT_MISSING", "Report not yet available", True).to_envelope()
        try:
            rep = load_report_file(rp)
        except Exception as e:
            return FastRouteError("REPORT_PARSE_FAILED", str(e), False).to_envelope()
        viols = rep.clearance_violations
        if not include_unfixable:
            viols = [v for v in viols if not v.unfixable]
        return {"ok": True, "data": {"violations": [v.model_dump() for v in viols], "count": len(viols)}, "warnings": [], "error": None}

    @mcp.tool()
    async def diagnose_unrouted_nets(job_id: str, run_diagnosis_if_missing: bool = False) -> dict:
        try:
            job = manager._get_job(job_id)
        except FastRouteError as e:
            return e.to_envelope()
        rp = Path(job.report_path)
        if rp.exists():
            try:
                rep = load_report_file(rp)
                if any(c.diagnosis for c in rep.unrouted_connections):
                    return {"ok": True, "data": {"diagnosis_present": True, "report": rep.model_dump()}, "warnings": [], "error": None}
            except Exception:
                pass
        if not run_diagnosis_if_missing:
            return {"ok": True, "data": {"diagnosis_present": False, "message": "No diagnosis in report. Set run_diagnosis_if_missing=true to launch a diagnostic rerun (requires approval)."}, "warnings": [], "error": None}
        # launch diagnostic rerun: same config + diagnose=True, initial session? Use resume from job? 
        # We'll start a new job with diagnose=True and same board, no initial session
        try:
            new_job = await manager.start_routing(
                board_path=job.board_path,
                profile=job.profile,
                max_time_seconds=job.effective_config.get("max_time_seconds"),
                diagnose=True,
                live_monitoring=False,
                existing_tracks=job.effective_config.get("existing_tracks", "router_default"),
                extra_options=job.effective_config.get("router_overrides", {}),
            )
        except FastRouteError as e:
            return e.to_envelope()
        return {"ok": True, "data": {"launched_job_id": new_job.job_id, "diagnosis_present": False}, "warnings": ["Diagnostic rerun launched; poll for completion."], "error": None}

    @mcp.tool()
    def compare_routing_runs(job_ids: list[str], metrics: list[str] | None = None) -> dict:
        from ..service.comparison import compare_runs
        runs = []
        for jid in job_ids:
            try:
                job = manager._get_job(jid)
            except FastRouteError as e:
                return e.to_envelope()
            rp = Path(job.report_path)
            rep = None
            if rp.exists():
                try:
                    rep = load_report_file(rp)
                    rep = rep.model_dump()
                except Exception:
                    rep = None
            runs.append({"job_id": jid, "report": rep, "board_sha256": job.board_sha256, "config_hash": job.config_hash})
        result = compare_runs(runs)
        return {"ok": True, "data": result, "warnings": [], "error": None}
