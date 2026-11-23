from __future__ import annotations
from pathlib import Path
from ..errors import FastRouteError


def register(mcp, manager):
    @mcp.tool()
    def inspect_board(board_path: str, include: list[str] | None = None, max_items: int = 500) -> dict:
        """Inspect a KiCad board or DSN. Read-only."""
        try:
            roots = manager.allowed_roots()
            from ..security import validate_path
            bp = validate_path(board_path, roots, must_exist=True)
        except FastRouteError as e:
            return e.to_envelope()
        if bp.suffix.lower() == ".dsn":
            text = bp.read_text(errors="replace")
            import hashlib
            summary = {
                "board_id": "sha256:" + hashlib.sha256(bp.read_bytes()).hexdigest(),
                "format": "dsn",
                "kicad_version": None,
                "summary": {
                    "components": text.count("(component"),
                    "nets": text.count("(net "),
                },
                "warnings": ["Inspection is DSN-derived; KiCad-only properties omitted."],
            }
            return {"ok": True, "data": summary, "warnings": summary["warnings"], "error": None}
        if not manager.kicad_python:
            return FastRouteError("KICAD_NOT_FOUND", "KiCad Python (pcbnew) not available", True).to_envelope()
        return {"ok": True, "data": {"board_path": str(bp), "format": "kicad_pcb", "note": "KiCad inspection adapter placeholder."}, "warnings": [], "error": None}

    @mcp.tool()
    def get_board_rules(board_path: str) -> dict:
        try:
            roots = manager.allowed_roots()
            from ..security import validate_path
            bp = validate_path(board_path, roots, must_exist=True)
        except FastRouteError as e:
            return e.to_envelope()
        if not manager.kicad_python:
            return FastRouteError("KICAD_NOT_FOUND", "KiCad Python not available", True).to_envelope()
        data = {
            "board_path": str(bp),
            "source": "unknown",
            "rules": [],
            "warnings": ["Full rule extraction requires KiCad bridge; not implemented in this build."],
        }
        return {"ok": True, "data": data, "warnings": data["warnings"], "error": None}

    @mcp.tool()
    def get_net_details(board_path: str, net_names: list[str] | None = None, include_endpoints: bool = True) -> dict:
        return {"ok": True, "data": {"nets": [], "warnings": ["Net detail extraction requires KiCad bridge."]},
                "warnings": ["Net detail extraction requires KiCad bridge."], "error": None}

    @mcp.tool()
    def get_differential_pairs(board_path: str) -> dict:
        return {"ok": True, "data": {"pairs": [], "warnings": ["Differential pair extraction requires KiCad bridge."]},
                "warnings": ["Differential pair extraction requires KiCad bridge."], "error": None}
