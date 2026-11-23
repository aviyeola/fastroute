from __future__ import annotations
import asyncio
from ..errors import FastRouteError


def register(mcp, manager):
    @mcp.tool()
    def get_routing_profiles() -> dict:
        from ..service.profile_manager import PROFILES
        data = []
        for name, cfg in PROFILES.items():
            data.append({"name": name, "description": cfg.get("description"), "parameters": {k: v for k, v in cfg.items() if k != "description"}})
        return {"ok": True, "data": {"profiles": data, "schema_version": 1}, "warnings": [], "error": None}

    @mcp.tool()
    def validate_routing_config(profile: str, overrides: dict | None = None) -> dict:
        from ..service.profile_manager import validate_config
        try:
            cfg, rejected, warnings = validate_config(profile, overrides or {})
        except ValueError as e:
            return FastRouteError("CONFIG_REJECTED", str(e), False).to_envelope()
        requires_approval = False
        return {"ok": True, "data": {
            "effective_config": cfg,
            "rejected": rejected,
            "warnings": warnings,
            "requires_approval": requires_approval,
        }, "warnings": warnings, "error": None}

    @mcp.tool()
    def apply_routing_profile(profile: str, overrides: dict | None = None) -> dict:
        from ..service.profile_manager import validate_config
        try:
            cfg, rejected, warnings = validate_config(profile, overrides or {})
        except ValueError as e:
            return FastRouteError("CONFIG_REJECTED", str(e), False).to_envelope()
        if rejected:
            return {"ok": False, "data": {"effective_config": cfg, "rejected": rejected, "warnings": warnings},
                    "warnings": warnings, "error": {"code": "CONFIG_REJECTED", "message": "; ".join(rejected), "retryable": False, "details": {}}}
        return {"ok": True, "data": {"effective_config": cfg, "rejected": [], "warnings": warnings}, "warnings": warnings, "error": None}

    @mcp.tool()
    def get_routing_config(job_id: str | None = None, profile: str | None = None) -> dict:
        if job_id:
            try:
                job = manager._get_job(job_id)
            except FastRouteError as e:
                return e.to_envelope()
            return {"ok": True, "data": {
                "effective_config": job.effective_config,
                "argv": job.argv,
                "fastroute_version": job.fastroute_version,
                "config_hash": job.config_hash,
            }, "warnings": [], "error": None}
        if profile:
            from ..service.profile_manager import validate_config
            try:
                cfg, rejected, warnings = validate_config(profile, {})
            except ValueError as e:
                return FastRouteError("CONFIG_REJECTED", str(e), False).to_envelope()
            return {"ok": True, "data": {"effective_config": cfg, "rejected": rejected, "warnings": warnings}, "warnings": warnings, "error": None}
        return FastRouteError("JOB_NOT_FOUND", "Provide job_id or profile", False).to_envelope()

    @mcp.tool()
    async def start_routing(board_path: str, profile: str = "balanced",
                            max_time_seconds: int | None = None,
                            max_passes: int | None = None,
                            threads: int | None = None,
                            multi_start: int | None = None,
                            diagnose: bool = False,
                            live_monitoring: bool = True,
                            existing_tracks: str = "router_default",
                            extra_options: dict | None = None) -> dict:
        try:
            job = await manager.start_routing(
                board_path=board_path, profile=profile,
                max_time_seconds=max_time_seconds, max_passes=max_passes,
                threads=threads, multi_start=multi_start, diagnose=diagnose,
                live_monitoring=live_monitoring, existing_tracks=existing_tracks,
                extra_options=extra_options or {})
        except FastRouteError as e:
            return e.to_envelope()
        except Exception as e:
            return FastRouteError("PROCESS_START_FAILED", str(e), True).to_envelope()
        return {"ok": True, "data": {
            "job_id": job.job_id,
            "state": job.state.value,
            "board_sha256": job.board_sha256,
            "effective_profile": job.profile,
            "workspace": job.workspace,
            "warnings": [],
        }, "warnings": [], "error": None}

    @mcp.tool()
    async def stop_routing(job_id: str, force: bool = False, grace_seconds: int = 60) -> dict:
        try:
            res = await manager.stop_routing(job_id, force=force, grace_seconds=grace_seconds)
        except FastRouteError as e:
            return e.to_envelope()
        job = manager._get_job(job_id)
        return {"ok": True, "data": {
            "job_id": job_id,
            "state": job.state.value,
            "signal_sent": res.get("signal_sent", False),
            "process_state": res.get("process_state"),
            "checkpoint_exists": res.get("checkpoint_exists", False),
            "grace_timeout": res.get("grace_timeout", False),
        }, "warnings": [], "error": None}

    @mcp.tool()
    async def resume_routing(source_job_id: str, profile: str | None = None,
                            max_time_seconds: int | None = None,
                            multi_start: int | None = None,
                            diagnose: bool | None = None,
                            approved_board_hash: str | None = None) -> dict:
        try:
            job = await manager.resume_routing(source_job_id, profile=profile,
                                            max_time_seconds=max_time_seconds,
                                            multi_start=multi_start, diagnose=diagnose,
                                            approved_board_hash=approved_board_hash)
        except FastRouteError as e:
            return e.to_envelope()
        except Exception as e:
            return FastRouteError("PROCESS_START_FAILED", str(e), True).to_envelope()
        src = manager._get_job(source_job_id)
        return {"ok": True, "data": {
            "job_id": job.job_id,
            "parent_job_id": job.parent_job_id,
            "source_ses_path": src.ses_path,
            "board_sha256": job.board_sha256,
            "warnings": [],
        }, "warnings": [], "error": None}

    @mcp.tool()
    def get_routing_status(job_id: str) -> dict:
        try:
            status = manager.get_status(job_id)
        except FastRouteError as e:
            return e.to_envelope()
        return {"ok": True, "data": status, "warnings": [], "error": None}

    @mcp.tool()
    def get_routing_logs(job_id: str, stream: str = "combined", after_line: int = 0, limit: int = 500) -> dict:
        try:
            res = manager.get_logs(job_id, stream=stream, after_line=after_line, limit=limit)
        except FastRouteError as e:
            return e.to_envelope()
        return {"ok": True, "data": res, "warnings": [], "error": None}

    @mcp.tool()
    def get_routing_events(job_id: str, after_event_id: int = 0, limit: int = 500) -> dict:
        try:
            res = manager.get_events(job_id, after_event_id=after_event_id, limit=limit)
        except FastRouteError as e:
            return e.to_envelope()
        return {"ok": True, "data": res, "warnings": [], "error": None}

    @mcp.tool()
    def list_routing_jobs(board_sha256: str | None = None, state: list[str] | None = None, limit: int = 50, cursor: int = 0) -> dict:
        try:
            res = manager.list_jobs(board_sha256=board_sha256, states=state, limit=limit, cursor=cursor)
        except FastRouteError as e:
            return e.to_envelope()
        return {"ok": True, "data": res, "warnings": [], "error": None}
