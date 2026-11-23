from __future__ import annotations
from pathlib import Path
import hashlib
from ..errors import FastRouteError


def register(mcp, manager):
    @mcp.tool()
    async def validate_routing_result(job_id: str, run_kicad_drc: bool = True, refill_zones: bool = True) -> dict:
        try:
            job = manager._get_job(job_id)
        except FastRouteError as e:
            return e.to_envelope()
        ses = Path(job.ses_path)
        if not ses.exists():
            return FastRouteError("CHECKPOINT_MISSING", "No SES checkpoint", False).to_envelope()
        board_path = Path(job.board_path)
        out_board = Path(job.routed_board_path)
        # import SES into a copy of original board (if kicad available)
        if manager.kicad_python and board_path.suffix.lower() == ".kicad_pcb":
            from ..service.kicad_adapter import import_ses_to_board
            ok, msg = import_ses_to_board(manager.kicad_python, board_path, ses, out_board, refill_zones=refill_zones)
            if not ok:
                return FastRouteError("IMPORT_FAILED", msg, True).to_envelope()
        else:
            # no kicad; just record ses
            pass
        drc_result = None
        if run_kicad_drc and manager.kicad_cli and out_board.exists():
            from ..service.kicad_adapter import run_kicad_drc
            ok, msg = run_kicad_drc(manager.kicad_cli, out_board, Path(job.drc_report_path))
            drc_result = {"ok": ok, "message": msg}
        return {"ok": True, "data": {
            "job_id": job_id,
            "routed_board_path": str(out_board),
            "routed_board_exists": out_board.exists(),
            "drc": drc_result,
        }, "warnings": [] if manager.kicad_python else ["KiCad not available; routed board not generated."], "error": None}

    @mcp.tool()
    def export_routing_artifacts(job_id: str, artifacts: list[str]) -> dict:
        try:
            job = manager._get_job(job_id)
        except FastRouteError as e:
            return e.to_envelope()
        ws = Path(job.workspace)
        mapping = {
            "ses": Path(job.ses_path),
            "report": Path(job.report_path),
            "routed_board": Path(job.routed_board_path),
            "drc_report": Path(job.drc_report_path),
            "logs": ws / "logs",
            "manifest": ws / "manifest.json",
        }
        out = []
        for a in artifacts:
            p = mapping.get(a)
            if p is None:
                return FastRouteError("ARTIFACT_NOT_FOUND", f"Unknown artifact {a}", False).to_envelope()
            if not p.exists():
                return FastRouteError("ARTIFACT_NOT_FOUND", f"Artifact missing: {p}", False).to_envelope()
            if p.is_file():
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                size = p.stat().st_size
            else:
                h = None
                size = None
            out.append({"type": a, "path": str(p), "sha256": h, "size": size})
        return {"ok": True, "data": {"artifacts": out}, "warnings": [], "error": None}

    @mcp.tool()
    def import_routing_result(job_id: str, output_board_path: str, approval_token: str | None = None) -> dict:
        # High-impact: require approval token
        if not approval_token:
            return FastRouteError("APPROVAL_REQUIRED", "approval_token required", False).to_envelope()
        try:
            job = manager._get_job(job_id)
        except FastRouteError as e:
            return e.to_envelope()
        roots = manager.allowed_roots()
        from ..security import validate_path
        try:
            outp = validate_path(output_board_path, roots, must_exist=False)
        except FastRouteError as e:
            return e.to_envelope()
        # ensure not overwriting source board
        if outp.resolve() == Path(job.board_path).resolve():
            return FastRouteError("BOARD_INVALID", "Cannot overwrite source board", False).to_envelope()
        # copy routed board if exists
        src = Path(job.routed_board_path)
        if not src.exists():
            return FastRouteError("ARTIFACT_NOT_FOUND", "Routed board not available; run validate_routing_result first", False).to_envelope()
        import shutil
        outp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, outp)
        h = hashlib.sha256(outp.read_bytes()).hexdigest()
        return {"ok": True, "data": {"output_board_path": str(outp), "sha256": h}, "warnings": [], "error": None}
