from __future__ import annotations
import os
from pathlib import Path


def default_workspace_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "FastRouteMCP" / "jobs"
    # linux/mac
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "fastroute-mcp" / "jobs"


def normalize_path(p: str | Path) -> Path:
    return Path(p).expanduser().resolve()


def is_within(path: Path, roots: list[Path]) -> bool:
    rp = path.resolve()
    for r in roots:
        try:
            rp.relative_to(r.resolve())
            return True
        except ValueError:
            continue
    return False
