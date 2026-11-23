from __future__ import annotations
import json
from pathlib import Path


def register(mcp, manager):
    @mcp.resource("fastroute://capabilities")
    def capabilities() -> str:
        import platform
        return json.dumps({
            "fastroute_binary": str(manager.binary) if manager.binary else None,
            "fastroute_version": manager.fastroute_ver,
            "kicad_python": manager.kicad_python,
            "kicad_cli": manager.kicad_cli,
            "kicad_version": manager.kicad_ver,
            "workspace_root": str(manager.settings.workspace_root),
            "platform": platform.platform(),
        }, indent=2)

    @mcp.resource("fastroute://profiles")
    def profiles() -> str:
        from ..service.profile_manager import PROFILES
        return json.dumps(PROFILES, indent=2)

    @mcp.resource("fastroute://jobs")
    def jobs() -> str:
        res = manager.list_jobs(limit=50, cursor=0)
        return json.dumps(res, indent=2)

    @mcp.resource("fastroute://jobs/{job_id}/status")
    def job_status(job_id: str) -> str:
        try:
            st = manager.get_status(job_id)
        except Exception as e:
            return json.dumps({"error": str(e)})
        return json.dumps(st, indent=2)

    @mcp.resource("fastroute://jobs/{job_id}/report")
    def job_report(job_id: str) -> str:
        try:
            job = manager._get_job(job_id)
        except Exception as e:
            return json.dumps({"error": str(e)})
        rp = Path(job.report_path)
        if not rp.exists():
            return json.dumps({"error": "report not available"})
        return rp.read_text()

    @mcp.resource("fastroute://jobs/{job_id}/manifest")
    def job_manifest(job_id: str) -> str:
        try:
            job = manager._get_job(job_id)
        except Exception as e:
            return json.dumps({"error": str(e)})
        ws = Path(job.workspace)
        mp = ws / "manifest.json"
        if mp.exists():
            return mp.read_text()
        return json.dumps(job.model_dump(), indent=2, default=str)
