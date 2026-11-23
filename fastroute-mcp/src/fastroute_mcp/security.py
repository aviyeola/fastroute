from __future__ import annotations
from pathlib import Path
from .errors import FastRouteError
from .paths import is_within, normalize_path


def validate_path(path: str, allowed_roots: list[Path], must_exist: bool = True) -> Path:
    try:
        p = normalize_path(path)
    except Exception as e:
        raise FastRouteError("BOARD_INVALID", f"Invalid path: {e}", False)
    if not is_within(p, allowed_roots):
        raise FastRouteError("BOARD_OUTSIDE_ALLOWED_ROOT", f"Path outside allowed roots: {p}", False)
    if must_exist and not p.exists():
        raise FastRouteError("BOARD_NOT_FOUND", f"File not found: {p}", False)
    return p


def safe_filename(name: str) -> str:
    # strip any path components
    return Path(name).name
